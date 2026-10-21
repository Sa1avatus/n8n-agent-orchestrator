"""Guard framework core.

A guard is a named check with an interval and an action.  ``GuardSupervisor``
runs the registered guards, each in its own asyncio task: a check failure in
one guard increments its error count and backoff delay, and never stops the
other guards.  ``DASHBOARD_GUARDS=1`` turns every per-guard setting on at once;
``DASHBOARD_GUARDS_DRY_RUN=1`` (or ``DASHBOARD_GUARDS_<NAME>_DRY_RUN=1``) records
intended actions and notices without performing or sending them.  A guard with
three errors in a row is disabled and sends one notice.  Notices go to Telegram
through the container bot variables (``DASHBOARD_TELEGRAM_BOT_TOKEN``,
``DASHBOARD_TELEGRAM_CHAT_ID``); their
values are never logged, redacted, or returned.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import urllib.request
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .guards_history import GuardHistory

logger = logging.getLogger("claude_runner.guards")

# Telegram variables of the container (names from T005).  Only their *names*
# are ever logged; their values are never put in logs, state, or results.
TELEGRAM_TOKEN_VAR = "DASHBOARD_TELEGRAM_BOT_TOKEN"
TELEGRAM_CHAT_VAR = "DASHBOARD_TELEGRAM_CHAT_ID"

# Backoff between failed checks of one guard: 60 s doubling per consecutive
# error, capped at one hour.  One failed check never delays another guard.
BACKOFF_BASE_S = 60.0
BACKOFF_CAP_S = 3600.0

# A guard is disabled after this many errors in a row (one notice is sent); one
# successful check resets the streak.
DISABLE_AFTER_ERRORS = 3

# Default interval for a guard without its own setting.
DEFAULT_INTERVAL_S = 60.0

# Notice rate limit: at most one notice per minute across all guards.
NOTICE_LIMIT_PER_MIN = 1.0

# Orphan-process guard: how many consecutive closed-run checks confirm one pid.
ORPHAN_CONFIRM_CHECKS = 2
# Interrupt-loop guard: how many interrupted runs (closed quickly after start)
# count as one window, how long that window is, and how quickly a run must
# have ended to be counted as interrupted-quick.
INTERRUPT_LOOP_THRESHOLD = 3
INTERRUPT_LOOP_WINDOW_S = 900.0
INTERRUPT_QUICK_S = 120.0


def _float(env: Mapping[str, str], name: str, default: float) -> float:
    raw = env.get(name, "")
    try:
        value = float(raw)
    except ValueError:
        value = default
    return max(value, 0.0)


def _int(env: Mapping[str, str], name: str, default: int) -> int:
    raw = env.get(name, "")
    try:
        value = int(raw)
    except ValueError:
        value = default
    return max(value, 0)


def _bool(env: Mapping[str, str], name: str, default: bool) -> bool:
    raw = env.get(name, "")
    if not raw:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _epoch(value: Any) -> float:
    """A run's ``started_at``/``finished_at`` as epoch seconds (0 when missing)."""
    if isinstance(value, int | float):
        return float(value)
    try:
        return datetime.fromisoformat(str(value)).timestamp()
    except ValueError:
        return 0.0


@dataclass
class GuardEntry:
    """Per-guard settings: whether the guard is enabled and its interval."""

    enabled: bool
    interval: float


@dataclass
class GuardSettings:
    """Guard settings read from the environment with safe defaults."""

    master: bool
    dry_run: bool
    dry_run_guards: frozenset[str]
    per_guard: dict[str, GuardEntry]
    notice_limit: float
    orphan_confirm_checks: int
    interrupt_loop_threshold: int
    interrupt_loop_window_s: float
    interrupt_loop_quick_s: float
    queue_state_path: str = ""
    tab: bool = False
    state_dir: str = ""

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> GuardSettings:
        import os

        env = dict(os.environ if env is None else env)
        master = _bool(env, "DASHBOARD_GUARDS", False)
        per_guard: dict[str, GuardEntry] = {}
        dry_run_guards: set[str] = set()
        if master:
            # DASHBOARD_GUARDS=1 turns everything on; per-guard settings are
            # then read from DASHBOARD_GUARDS_<NAME>_ENABLED / _INTERVAL / _DRY_RUN.
            for key in env:
                upper = key.upper()
                prefix = "DASHBOARD_GUARDS_"
                if not upper.startswith(prefix):
                    continue
                if upper.endswith("_ENABLED"):
                    name = (upper[len(prefix) : -len("_ENABLED")]).lower()
                    if name:
                        per_guard[name] = GuardEntry(
                            enabled=_bool(env, key, True),
                            interval=DEFAULT_INTERVAL_S,
                        )
                elif upper.endswith("_INTERVAL"):
                    name = (upper[len(prefix) : -len("_INTERVAL")]).lower()
                    if name:
                        per_guard[name] = GuardEntry(
                            enabled=True,
                            interval=_float(env, key, DEFAULT_INTERVAL_S),
                        )
                elif upper.endswith("_DRY_RUN"):
                    name = (upper[len(prefix) : -len("_DRY_RUN")]).lower()
                    if name and _bool(env, key, False):
                        dry_run_guards.add(name)
        notice_limit = _float(
            env, "DASHBOARD_GUARDS_NOTICE_LIMIT", NOTICE_LIMIT_PER_MIN
        )
        return cls(
            master=master,
            dry_run=_bool(env, "DASHBOARD_GUARDS_DRY_RUN", False),
            dry_run_guards=frozenset(dry_run_guards),
            per_guard=per_guard,
            notice_limit=max(notice_limit, 0.0),
            orphan_confirm_checks=_int(
                env, "DASHBOARD_GUARDS_ORPHAN_CONFIRM_CHECKS", ORPHAN_CONFIRM_CHECKS
            ),
            interrupt_loop_threshold=_int(
                env, "DASHBOARD_GUARDS_INTERRUPT_LOOP_THRESHOLD", INTERRUPT_LOOP_THRESHOLD
            ),
            interrupt_loop_window_s=_float(
                env, "DASHBOARD_GUARDS_INTERRUPT_LOOP_WINDOW_S", INTERRUPT_LOOP_WINDOW_S
            ),
            interrupt_loop_quick_s=_float(
                env, "DASHBOARD_GUARDS_INTERRUPT_LOOP_QUICK_S", INTERRUPT_QUICK_S
            ),
            queue_state_path=env.get("DASHBOARD_GUARDS_QUEUE_STATE_PATH", "").strip(),
            tab=master and _bool(env, "DASHBOARD_GUARDS_TAB", False),
            state_dir=env.get("DASHBOARD_GUARDS_STATE_DIR", "").strip(),
        )

    def enabled(self, name: str) -> bool:
        if not self.master:
            return False
        entry = self.per_guard.get(name.lower())
        return entry.enabled if entry is not None else True

    def interval(self, name: str) -> float:
        entry = self.per_guard.get(name.lower())
        return entry.interval if entry is not None else DEFAULT_INTERVAL_S

    def dry_run_for(self, name: str) -> bool:
        return self.dry_run or name.lower() in self.dry_run_guards


@dataclass
class Finding:
    """One observation of a guard check."""

    detail: str = ""
    severity: str = "warning"


@dataclass
class GuardState:
    """Rolling state of one guard."""

    last_run: float = 0.0
    last_result: str | None = None
    last_action: str | None = None
    error_count: int = 0
    consecutive_errors: int = 0
    status: str = "ok"
    enabled: bool = True

    def snapshot(self) -> dict[str, object]:
        return {
            "last_run": self.last_run,
            "last_result": self.last_result,
            "last_action": self.last_action,
            "error_count": self.error_count,
            "status": self.status,
        }


class Guard:
    """Base class for a guard: a named check with an interval and an action."""

    def __init__(self, name: str, interval: float = DEFAULT_INTERVAL_S) -> None:
        self.name = name
        self.interval = max(interval, 0.0)
        self.state = GuardState()

    async def check(self) -> list[Finding]:
        """Run the check; return findings (an empty list means no findings)."""
        return []

    async def act(self, findings: list[Finding]) -> str | None:
        """Act on the findings; return the action description or None."""
        return None


class RunnerClient:
    """Injectable stand-in for the runner's HTTP API: it lists the runner's own
    CLI processes, terminates one of them by pid, and reads run summaries.  The
    dashboard uses an httpx client; the tests inject a fake (criterion 3)."""

    async def list_processes(self) -> list[dict[str, Any]]:
        """pid, age, session id, run id and the run's status (no environment)."""
        raise NotImplementedError

    async def list_runs(self) -> list[dict[str, Any]]:
        """Run summaries of ``GET /v1/runs``: run id, status, started_at and
        finished_at."""
        raise NotImplementedError

    async def terminate(self, pid: int) -> dict[str, Any] | None:
        """The terminate outcome of POST /v1/processes/{pid}/terminate; None when
        the runner does not answer with 200 (not found, foreign, or run active)."""
        raise NotImplementedError


class OrphanProcessGuard(Guard):
    """Terminates a CLI process whose run is closed.

    Each check lists the runner's processes; a pid is an orphan when its run is
    closed (anything but ``queued``/``running``).  The pid is terminated only
    after the closed-run state has been confirmed by two checks in a row
    (``ORPHAN_CONFIRM_CHECKS`` default)."""

    def __init__(
        self,
        client: RunnerClient,
        notifier: Notifier | None = None,
        interval: float = DEFAULT_INTERVAL_S,
        confirm_checks: int = ORPHAN_CONFIRM_CHECKS,
    ) -> None:
        super().__init__("orphan_process", interval)
        self.client = client
        self.notifier = notifier
        self.confirm_checks = max(confirm_checks, 1)
        self._closed_counts: dict[int, int] = {}
        self._noticed: set[int] = set()

    async def check(self) -> list[Finding]:
        procs = await self.client.list_processes()
        counts: dict[int, int] = {}
        findings: list[Finding] = []
        for proc in procs:
            pid = int(proc.get("pid") or 0)
            if not pid:
                continue
            status = str(proc.get("status") or "")
            if status in ("queued", "running"):
                continue
            counts[pid] = self._closed_counts.get(pid, 0) + 1
            if counts[pid] >= self.confirm_checks:
                findings.append(
                    Finding(f"pid {pid}: run closed for {counts[pid]} checks in a row")
                )
        self._closed_counts = counts
        return findings

    async def act(self, findings: list[Finding]) -> str | None:
        if not findings:
            return None
        texts = []
        for finding in findings:
            # "pid 123: run closed for 2 checks in a row" -> 123
            pid = int(finding.detail.split(" ", 2)[1])
            result = await self.client.terminate(pid)
            if result is None:
                texts.append(f"pid {pid}: terminate refused")
            else:
                texts.append(f"terminated pid {pid} (run {result.get('run_id', '')})")
            self._noticed.add(pid)
        return "; ".join(texts)


class InterruptLoopGuard(Guard):
    """Notice-only guard for runs that keep ending as ``interrupted`` quickly.

    Three runs (``INTERRUPT_LOOP_THRESHOLD``) closed as ``interrupted`` within
    ``INTERRUPT_QUICK_S`` (120 s) of their start and finishing within
    ``INTERRUPT_LOOP_WINDOW_S`` (900 s) of now raise one notice per window;
    the guard takes no action."""

    def __init__(
        self,
        client: RunnerClient,
        notifier: Notifier | None = None,
        interval: float = DEFAULT_INTERVAL_S,
        threshold: int = INTERRUPT_LOOP_THRESHOLD,
        window_s: float = INTERRUPT_LOOP_WINDOW_S,
        quick_s: float = INTERRUPT_QUICK_S,
    ) -> None:
        super().__init__("interrupt_loop", interval)
        self.client = client
        self.notifier = notifier
        self.threshold = max(threshold, 1)
        self.window_s = max(window_s, 0.0)
        self.quick_s = max(quick_s, 0.0)
        self._last_notice_at = 0.0

    async def check(self) -> list[Finding]:
        runs = await self.client.list_runs()
        now = time.time()
        quick: list[str] = []
        for run in runs:
            if str(run.get("status") or "") != "interrupted":
                continue
            start = _epoch(run.get("started_at"))
            finished = _epoch(run.get("finished_at"))
            if start <= 0.0 or finished <= 0.0:
                continue
            if finished - start <= self.quick_s and now - finished <= self.window_s:
                quick.append(str(run.get("run_id") or ""))
        if len(quick) < self.threshold or now - self._last_notice_at < self.window_s:
            return []
        self._last_notice_at = now
        return [
            Finding(
                f"{len(quick)} interrupted runs closed within {self.window_s:.0f}s of their start"
            )
        ]

    async def act(self, findings: list[Finding]) -> str | None:
        if not findings:
            return None
        if self.notifier is None:
            logger.info("interrupt loop: %s", findings[0].detail)
            return None
        sent = await self.notifier.send(findings[0].detail)
        return "notice sent" if sent else "notice failed"


class NoticeLimiter:
    """Caps notice traffic across all guards (one notice per minute by default)."""

    def __init__(self, limit_per_minute: float = NOTICE_LIMIT_PER_MIN) -> None:
        self._limit = max(limit_per_minute, 0.0)
        self._window = time.monotonic()
        self._count = 0

    def allow(self, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        if now - self._window >= 60.0:
            self._window = now
            self._count = 0
        if self._count >= self._limit:
            return False
        self._count += 1
        return True


class Notifier:
    """Sends Telegram notices.

    The bot token and chat id are read from the container variables
    (``TELEGRAM_TOKEN_VAR`` / ``TELEGRAM_CHAT_VAR``) and are never put into
    logs, state, or results.  When either variable is missing the notice falls
    back to the log.  The transport is injectable for tests.
    """

    def __init__(
        self,
        env: Mapping[str, str] | None = None,
        transport: Callable[[str], Awaitable[bool]] | None = None,
        env_provider: Callable[[], Mapping[str, str]] | None = None,
    ) -> None:
        import os

        self._env = dict(os.environ if env is None else env)
        self._transport = transport
        self._env_provider = env_provider

    def _env_value(self, name: str) -> str:
        source = self._env_provider() if self._env_provider is not None else self._env
        return str(source.get(name, "")).strip()

    def telegram_configured(self) -> bool:
        return bool(self._env_value(TELEGRAM_TOKEN_VAR)) and bool(
            self._env_value(TELEGRAM_CHAT_VAR)
        )

    async def send(self, text: str) -> bool:
        """Send one notice.  Returns True when it was delivered (Telegram or
        the log fallback).  The token and chat id are never logged or
        returned."""
        if self._transport is not None:
            # The injected transport receives the text only; the token and
            # chat id are never passed to it.
            return await self._transport(text)
        if not self.telegram_configured():
            logger.info("guard notice (telegram variables missing): %s", text)
            return True
        token = self._env_value(TELEGRAM_TOKEN_VAR)
        chat_id = self._env_value(TELEGRAM_CHAT_VAR)
        body = json.dumps({"chat_id": chat_id, "text": text}).encode("utf-8")
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=body,
            headers={"Content-Type": "application/json"},
        )

        def _do_urlopen() -> int:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return int(getattr(resp, "status", 0) or 0)

        try:
            status = await asyncio.get_running_loop().run_in_executor(
                None, _do_urlopen
            )
            return 200 <= status < 300
        except Exception:
            logger.info("guard notice failed, logging instead: %s", text)
            return True


class SupervisorSnapshot(dict[str, object]):
    """Snapshot of the supervisor and its guard states."""

    def __init__(self, dry_run: bool, running: bool, guards: dict[str, dict[str, object]]) -> None:
        super().__init__(dry_run=dry_run, running=running, guards=guards)


class GuardSupervisor:
    """Runs the registered guards, each in its own asyncio task."""

    def __init__(
        self,
        settings: GuardSettings,
        guards: list[Guard],
        notifier: Notifier | None = None,
        history: GuardHistory | None = None,
    ) -> None:
        self.settings = settings
        self.guards = guards
        self.notifier = notifier
        self.history = history
        self.limiter = NoticeLimiter(settings.notice_limit)
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._notice_tasks: set[asyncio.Task[bool]] = set()
        self._running = False
        self._stop_event: asyncio.Event | None = None

    async def start(self) -> None:
        """Start one asyncio task per enabled guard.  With DASHBOARD_GUARDS
        unset, no tasks are started."""
        if self._running:
            return
        self._stop_event = asyncio.Event()
        started = 0
        for guard in self.guards:
            if not self.settings.enabled(guard.name):
                continue
            self._tasks[guard.name] = asyncio.create_task(self._run_guard(guard))
            started += 1
        self._running = True
        logger.info("guards started: %d of %d", started, len(self.guards))

    async def stop(self) -> None:
        """Stop all guard tasks and wait for them to finish."""
        if not self._running:
            return
        assert self._stop_event is not None
        self._stop_event.set()
        for task in self._tasks.values():
            task.cancel()
        await asyncio.gather(*self._tasks.values(), return_exceptions=True)
        self._tasks.clear()
        if self.history is not None:
            self.history.save()
        for notice_task in self._notice_tasks:
            notice_task.cancel()
        if self._notice_tasks:
            await asyncio.gather(*self._notice_tasks, return_exceptions=True)
        self._notice_tasks.clear()
        self._running = False
        logger.info("guards stopped")

    def state(self) -> SupervisorSnapshot:
        return SupervisorSnapshot(
            dry_run=self.settings.dry_run,
            running=self._running,
            guards={g.name: self._guard_status(g) for g in self.guards},
        )

    def _guard_status(self, guard: Guard) -> dict[str, object]:
        return {
            "name": guard.name,
            **guard.state.snapshot(),
            "enabled": self.settings.enabled(guard.name) and guard.state.enabled,
            "dry_run": self.settings.dry_run_for(guard.name),
        }

    async def _run_guard(self, guard: Guard) -> None:
        backoff = BACKOFF_BASE_S
        failing = False
        stop_event = self._stop_event
        assert stop_event is not None
        while not stop_event.is_set():
            started = time.monotonic()
            try:
                findings = await guard.check()
                if self.settings.dry_run_for(guard.name):
                    # dry-run: record the intended action without performing it
                    action: str | None = (
                        f"dry-run: {guard.name} would act on {len(findings)} finding(s)"
                    )
                else:
                    action = await guard.act(findings)
                recovered = failing
                failing = False
                backoff = BACKOFF_BASE_S
                guard.state.consecutive_errors = 0
                guard.state.last_run = time.time()
                guard.state.last_result = (
                    "ok (recovered)" if recovered else f"findings={len(findings)}"
                )
                guard.state.last_action = action
                guard.state.status = "ok"
                self._record(guard, "ok", started)
                if findings and action is not None:
                    self._notify(guard, action)
            except Exception:
                failing = True
                guard.state.error_count += 1
                guard.state.consecutive_errors += 1
                guard.state.status = "error"
                self._record(guard, "error", started)
                logger.exception("guard %s check failed; backoff %d s", guard.name, backoff)  # noqa: UP031
                if guard.state.consecutive_errors >= DISABLE_AFTER_ERRORS:
                    self._disable(guard)
                    return
            try:
                if stop_event.is_set():
                    break
                # sleep the current backoff first, then double it afterwards, so
                # the first failure sleeps BACKOFF_BASE_S
                await asyncio.sleep(backoff if failing else guard.interval)
            except asyncio.CancelledError:
                break
            if failing:
                backoff = min(backoff * 2, BACKOFF_CAP_S)

    def _record(self, guard: Guard, result: str, started: float) -> None:
        if self.history is not None:
            self.history.record(
                guard.name, result, guard.state.last_action,
                int((time.monotonic() - started) * 1000),
            )

    def _notify(self, guard: Guard, action: str) -> None:
        if not self.limiter.allow():
            return
        self._post(guard, action)

    def _disable(self, guard: Guard) -> None:
        guard.state.enabled = False
        self._post(
            guard, f"disabled after {guard.state.consecutive_errors} errors in a row"
        )

    def _post(self, guard: Guard, detail: str) -> None:
        text = f"guard {guard.name}: {detail}"
        if self.settings.dry_run_for(guard.name):
            logger.info("dry-run, notice not sent: %s", text)
            return
        if self.notifier is None:
            logger.info("guard notice: %s", text)
            return
        task = asyncio.create_task(self._send(text))
        self._notice_tasks.add(task)
        task.add_done_callback(self._notice_tasks.discard)

    async def _send(self, text: str) -> bool:
        notifier = self.notifier
        if notifier is None:
            logger.info("guard notice (no notifier configured): %s", text)
            return True
        try:
            return await notifier.send(text)
        except Exception:
            # Log the failure without the token: the text carries no secrets.
            logger.exception("guard notice send failed: %s", text)
            return False
