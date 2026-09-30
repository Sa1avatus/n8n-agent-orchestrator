"""Asynchronous run execution: queueing, CLI processes, timeouts, cancellation, compaction."""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .claude_cli import (
    UUID_RE,
    Outcome,
    StreamState,
    build_command,
    child_env,
    classify,
    transcript_exists,
)
from .config import ROLES, Settings
from .context_probe import probe_context, window_env
from .events import EventLog
from .store import Store, now

STDERR_TAIL = 8000


def run_cost(total: Any, previous: float | None) -> float | None:
    """This run's cost. Claude Code reports the session's cumulative cost and restores it on
    --resume, so a resumed run's share is the difference to the previous run's total."""
    if not isinstance(total, int | float):
        return None
    if previous is None or previous > total:  # new session, or Claude Code did not restore it
        return float(total)
    return round(float(total) - previous, 6)


class RunnerError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code


@dataclass
class StartRequest:
    input: str
    model: str = ""
    provider: str = ""
    session_id: str = ""
    working_directory: str = ""
    role: str = ""


class RunManager:
    def __init__(self, settings: Settings, store: Store | None = None) -> None:
        self.settings = settings
        self.store = store or Store(settings.data_dir / "runner.sqlite")
        self._slots = asyncio.Semaphore(settings.max_concurrent)
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._procs: dict[str, asyncio.subprocess.Process] = {}
        self._cancelled: set[str] = set()
        self._shutting_down = False
        self._session_lock = asyncio.Lock()
        self.events = EventLog(settings.data_dir / "events", settings.event_retention_days)

    async def startup(self) -> int:
        return self.store.interrupt_active_runs(
            "Run was interrupted by a claude-runner restart; resume the session to continue."
        )

    async def shutdown(self) -> None:
        # Runs stopped here stay resumable: they are reported as run_not_found, which makes
        # the Run Manager continue the saved Claude Code session after the restart.
        self._shutting_down = True
        for run_id in list(self._tasks):
            await self.cancel(run_id)
        for task in list(self._tasks.values()):
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await asyncio.wait_for(task, self.settings.interrupt_grace_seconds + 5)
        self.store.close()

    # --------------------------------------------------------------------------- helpers
    def resolve_cwd(self, requested: str) -> str:
        raw = (requested or "").strip() or self.settings.workspace
        for source, target in self.settings.path_map:
            norm_raw, norm_src = raw.replace("\\", "/").lower(), source.replace("\\", "/").lower()
            if norm_raw == norm_src or norm_raw.startswith(norm_src.rstrip("/") + "/"):
                rest = raw.replace("\\", "/")[len(norm_src.rstrip("/")) :].lstrip("/")
                raw = str(Path(target) / rest) if rest else target
                break
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
            claude_id, resume, created = self._bind(session_id, cwd, role)
            session_id = session_id or claude_id
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
            )
            self.store.touch_session(session_id, run_id, None)
        self._launch(run_id, prompt, model, role, claude_id, resume, cwd, request.provider.strip())
        run = self.store.get_run(run_id)
        assert run is not None
        return self.public(run, attached=False)

    def _bind(self, session_id: str, cwd: str, role: str) -> tuple[str, bool, bool]:
        """Return (claude_session_id, resume?, created?) for a runner session id."""
        if session_id:
            known = self.store.get_session(session_id)
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
            if run_id in self._cancelled:
                self._finish(
                    run_id,
                    Outcome(
                        "cancelled", error_code="cancelled", error_message="Run was cancelled."
                    ),
                    None,
                )
                return
            self.store.update_run(run_id, status="running", started_at=now())
            profile = self.settings.profile(role)
            provider = self.settings.provider(provider_name)
            cmd = build_command(
                self.settings,
                profile,
                model=model,
                claude_session_id=claude_id,
                resume=resume,
                compact=compact,
                provider=provider,
            )
            timeout = (
                self.settings.compact_timeout_seconds if compact else self.settings.max_run_seconds
            )
            env = child_env(provider, model)
            if provider is not None and provider.context_probe_url:
                # the window of the model as llama-server runs it now, not a fixed 65536
                window = await asyncio.to_thread(
                    probe_context, provider.context_probe_url, provider.context_probe_key, model
                )
                overrides = window_env(window, provider) if window else None
                if overrides:
                    env.update(overrides)
                    env.pop("CLAUDE_AUTOCOMPACT_PCT_OVERRIDE", None)  # a percentage of 65536
                self.events.add(
                    run_id,
                    {
                        "kind": "context_window",
                        "source": "probe" if overrides else "static",
                        "probed": window,
                        "window": int(env.get("CLAUDE_CODE_MAX_CONTEXT_TOKENS") or 0) or None,
                        "compact_window": int(env.get("CLAUDE_CODE_AUTO_COMPACT_WINDOW") or 0)
                        or None,
                    },
                )
            state = StreamState()
            stderr_chunks: list[bytes] = []
            timed_out = False
            try:
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
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
                    await self._stop(proc)
                    with contextlib.suppress(Exception):
                        await asyncio.wait_for(pumps, self.settings.interrupt_grace_seconds + 5)
            except Exception as exc:  # stream failure must still finish the run record
                pump_error = exc
                await self._stop(proc)
            finally:
                self._procs.pop(run_id, None)
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
            if stderr.strip():
                outcome.details["stderr_tail"] = stderr[-2000:]
            if state.compact_result:
                outcome.details["compact_result"] = state.compact_result
            self._finish(run_id, outcome, state)

    def _finish(self, run_id: str, outcome: Outcome, state: StreamState | None) -> None:
        self._cancelled.discard(run_id)
        if self._shutting_down and outcome.status == "cancelled":
            outcome.status = "interrupted"
            outcome.error_code = "run_not_found"
            outcome.error_message = (
                "Run was interrupted by a claude-runner shutdown; resume the session to continue."
            )
        details = outcome.details
        record = self.store.get_run(run_id) or {}
        previous = self.store.session_cost_before(
            str(record.get("claude_session_id") or ""), run_id
        )
        self.store.update_run(
            run_id,
            status=outcome.status,
            output=outcome.output,
            error_code=outcome.error_code,
            error_message=outcome.error_message,
            http_code=outcome.http_code,
            cost_usd=run_cost(details.get("total_cost_usd"), previous),
            num_turns=details.get("num_turns"),
            context_tokens=state.final_context_tokens() if state else None,
            details=details,
            finished_at=now(),
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
        """SIGINT ends the turn cleanly (the transcript stays resumable); then escalate to the
        whole process group (the CLI runs in its own session), so tool processes it started do
        not outlive it."""
        for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGKILL):
            if proc.returncode is not None:
                break
            with contextlib.suppress(ProcessLookupError, PermissionError):
                if sig == signal.SIGINT:
                    proc.send_signal(sig)
                else:
                    os.killpg(proc.pid, sig)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(proc.wait(), self.settings.interrupt_grace_seconds)
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(proc.pid, signal.SIGKILL)  # tools left behind by a CLI that exited

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
            proc = self._procs.get(run_id)
            if proc is not None:
                await self._stop(proc)
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
            provider = self.settings.provider(provider_name)
            if min_tokens is None and provider and provider.compact_min_tokens is not None:
                threshold = provider.compact_min_tokens
            tokens = int(session["context_tokens"] or 0)
            if mode == "auto" and tokens < threshold:
                return skipped("below_threshold", context_tokens=tokens, min_tokens=threshold)
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
