"""Asynchronous run execution: queueing, CLI processes, timeouts, cancellation, compaction."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import re
import shutil
import signal
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import cloud_think, store
from .claude_cli import (
    UUID_RE,
    Outcome,
    StreamState,
    build_command,
    child_env,
    classify,
    transcript_exists,
)
from .config import ROLES, ProviderProfile, Settings, map_host_path
from .context_probe import probe_context, resume_compact_threshold, window_env
from .events import EventLog
from .git_snapshot import take_snapshot
from .local_pricing import LocalPricing
from .store import Store, now

log = logging.getLogger("claude_runner.runs")

STDERR_TAIL = 8000

# A fresh-session retry input carries the task, the review findings, the previous report
# and the previous session's digest (newest last). Cut only the digest if it alone would
# break the budget; keep the three fixed parts whole otherwise, newest last.
_RETRY_MARK = "[previous session's digest trimmed to the retry budget]"
_RETRY_KEEP_PARTS_CHARS = 4000


def cut_retry_input(prompt: str, budget: int) -> str:
    """Cut the retry input to ``budget`` characters, newest last."""
    budget = max(1, budget)
    if len(prompt) <= budget:
        return prompt
    parts = prompt.split("\n\n")
    digest = parts[-1] if parts else ""
    fixed = "\n\n".join(parts[:-1]) if len(parts) > 1 else ""
    if len(fixed) + 4 + len(_RETRY_MARK) > budget:
        # the fixed parts alone exceed the budget: cut the whole input
        return prompt[:budget]
    digest_chars = max(0, budget - 4 - len(fixed) - len(_RETRY_MARK))
    if digest_chars >= len(digest):
        # fits exactly: no marker
        return prompt
    return f"{fixed}\n\n{digest[:digest_chars]}\n\n{_RETRY_MARK}"


def run_cost(total: Any, previous: float | None) -> float | None:
    """This run's share of a cumulative session counter (cost, API time). Claude Code reports
    the session's totals and restores them on --resume, so a resumed run's share is the
    difference to the previous run's total."""
    if not isinstance(total, int | float):
        return None
    if previous is None or previous > total:  # new session, or Claude Code did not restore it
        return float(total)
    return round(float(total) - previous, 6)


def _group_alive(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    return True


QA_SUMMARY_CHARS = 500
WATCHDOG_MESSAGES = {
    "child_hung": "The Claude Code process used more CPU without any progress; it was stopped.",
    "orphan": "The Claude Code process lost its runner parent; it was stopped.",
    "qa_timeout": "The QA run exceeded its time limit; it was stopped.",
}


@dataclass(frozen=True)
class ProcSample:
    pid: int
    ppid: int
    cpu_seconds: float
    pgrp: int = 0


def read_sample(pid: int, proc_root: Path = Path("/proc")) -> ProcSample | None:
    try:
        text = (proc_root / str(pid) / "stat").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    # comm can hold spaces and parentheses: the fields start after the last ')'
    fields = text[text.rfind(")") + 2 :].split()
    ticks = os.sysconf("SC_CLK_TCK")
    return ProcSample(
        pid,
        int(fields[1]),
        (int(fields[11]) + int(fields[12])) / ticks,
        int(fields[2]),
    )


def group_sample(pid: int, proc_root: Path = Path("/proc")) -> ProcSample | None:
    """The run's CLI process plus every process in its session (group) or descendants:
    its own ppid, kept for the orphan check, and the summed utime+stime of every member
    whose pgrp is the leader's pid, so a hung tool child is caught, not just the CLI."""
    sample = read_sample(pid, proc_root)
    if sample is None:
        return None
    total = 0.0
    for entry in proc_root.iterdir():
        if not entry.is_dir():
            continue
        member = read_sample(entry.name, proc_root)
        if member is not None and member.pgrp == pid:
            total += member.cpu_seconds
    return ProcSample(pid, sample.ppid, total, sample.pgrp)


def watchdog_verdict(
    sample: ProcSample,
    cpu_at_last_event: float,
    cpu_limit: float,
    runner_pid: int,
    cpu_watch: bool = True,
) -> str | None:
    """Why the run's CLI process must be stopped, or None. ``cpu_at_last_event`` is the
    process's CPU time when the run last produced an event, so the difference is the CPU
    it used since then. ``cpu_watch=False`` skips the CPU check (QA runs, whose test
    suites legitimately burn CPU between events)."""
    if sample.ppid != runner_pid:
        return "orphan"
    if cpu_watch and sample.cpu_seconds - cpu_at_last_event > cpu_limit:
        return "child_hung"
    return None


class RunnerError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code


def task_number(prompt: str) -> int:
    """The AHAWR task number in a Worker prompt (``TASK T010`` -> 10), 0 when
    there is none. In a first-attempt prompt it stands after several thousand
    characters of rules, so the whole text is searched."""
    found = re.search(r"TASK T0*(\d+)[A-Z]*", prompt or "")
    return int(found.group(1)) if found else 0


@dataclass
class StartRequest:
    input: str
    model: str = ""
    provider: str = ""
    session_id: str = ""
    working_directory: str = ""
    role: str = ""
    think_policy: str = ""
    think_policy_from_task: int = 0
    think_off_after_seconds: int = -1
    think_off_after_steps: int = -1


class RunManager:
    def __init__(self, settings: Settings, store: Store | None = None) -> None:
        self.settings = settings
        # providers that exist only in the board database (set by the app when providers_db is on)
        self.extra_provider: Callable[[str], ProviderProfile | None] | None = None
        self.store = store or Store(settings.data_dir / "runner.sqlite")
        # Owner token for the run rows this manager starts: f"{pid}:{boot_id}"
        # (boot_id is a uuid4 per manager instance). A row carries it as
        # ``owner_pid``; ``interrupt_active_runs`` skips it only while the token
        # is in the store's live registry, so a second manager in the same
        # process (same pid, different boot id) cannot mark this manager's live
        # rows interrupted, and a row from a dead manager is interrupted even
        # when the pid was reused.
        self.owner_pid = f"{os.getpid()}:{uuid.uuid4().hex}"
        self.on_run_start: Callable[[], object] | None = (
            None  # set by the app: think policy (think_switch.py)
        )
        self.think_flag: Callable[[], str] | None = (
            None  # set by the app: 'on' / 'off' now (think_switch.py)
        )
        self.local_pricing = LocalPricing.from_env(settings.local_pricing_env)
        self._slots = asyncio.Semaphore(settings.max_concurrent)
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._procs: dict[str, asyncio.subprocess.Process] = {}
        # Monotonic start of each CLI process (for process listing); kept per process
        # so the age is right even when a run id is reused.
        self._proc_started_at: dict[asyncio.subprocess.Process, float] = {}
        self._cancelled: set[str] = set()
        self._shutting_down = False
        self._session_lock = asyncio.Lock()
        self.events = EventLog(settings.data_dir / "events", settings.event_retention_days)
        self._last_event_at: dict[str, float] = {}
        self._cpu_at_event: dict[str, tuple[float, float]] = {}
        self._watchdog_codes: dict[str, str] = {}
        self._qa_runs: set[str] = set()
        self._watchdog_task: asyncio.Task[None] | None = None

    async def startup(self) -> int:
        # Register this manager's owner token before rewriting orphan rows: a
        # second manager in the same process then protects its own live rows
        # from the first manager's ``interrupt_active_runs``.
        store._LIVE_OWNER_TOKENS.add(self.owner_pid)
        if Path("/proc").is_dir():  # the watchdog reads /proc: Linux only
            self._watchdog_task = asyncio.create_task(self._watchdog_loop())
        return self.store.interrupt_active_runs(
            "Run was interrupted by a claude-runner restart; resume the session to continue."
        )

    async def shutdown(self) -> None:
        # Runs stopped here stay resumable: they are reported as run_not_found, which makes
        # the Run Manager continue the saved Claude Code session after the restart.
        self._shutting_down = True
        if self._watchdog_task is not None:
            self._watchdog_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._watchdog_task
        for run_id in list(self._tasks):
            await self.cancel(run_id)
        for task in list(self._tasks.values()):
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await asyncio.wait_for(task, self.settings.interrupt_grace_seconds + 5)
        store._LIVE_OWNER_TOKENS.discard(self.owner_pid)
        self.store.close()

    # --------------------------------------------------------------------------- helpers
    def resolve_cwd(self, requested: str) -> str:
        raw = (requested or "").strip() or self.settings.workspace
        raw = map_host_path(raw, self.settings.path_map)
        path = Path(raw).resolve()
        for root in self.settings.allowed_roots:
            base = Path(root).resolve()
            if path == base or base in path.parents:
                if not path.is_dir():
                    raise RunnerError(
                        "invalid_working_directory", f"Working directory {path} does not exist."
                    )
                return str(path)
        raise RunnerError(
            "invalid_working_directory",
            f"Working directory {raw} is outside CLAUDE_RUNNER_ALLOWED_ROOTS.",
        )

    @staticmethod
    def normalize_role(role: str) -> str:
        role = (role or "").strip().lower()
        return role if role in ROLES else "generic"

    def _provider(self, name: str) -> ProviderProfile | None:
        found = self.settings.provider(name)
        if found is None and name.strip() and self.extra_provider is not None:
            return self.extra_provider(name)
        return found

    def public(self, run: dict[str, Any], **extra: Any) -> dict[str, Any]:
        """Hermes-compatible run view: the Run Manager reads status/output/error/session_id."""
        error: dict[str, Any] | str = ""
        if run["error_code"] or run["error_message"]:
            error = {"code": run["error_code"], "message": run["error_message"]}
        status = "failed" if run["status"] == "interrupted" else run["status"]
        details = run.get("details") or {}
        body: dict[str, Any] = {
            "run_id": run["run_id"],
            "session_id": run["session_id"],
            "claude_session_id": run["claude_session_id"],
            "session_created": bool(run["session_created"]),
            "status": status,
            "role": run["role"],
            "model": run["model"],
            "output": run["output"],
            "error": error,
            "http_code": run["http_code"],
            "cost_usd": run["cost_usd"],
            "api_ms": details.get("run_duration_api_ms"),
            "num_turns": run["num_turns"],
            "context_tokens": run["context_tokens"],
            "permission_denials": details.get("permission_denials", []),
            "created_at": run["created_at"],
            "started_at": run["started_at"],
            "finished_at": run["finished_at"],
        }
        body.update(extra)
        return body

    # ----------------------------------------------------------------------------- runs
    async def start(self, request: StartRequest) -> dict[str, Any]:
        prompt = request.input
        if not prompt.strip():
            raise RunnerError("input_required", "input is required.")
        model = request.model.strip() or self.settings.default_model
        role = self.normalize_role(request.role)
        cwd = self.resolve_cwd(request.working_directory)
        async with self._session_lock:
            session_id = request.session_id.strip()
            if session_id:
                active = self.store.active_run(session_id)
                if active:
                    # One turn per session at a time: a repeated start (n8n retry, recovery)
                    # attaches to the run that is already working on this session.
                    return self.public(active, attached=True)
            provider = self._provider(request.provider.strip())
            fresh_retry = bool(session_id and self.settings.retry_fresh_session_for(provider))
            claude_id, resume, created = self._bind(session_id, cwd, role, fresh=fresh_retry)
            session_id = session_id or claude_id
            if fresh_retry:
                prompt = cut_retry_input(prompt, self.settings.retry_input_chars)
            run_id = "run_" + uuid.uuid4().hex
            self.store.create_run(
                run_id=run_id,
                session_id=session_id,
                claude_session_id=claude_id,
                session_created=int(created),
                role=role,
                model=model,
                provider=request.provider.strip(),
                cwd=cwd,
                input=prompt,
                think_policy=request.think_policy,
                think_from=request.think_policy_from_task,
                think_after=request.think_off_after_seconds,
                think_after_steps=request.think_off_after_steps,
                think_task=task_number(prompt),
                owner_pid=self.owner_pid,
            )
            self.store.supersede_queued(session_id)
            self.store.touch_session(session_id, run_id, None)
        self._launch(run_id, prompt, model, role, claude_id, resume, cwd, request.provider.strip())
        run = self.store.get_run(run_id)
        assert run is not None
        return self.public(run, attached=False)

    def _bind(
        self, session_id: str, cwd: str, role: str, fresh: bool = False
    ) -> tuple[str, bool, bool]:
        """Return (claude_session_id, resume?, created?) for a runner session id.

        With ``fresh`` a retry starts a new Claude Code session instead of resuming: the
        old transcript stays untouched and its digest is carried in the retry input, so no
        compaction of the old session is needed before continuing."""
        if session_id:
            known = self.store.get_session(session_id)
            if fresh:
                claude_id = str(uuid.uuid4())
                self.store.bind_session(session_id, claude_id, cwd, role)
                return claude_id, False, True
            if known and transcript_exists(known["claude_session_id"]):
                return known["claude_session_id"], True, False
            if not known and transcript_exists(session_id):
                self.store.bind_session(session_id, session_id, cwd, role)
                return session_id, True, False
            claude_id = session_id if UUID_RE.match(session_id) and not known else str(uuid.uuid4())
            if transcript_exists(claude_id):  # pragma: no cover - uuid collision
                claude_id = str(uuid.uuid4())
            self.store.bind_session(session_id, claude_id, cwd, role)
            return claude_id, False, True
        claude_id = str(uuid.uuid4())
        self.store.bind_session(claude_id, claude_id, cwd, role)
        return claude_id, False, True

    def _launch(
        self,
        run_id: str,
        prompt: str,
        model: str,
        role: str,
        claude_id: str,
        resume: bool,
        cwd: str,
        provider: str = "",
        compact: bool = False,
    ) -> asyncio.Task[None]:
        self.events.open(run_id)
        self.events.add(run_id, {"kind": "prompt", "text": prompt, "resume": resume})
        task = asyncio.create_task(
            self._execute(run_id, prompt, model, role, claude_id, resume, cwd, provider, compact)
        )
        self._tasks[run_id] = task
        task.add_done_callback(lambda _t: self._tasks.pop(run_id, None))
        return task

    async def _execute(
        self,
        run_id: str,
        prompt: str,
        model: str,
        role: str,
        claude_id: str,
        resume: bool,
        cwd: str,
        provider_name: str,
        compact: bool,
    ) -> None:
        async with self._slots:
            # the check and the switch to running below have no await between them
            self.store.supersede_queued((self.store.get_run(run_id) or {}).get("session_id", ""))
            current = self.store.get_run(run_id) or {}
            if run_id in self._cancelled or current.get("status") != "queued":
                self._finish(
                    run_id,
                    Outcome(
                        "cancelled",
                        error_code=current.get("error_code") or "cancelled",
                        error_message=current.get("error_message") or "Run was cancelled.",
                    ),
                    None,
                )
                return
            self.store.update_run(
                run_id, status="running", started_at=now(), owner_pid=self.owner_pid
            )
            if self.on_run_start is not None:
                with contextlib.suppress(Exception):
                    self.on_run_start()
            if role == "worker" and not compact and self.settings.worker_git_snapshot:
                ref = await asyncio.to_thread(
                    take_snapshot, Path(cwd), run_id, self.settings.git_snapshot_keep
                )
                if ref:
                    self.events.add(run_id, {"kind": "git_snapshot", "ref": ref})
            if self.think_flag is not None and provider_name == "local":
                with contextlib.suppress(Exception):
                    # the thinking mode the first request of the run starts in
                    # (the local price depends on it)
                    self.events.add(
                        run_id, {"kind": "thinking_state", "thinking": self.think_flag()}
                    )
            profile = self.settings.profile(role)
            provider = self._provider(provider_name)
            # Cloud thinking: resolve the per-attempt mode for non-local providers when the
            # setting is on; None when the provider is local, the setting is off, or the
            # policy is empty/invalid. An unconfigured cloud provider is still resolved: the
            # mapping checks the provider name itself, so a container-env Anthropic run gets
            # the variant/mode recorded even without a provider profile. The result's env
            # vars and (empty) args are added to the child env and command below.
            ct_setting: cloud_think.CloudThinkSetting | None = None
            if self.settings.cloud_think and provider_name != "local":
                run = self.store.get_run(run_id)
                if run is not None:
                    session_id = str(run.get("session_id", ""))
                    is_retry = (
                        self.store.last_completed_worker_finish(session_id, run_id) is not None
                    )
                    ct_setting = cloud_think.resolve_cloud_think(
                        run.get("think_policy", ""),
                        int(run.get("think_task", 0) or 0),
                        is_retry,
                        provider_name,
                        self.settings.cloud_think,
                    )
                    if ct_setting is not None:
                        # Record the chosen variant and mode for the event log (the
                        # extraction pipeline derives the per-step think flag from
                        # thinking/redacted_thinking entries, same as the local path) and
                        # in the run record itself, as optional fields absent from local
                        # runs (no variant/mode fields there).
                        self.events.add(
                            run_id,
                            {
                                "kind": "thinking_state",
                                "thinking": "on" if ct_setting.mode == "think" else "off",
                                "variant": ct_setting.variant,
                                "retry": is_retry,
                            },
                        )
                        self.store.update_run(
                            run_id, think_variant=ct_setting.variant, think_mode=ct_setting.mode
                        )
            cmd = build_command(
                self.settings,
                profile,
                model=model,
                claude_session_id=claude_id,
                resume=resume,
                compact=compact,
                provider=provider,
                search_first=self.settings.search_first_for(role, provider),
                memory_rule=role == "worker" and self.settings.proc_data_limit_mb > 0,
                cloud_think=ct_setting,
                role=role,
            )
            timeout = (
                self.settings.compact_timeout_seconds if compact else self.settings.max_run_seconds
            )
            env = child_env(
                provider,
                model,
                role=role,
                local_tool_output_limits=self.settings.local_tool_output_limits,
                shared_prefix=self.settings.local_shared_prefix,
                cloud_think=ct_setting,
            )
            if provider is not None and provider.context_probe_url:
                # the window of the model as llama-server runs it now, not a fixed 65536
                window = await asyncio.to_thread(
                    probe_context, provider.context_probe_url, provider.context_probe_key, model
                )
                overrides = window_env(window, provider) if window else None
                if overrides:
                    env.update(overrides)
                self.events.add(
                    run_id,
                    {
                        "kind": "context_window",
                        "source": "probe" if overrides else "static",
                        "probed": window,
                        "window": int(env.get("CLAUDE_CODE_MAX_CONTEXT_TOKENS") or 0) or None,
                        "compact_window": int(env.get("CLAUDE_CODE_AUTO_COMPACT_WINDOW") or 0)
                        or None,
                        "compact_pct": env.get("CLAUDE_AUTOCOMPACT_PCT_OVERRIDE") or None,
                    },
                )
            state = StreamState()
            stderr_chunks: list[bytes] = []
            timed_out = False
            try:
                cmd_to_exec = self._limited(cmd)
                # When prlimit is available it is argv[0], so the missing claude binary
                # fails inside the prlimit child (exit 127) instead of raising OSError
                # here; pre-check the target to keep the cli_unavailable branch working.
                if cmd_to_exec[0] != cmd[0] and shutil.which(
                    cmd[0], path=env.get("PATH")
                ) is None:
                    raise OSError(f"[Errno 2] No such file or directory: {cmd[0]!r}")
                proc = await asyncio.create_subprocess_exec(
                    *cmd_to_exec,
                    cwd=cwd,
                    env=env,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    limit=64 * 1024 * 1024,
                    start_new_session=True,
                )
            except OSError as exc:
                outcome = Outcome(
                    "failed",
                    error_code="cli_unavailable",
                    error_message=f"Cannot start Claude Code CLI: {exc}",
                    http_code=503,
                )
                self._finish(run_id, outcome, None)
                return
            self._procs[run_id] = proc
            if role == "qa":
                self._qa_runs.add(run_id)
            self._proc_started_at[proc] = time.monotonic()
            pump_error: Exception | None = None
            try:
                assert proc.stdin and proc.stdout and proc.stderr
                proc.stdin.write(prompt.encode("utf-8"))
                with contextlib.suppress(BrokenPipeError, ConnectionResetError):
                    await proc.stdin.drain()
                proc.stdin.close()

                async def pump_stdout() -> None:
                    assert proc.stdout
                    async for raw in proc.stdout:
                        event = state.feed(raw.decode("utf-8", "replace"))
                        if event is not None:
                            self._last_event_at[run_id] = time.monotonic()
                            self.events.feed(run_id, event)

                async def pump_stderr() -> None:
                    assert proc.stderr
                    async for raw in proc.stderr:
                        stderr_chunks.append(raw)
                        while sum(map(len, stderr_chunks)) > STDERR_TAIL and len(stderr_chunks) > 1:
                            stderr_chunks.pop(0)

                pumps = asyncio.gather(pump_stdout(), pump_stderr(), proc.wait())
                try:
                    await asyncio.wait_for(asyncio.shield(pumps), timeout)
                except TimeoutError:
                    timed_out = True
                    await self._terminate_proc(run_id)
                    with contextlib.suppress(Exception):
                        await asyncio.wait_for(pumps, self.settings.interrupt_grace_seconds + 5)
            except Exception as exc:  # stream failure must still finish the run record
                pump_error = exc
                await self._terminate_proc(run_id)
            finally:
                # every exit path (completion, failure, timeout, stream error, and
                # cancellation) goes through the helper so leftover tool processes in
                # the group are killed and the entry cannot be left behind
                await asyncio.shield(self._terminate_proc(run_id))
            stderr = b"".join(stderr_chunks).decode("utf-8", "replace")[-STDERR_TAIL:]
            outcome = classify(
                state,
                proc.returncode,
                stderr,
                timed_out=timed_out,
                cancelled=run_id in self._cancelled,
                timeout_seconds=timeout,
            )
            if pump_error is not None and outcome.status == "failed" and not state.result:
                outcome.error_code = "cli_error"
                outcome.error_message = f"Reading Claude Code output failed: {pump_error!r}"
            watchdog_code = self._watchdog_codes.pop(run_id, None)
            if watchdog_code is not None:
                outcome.status = "failed"
                outcome.error_code = watchdog_code
                outcome.error_message = WATCHDOG_MESSAGES[watchdog_code]
            if stderr.strip():
                outcome.details["stderr_tail"] = stderr[-2000:]
            if state.compact_result:
                outcome.details["compact_result"] = state.compact_result
            self._finish(run_id, outcome, state)

    def _limited(self, cmd: list[str]) -> list[str]:
        """The command under ``prlimit --data``: the claude process and everything a Worker
        starts get MemoryError (a traceback they can act on) instead of an OOM kill when they
        allocate more than the limit."""
        limit = self.settings.proc_data_limit_mb
        prlimit = shutil.which("prlimit") if limit > 0 else None
        if prlimit is None:
            return cmd
        return [prlimit, f"--data={limit * 1024 * 1024}", "--", *cmd]

    def _finish(self, run_id: str, outcome: Outcome, state: StreamState | None) -> None:
        self._cancelled.discard(run_id)
        self._qa_runs.discard(run_id)
        if self._shutting_down and outcome.status == "cancelled":
            outcome.status = "interrupted"
            outcome.error_code = "run_not_found"
            outcome.error_message = (
                "Run was interrupted by a claude-runner shutdown; resume the session to continue."
            )
        details = outcome.details
        record = self.store.get_run(run_id) or {}
        claude_session = str(record.get("claude_session_id") or "")
        previous = self.store.session_cost_before(claude_session, run_id)
        api_ms = run_cost(
            details.get("duration_api_ms"),
            self.store.session_total_before(claude_session, run_id, "duration_api_ms"),
        )
        if api_ms is not None:
            details["run_duration_api_ms"] = int(api_ms)
        cost = run_cost(details.get("total_cost_usd"), previous)
        if record.get("provider") == "local" and self.local_pricing.enabled:
            # what the run would have cost on a cloud model: steps made without
            # thinking at the cheaper model's rates, with thinking at the other's
            with contextlib.suppress(Exception):
                local = self.local_pricing.cost_from_events(
                    self.events.directory / f"{run_id}.jsonl"
                )
                if local is not None:
                    details["local_cost"] = local
                    cost = local["cost_usd"]
        self.store.update_run(
            run_id,
            status=outcome.status,
            output=outcome.output,
            error_code=outcome.error_code,
            error_message=outcome.error_message,
            http_code=outcome.http_code,
            cost_usd=cost,
            num_turns=details.get("num_turns"),
            context_tokens=state.final_context_tokens() if state else None,
            details=details,
            finished_at=now(),
        )
        if record.get("role") == "qa":
            self.events.add(
                run_id,
                {
                    "kind": "qa_verdict",
                    "role": "qa",
                    "status": outcome.status,
                    "summary": outcome.output[:QA_SUMMARY_CHARS],
                    "cost_usd": cost,
                },
            )
        self.events.add(
            run_id,
            {
                "kind": "end",
                "status": outcome.status,
                "error_code": outcome.error_code,
                "error": outcome.error_message,
                "stderr_tail": str(details.get("stderr_tail") or "")[-2000:],
            },
        )
        self.events.close(run_id)
        run = self.store.get_run(run_id)
        if run:
            self.store.touch_session(
                run["session_id"], run_id, state.final_context_tokens() if state else None
            )

    async def _stop(self, proc: asyncio.subprocess.Process) -> None:
        """SIGINT ends the turn cleanly (the transcript stays resumable); then SIGTERM and
        SIGKILL go to the whole process group (the CLI runs in its own session), so tool
        processes it started do not outlive it, even when the CLI exited on SIGINT."""
        grace = self.settings.interrupt_grace_seconds
        if proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                proc.send_signal(signal.SIGINT)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(proc.wait(), grace)
        for sig in (signal.SIGTERM, signal.SIGKILL):
            if not _group_alive(proc.pid):
                return
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(proc.pid, sig)
            deadline = time.monotonic() + grace
            while _group_alive(proc.pid) and time.monotonic() < deadline:
                await asyncio.sleep(0.05)

    async def _terminate_proc(self, run_id: str) -> None:
        """Terminate the run's CLI process tree (SIGINT -> SIGTERM -> SIGKILL with grace)
        and remove it from ``_procs``. No-op when no process was started or it has
        already exited."""
        proc = self._procs.get(run_id)
        if proc is None:
            return
        try:
            await self._stop(proc)
        finally:
            self._procs.pop(run_id, None)
            self._proc_started_at.pop(proc, None)
            self._last_event_at.pop(run_id, None)
            self._cpu_at_event.pop(run_id, None)

    def _qa_overdue(self, run_id: str, proc: asyncio.subprocess.Process) -> bool:
        started = self._proc_started_at.get(proc)
        if run_id not in self._qa_runs or started is None:
            return False
        return time.monotonic() - started > self.settings.qa_max_minutes * 60

    def watchdog_scan(self) -> None:
        cpu_limit = self.settings.watchdog_cpu_seconds
        for run_id, proc in list(self._procs.items()):
            sample = group_sample(proc.pid)
            if sample is None:
                continue
            mark = self._last_event_at.get(run_id, 0.0)
            base = self._cpu_at_event.get(run_id)
            if base is None or base[0] != mark:
                base = (mark, sample.cpu_seconds)
                self._cpu_at_event[run_id] = base
            code = watchdog_verdict(
                sample, base[1], cpu_limit, os.getpid(), cpu_watch=run_id not in self._qa_runs
            )
            if code is None and self._qa_overdue(run_id, proc):
                code = "qa_timeout"
            if code is None:
                continue
            log.warning("watchdog stops run %s (pid %s): %s", run_id, proc.pid, code)
            self._watchdog_codes[run_id] = code
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(proc.pid, signal.SIGKILL)

    async def _watchdog_loop(self) -> None:
        while True:
            try:
                self.watchdog_scan()
            except Exception:
                log.exception("watchdog scan failed")
            await asyncio.sleep(self.settings.watchdog_interval_seconds)

    # --------------------------------------------------------------------------- processes
    def list_processes(self) -> list[dict[str, Any]]:
        """The CLI processes this manager started, from its own ``_procs`` registry (not a
        host process list): pid, age in seconds, the run's session id, the run id and the
        run's status. No environment values and no unmasked command line."""
        procs: list[dict[str, Any]] = []
        for run_id, proc in self._procs.items():
            run = self.store.get_run(run_id) or {}
            procs.append(
                {
                    "pid": proc.pid,
                    "age_seconds": round(time.monotonic() - self._proc_started_at.get(
                        proc, 0.0
                    ), 1),
                    "session_id": str(run.get("session_id") or ""),
                    "run_id": run_id,
                    "status": str(run.get("status") or ""),
                }
            )
        return procs

    async def terminate_pid(self, pid: int) -> dict[str, Any]:
        """Terminate one of the manager's own CLI processes by pid. Refused with
        ``not_found`` (not in the registry), ``not_owned`` (a run of a session this
        manager does not own) and ``run_active`` (the run is queued or running);
        ``terminated`` when the process was stopped. Only the run's session id and
        run id are returned: never the environment or an unmasked command line."""
        entry = None
        for run_id, proc in self._procs.items():
            if proc.pid == pid:
                entry = (run_id, proc)
                break
        if entry is None:
            return {"outcome": "not_found", "pid": pid}
        run_id, proc = entry
        run = self.store.get_run(run_id) or {}
        if self.store.get_session(str(run.get("session_id") or "")) is None:
            return {"outcome": "not_owned", "pid": pid, "run_id": run_id}
        if str(run.get("status") or "") in ("queued", "running"):
            return {"outcome": "run_active", "pid": pid, "run_id": run_id, "status": run["status"]}
        await self._terminate_proc(run_id)
        return {"outcome": "terminated", "pid": pid, "run_id": run_id}

    def get(self, run_id: str) -> dict[str, Any]:
        run = self.store.get_run(run_id)
        if not run:
            raise RunnerError("run_not_found", f"Run {run_id} not found.", 404)
        if run["status"] == "interrupted":
            # The Run Manager treats run_not_found as "resume the saved session".
            raise RunnerError("run_not_found", run["error_message"], 404)
        return self.public(run)

    async def cancel(self, run_id: str) -> dict[str, Any]:
        run = self.store.get_run(run_id)
        if not run:
            raise RunnerError("run_not_found", f"Run {run_id} not found.", 404)
        if run["status"] in ("queued", "running"):
            self._cancelled.add(run_id)
            await self._terminate_proc(run_id)
            task = self._tasks.get(run_id)
            if task is not None:
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(asyncio.shield(task), 30)
        run = self.store.get_run(run_id)
        assert run is not None
        return self.public(run)

    # ------------------------------------------------------------------------- compaction
    async def compact(
        self, session_id: str, mode: str = "", min_tokens: int | None = None, model: str = ""
    ) -> dict[str, Any]:
        mode = mode or self.settings.compact_mode
        threshold = self.settings.compact_min_tokens if min_tokens is None else min_tokens
        body: dict[str, Any] = {
            "session_id": session_id,
            "compression_completed": False,
            "compression_failed": False,
        }

        def skipped(reason: str, **extra: Any) -> dict[str, Any]:
            return {
                **body,
                "status": "skipped",
                "compression_status": "skipped",
                "reason": reason,
                **extra,
            }

        if mode == "off":
            return skipped("compaction_disabled")
        async with self._session_lock:
            if self.store.active_run(session_id):
                return skipped("session_busy")
            session = self.store.get_session(session_id)
            if not session or not transcript_exists(session["claude_session_id"]):
                return skipped("session_not_found")
            # Compact with the backend (and model) the session last ran on.
            last = self.store.get_run(session["last_run_id"]) if session["last_run_id"] else None
            provider_name = str((last or {}).get("provider") or "")
            model = model or str((last or {}).get("model") or "")
            provider = self._provider(provider_name)
            if self.settings.retry_fresh_session_for(provider):
                # Fresh-session retry is on: a retry will start a new session, so
                # compacting the old one would be wasted work.
                return skipped("fresh_session_retry")
            if min_tokens is None and provider and provider.compact_min_tokens is not None:
                threshold = provider.compact_min_tokens
            window = None
            if min_tokens is None and provider is not None and provider.context_probe_url:
                # the threshold follows the window llama-server runs the model with now
                window = await asyncio.to_thread(
                    probe_context, provider.context_probe_url, provider.context_probe_key, model
                )
                if window:
                    threshold = resume_compact_threshold(window, provider, threshold)
            tokens = int(session["context_tokens"] or 0)
            if mode == "auto" and tokens < threshold:
                return skipped(
                    "below_threshold", context_tokens=tokens, min_tokens=threshold, window=window
                )
            # Same summary format as auto/manual compaction: the instruction text rides on the
            # /compact command, which Claude Code treats as custom summarization instructions.
            # Only the local provider gets it: other backends keep the stock summarizer.
            if (
                self.settings.local_compact_instructions
                and provider is not None
                and provider.isolates_credentials
            ):
                compact_prompt = "/compact " + self.settings.compact_instructions
            else:
                compact_prompt = "/compact"
            run_id = "cmp_" + uuid.uuid4().hex
            self.store.create_run(
                run_id=run_id,
                kind="compact",
                session_id=session_id,
                claude_session_id=session["claude_session_id"],
                role=session["role"],
                model=model,
                provider=provider_name,
                cwd=session["cwd"],
                input=compact_prompt,
                owner_pid=self.owner_pid,
            )
            task = self._launch(
                run_id,
                compact_prompt,
                model,
                session["role"],
                session["claude_session_id"],
                True,
                session["cwd"],
                provider_name,
                compact=True,
            )
        await asyncio.shield(task)
        run = self.store.get_run(run_id)
        assert run is not None
        details = run["details"]
        compact = details.get("compact") or {}
        ok = run["status"] == "completed" and details.get("compact_result", "success") == "success"
        return {
            **body,
            "status": "completed" if ok else "failed",
            "compression_status": "completed" if ok else "failed",
            "compression_completed": ok,
            "compression_failed": not ok,
            "run_id": run_id,
            "error": "" if ok else (run["error_message"] or "Compaction failed."),
            "pre_tokens": compact.get("pre_tokens"),
            "post_tokens": compact.get("post_tokens"),
            "context_tokens_before": tokens,
        }
