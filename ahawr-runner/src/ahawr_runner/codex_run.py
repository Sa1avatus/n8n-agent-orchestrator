"""Codex engine runs (providers with ENGINE=codex): the CLI process, its event stream, the Codex
thread id kept as the session's resume id, and the run result.

``execute`` replaces the Claude Code process in ``RunManager._execute``. The process registry,
timeout, cancel and watchdog handling are the manager's own, as for Claude runs.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import shutil
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .claude_cli import Outcome
from .codex_auth import stage_auth, sync_auth_back
from .codex_cli import build_command, build_resume_command, child_env
from .codex_config import render_config_toml, write_codex_home
from .codex_cost import estimate_cost_usd
from .codex_events import OTHER, CodexEventMapper, classify_failure
from .config import ProviderProfile
from .store import Store

if TYPE_CHECKING:
    from .runs import RunManager

CODEX_BIN = "codex"
SESSION_DIR_NAME = "codex-sessions"
STDERR_TAIL_CHARS = 8000
STDERR_DETAIL_CHARS = 2000
STREAM_LIMIT_BYTES = 64 * 1024 * 1024


def bind(store: Store, session_id: str, cwd: str, role: str, fresh: bool) -> tuple[str, bool, bool]:
    """(id the run continues, resume?, created?) for a runner session on the Codex engine.

    The session row holds the runner session id until a run captures the Codex thread id; only
    a captured thread id is resumed, and a fresh retry never resumes.
    """
    if not session_id:
        session_id = str(uuid.uuid4())
        store.bind_session(session_id, session_id, cwd, role)
        return session_id, False, True
    known = store.get_session(session_id)
    if known is not None and not fresh and known["claude_session_id"] != session_id:
        return known["claude_session_id"], True, False
    store.bind_session(session_id, session_id, cwd, role)
    return session_id, False, known is None


def session_dir(data_dir: Path, session_id: str) -> Path:
    # hashed: the session id comes from the request body and must not shape a path
    digest = hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:32]
    return data_dir / SESSION_DIR_NAME / digest


async def execute(
    manager: RunManager,
    run_id: str,
    prompt: str,
    model: str,
    role: str,
    thread_id: str,
    resume: bool,
    cwd: str,
    provider: ProviderProfile,
) -> None:
    record = manager.store.get_run(run_id) or {}
    session_id = str(record.get("session_id", ""))
    # CODEX_HOME is kept across the session's runs: Codex keeps the resumable session under it
    home_root = session_dir(manager.settings.data_dir, session_id)
    last_path = home_root / f"{run_id}.last.txt"
    home = write_codex_home(home_root, render_config_toml(provider, role, model, cwd, None))
    if provider.codex_auth == "chatgpt" and not stage_auth(provider.codex_login_home, home):
        manager._finish(
            run_id,
            Outcome(
                "failed",
                error_code="codex_not_logged_in",
                error_message="No usable Codex login: run codex login with CODEX_HOME="
                + provider.codex_login_home,
                http_code=503,
            ),
            None,
        )
        return
    if resume:
        cmd = build_resume_command(
            CODEX_BIN,
            session_id=thread_id,
            role=role,
            last_message_path=str(last_path),
            prompt=prompt,
        )
    else:
        cmd = build_command(
            CODEX_BIN,
            workspace=cwd,
            role=role,
            last_message_path=str(last_path),
            prompt=prompt,
        )
    env = child_env(provider, home)
    if role == "qa":
        manager._qa_runs.add(run_id)
    try:
        proc = await _spawn(manager, cmd, cwd, env)
    except OSError as exc:
        manager._finish(
            run_id,
            Outcome(
                "failed",
                error_code="cli_unavailable",
                error_message=f"Cannot start Codex CLI: {exc}",
                http_code=503,
            ),
            None,
        )
        return
    manager._procs[run_id] = proc
    manager._proc_started_at[proc] = time.monotonic()
    mapper = CodexEventMapper()
    stderr_chunks: list[bytes] = []
    timed_out = False
    pump_error: Exception | None = None
    try:
        timed_out = await _read_to_end(manager, run_id, proc, mapper, stderr_chunks)
    except Exception as exc:  # stream failure must still finish the run record
        pump_error = exc
        await manager._terminate_proc(run_id)
    finally:
        await asyncio.shield(manager._terminate_proc(run_id))
    if provider.codex_auth == "chatgpt":
        sync_auth_back(provider.codex_login_home, home)
    stderr = b"".join(stderr_chunks).decode("utf-8", "replace")[-STDERR_TAIL_CHARS:]
    outcome = _outcome(
        manager,
        run_id,
        mapper,
        last_path,
        model,
        provider,
        proc.returncode,
        stderr,
        timed_out=timed_out,
        pump_error=pump_error,
    )
    if mapper.session_id:
        manager.store.bind_session(session_id, mapper.session_id, cwd, role)
    manager._finish(run_id, outcome, None)


async def _spawn(
    manager: RunManager, cmd: list[str], cwd: str, env: dict[str, str]
) -> asyncio.subprocess.Process:
    cmd_to_exec = manager._limited(cmd)
    # with prlimit in front, a missing binary would fail inside prlimit (exit 127) instead of here
    if cmd_to_exec[0] != cmd[0] and shutil.which(cmd[0], path=env.get("PATH")) is None:
        raise OSError(f"[Errno 2] No such file or directory: {cmd[0]!r}")
    return await asyncio.create_subprocess_exec(
        *cmd_to_exec,
        cwd=cwd,
        env=env,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        limit=STREAM_LIMIT_BYTES,
        start_new_session=True,
    )


async def _read_to_end(
    manager: RunManager,
    run_id: str,
    proc: asyncio.subprocess.Process,
    mapper: CodexEventMapper,
    stderr_chunks: list[bytes],
) -> bool:
    """Read the process to its end; True when the run timeout stopped it."""

    async def pump_stdout() -> None:
        assert proc.stdout
        async for raw in proc.stdout:
            entries = mapper.feed(raw.decode("utf-8", "replace"))
            if entries:
                manager._last_event_at[run_id] = time.monotonic()
            for entry in entries:
                manager.events.add(run_id, entry)

    async def pump_stderr() -> None:
        assert proc.stderr
        async for raw in proc.stderr:
            stderr_chunks.append(raw)
            while sum(map(len, stderr_chunks)) > STDERR_TAIL_CHARS and len(stderr_chunks) > 1:
                stderr_chunks.pop(0)

    pumps = asyncio.gather(pump_stdout(), pump_stderr(), proc.wait())
    try:
        await asyncio.wait_for(asyncio.shield(pumps), manager.settings.max_run_seconds)
    except TimeoutError:
        await manager._terminate_proc(run_id)
        with contextlib.suppress(Exception):
            await asyncio.wait_for(pumps, manager.settings.interrupt_grace_seconds + 5)
        return True
    return False


def _outcome(
    manager: RunManager,
    run_id: str,
    mapper: CodexEventMapper,
    last_path: Path,
    model: str,
    provider: ProviderProfile,
    returncode: int | None,
    stderr: str,
    *,
    timed_out: bool,
    pump_error: Exception | None,
) -> Outcome:
    output = _take_last_message(last_path) or mapper.final_message
    details: dict[str, Any] = {"exit_code": returncode, "num_turns": mapper.step_count}
    if stderr.strip():
        details["stderr_tail"] = stderr[-STDERR_DETAIL_CHARS:]
    details["total_cost_usd"] = _cumulative_cost(manager, run_id, mapper, model, provider)
    if run_id in manager._cancelled:
        outcome = Outcome("cancelled", output, "cancelled", "Run was cancelled.", None, details)
    elif timed_out:
        message = f"Codex run timed out after {manager.settings.max_run_seconds} s."
        outcome = Outcome("failed", output, "timeout", message, 408, details)
    else:
        outcome = _result(output, mapper, returncode, pump_error, details)
    watchdog_code = manager._watchdog_codes.pop(run_id, None)
    if watchdog_code is not None:
        outcome.status = "failed"
        outcome.error_code = watchdog_code
        outcome.error_message = f"Codex run stopped by the runner watchdog ({watchdog_code})."
    return outcome


def _result(
    output: str,
    mapper: CodexEventMapper,
    returncode: int | None,
    pump_error: Exception | None,
    details: dict[str, Any],
) -> Outcome:
    failure = classify_failure(mapper.turn_failed, returncode)
    if failure is None and pump_error is None:
        return Outcome("completed", output, details=details)
    failure_class, code = failure or (OTHER, "cli_error")
    details["failure_class"] = failure_class
    error = (mapper.turn_failed or {}).get("error")
    message = str(error.get("message") or "") if isinstance(error, dict) else ""
    message = message or f"Codex CLI stopped with exit code {returncode}."
    if pump_error is not None:
        code, message = "cli_error", f"Reading Codex output failed: {pump_error!r}"
    return Outcome("failed", output, code, message, None, details)


def _cumulative_cost(
    manager: RunManager,
    run_id: str,
    mapper: CodexEventMapper,
    model: str,
    provider: ProviderProfile,
) -> float | None:
    """Session-cumulative cost, as Claude Code reports it: ``_finish`` takes this run's share."""
    prices = provider.price_usd_per_million_tokens
    if prices is None:
        return None
    cost = estimate_cost_usd(model, mapper.usage, {model: prices})
    if cost is None:
        return None
    record = manager.store.get_run(run_id) or {}
    previous = manager.store.session_cost_before(str(record.get("claude_session_id", "")), run_id)
    return (previous or 0.0) + cost


def _take_last_message(path: Path) -> str:
    """The text of the ``-o`` file; the file is run temp data, removed once read."""
    text = path.read_text(encoding="utf-8").strip() if path.exists() else ""
    path.unlink(missing_ok=True)
    return text
