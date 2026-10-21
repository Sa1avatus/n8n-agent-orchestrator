"""Telegram notices about the mission queue, read from the ops store.

Carries the notice part of data-tables/ops/queue_watch.py with the same texts.  Each
notice is sent once, also across restarts: the keys of sent lines live in a JSON file
(SeenStore).  Notices are sent through the one Notifier; the guard is not a second sender.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import tempfile
import time
from collections import deque
from collections.abc import Callable
from typing import Any, Protocol

from claude_runner.board_ops_redact import scrub
from claude_runner.guards import DEFAULT_INTERVAL_S, Finding, Guard, Notifier

logger = logging.getLogger("claude_runner.guard_queue")

NOTICE_LIMIT_PER_HOUR = 20
HOUR_S = 3600.0
MINUTE_S = 60.0
IDLE_MINUTES_DEFAULT = 20
IDLE_NOTICE = "💤 Ничего не выполняется уже {minutes} мин, хотя очередь жива. Проверьте прогон."
QUEUE_LOG_LIMIT = 1000
SEEN_KEEP = 4000
REVIEW_REASON_MAX_CHARS = 350

NOT_COMPLETED_RE = re.compile(r"MISSION NOT COMPLETED: (\S+) \((\w+)\)")
COMPLETED_RE = re.compile(r"ended (\S+): completed")
STOPPED_MARK = "REAL QUEUE STOPPED"
ALERT_MARK = "ALERT:"
ALERT_IGNORED = ("DISABLE_THINKING", "new missions were not inserted", STOPPED_MARK)
DONE_MARK = "=== real queue done"


class QueueLogSource(Protocol):
    def get_queue_log(self, limit: int = 1000) -> list[dict[str, Any]]: ...


class EventSource(Protocol):
    def events(self, state_key: str) -> list[dict[str, Any]]: ...


def _read_keys(path: str) -> list[str]:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    keys = data.get("seen") if isinstance(data, dict) else None
    if not isinstance(keys, list) or not all(isinstance(k, str) for k in keys):
        raise ValueError(f"malformed queue state file: {path}")
    return keys


class SeenStore:
    """Keys of the queue-log lines already handled, kept in a JSON file.

    ``fresh`` is True when no state file existed: the first run only records the
    current log and sends nothing, so the history is not sent again.
    """

    def __init__(self, path: str) -> None:
        if not path:
            raise ValueError("queue state path is empty")
        self.path = path
        self.fresh = False
        self._dirty = False
        try:
            keys = _read_keys(path)
        except FileNotFoundError:
            self.fresh = True
            keys = []
        self._keys: dict[str, None] = dict.fromkeys(keys)

    def has(self, key: str) -> bool:
        return key in self._keys

    def add(self, key: str) -> None:
        self._keys[key] = None
        self._dirty = True

    def add_all(self, keys: list[str]) -> None:
        for key in keys:
            self.add(key)

    def save(self) -> None:
        if not self._dirty:
            return
        keys = list(self._keys)[-SEEN_KEEP:]
        self._keys = dict.fromkeys(keys)
        directory = os.path.dirname(os.path.abspath(self.path))
        fd, tmp_path = tempfile.mkstemp(dir=directory, prefix=".queue_state.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump({"seen": keys}, f)
            os.replace(tmp_path, self.path)
        except BaseException:
            os.unlink(tmp_path)
            raise
        self._dirty = False
        self.fresh = False


class HourlyLimit:
    """At most ``per_hour`` notices in any rolling hour."""

    def __init__(self, per_hour: int, now: Callable[[], float] = time.monotonic) -> None:
        self._per_hour = per_hour
        self._now = now
        self._sent: deque[float] = deque()

    def allow(self) -> bool:
        now = self._now()
        while self._sent and now - self._sent[0] >= HOUR_S:
            self._sent.popleft()
        if len(self._sent) >= self._per_hour:
            return False
        self._sent.append(now)
        return True


def _line_key(row: dict[str, Any]) -> str:
    raw = f"{row.get('at', '')}\n{row.get('line', '')}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _queue_notice(line: str) -> str | None:
    if STOPPED_MARK in line and "ALERT" not in line:
        reason = line.split("REAL QUEUE STOPPED:")[-1].strip()
        return (
            "⛔ Очередь миссий остановилась: " + reason
            + ". Сторож запустит её заново, когда n8n освободится."
        )
    if ALERT_MARK in line and not any(mark in line for mark in ALERT_IGNORED):
        return "⚠️ " + line.split(ALERT_MARK, 1)[1].strip()
    if DONE_MARK in line:
        return "🏁 Очередь миссий закончилась: все миссии пройдены или пропущены."
    return None


def _one_based(value: Any) -> str:
    return "?" if value is None else str(int(value) + 1)


class MissionNoticeGuard(Guard):
    """Sends a notice for each new mission result, ALERT line and queue stop or end."""

    def __init__(
        self,
        queue_log: QueueLogSource,
        events: EventSource,
        seen: SeenStore,
        notifier: Notifier,
        interval: float = DEFAULT_INTERVAL_S,
        limit: HourlyLimit | None = None,
        dry_run: Callable[[], bool] = lambda: False,
    ) -> None:
        super().__init__("mission_notice", interval)
        self.queue_log = queue_log
        self.events = events
        self.seen = seen
        self.notifier = notifier
        self.limit = limit or HourlyLimit(NOTICE_LIMIT_PER_HOUR)
        self.dry_run = dry_run
        self._row_keys: list[str] = []
        self._pending: list[tuple[str, list[str]]] = []

    async def check(self) -> list[Finding]:
        rows = self.queue_log.get_queue_log(QUEUE_LOG_LIMIT)
        self._row_keys = [_line_key(row) for row in rows]
        self._pending = []
        if self.seen.fresh and not self.dry_run():
            return []
        for row, key in reversed(list(zip(rows, self._row_keys, strict=True))):
            if not self.seen.has(key):
                texts = self._notices_for(str(row.get("line", "")))
                self._pending.append((key, texts))
                if self.dry_run():
                    for text in texts:
                        logger.info("mission notice would send: %s", scrub(text)[:200])
        return [Finding(detail=text) for _, texts in self._pending for text in texts]

    async def act(self, findings: list[Finding]) -> str | None:
        if self.dry_run():
            return None
        if self.seen.fresh:
            self.seen.add_all(self._row_keys)
        else:
            await self._deliver_pending()
        self.seen.save()
        return None

    async def _deliver_pending(self) -> None:
        for key, texts in self._pending:
            if not await self._deliver(texts):
                break
            self.seen.add(key)
            if texts:
                self.seen.save()

    async def _deliver(self, texts: list[str]) -> bool:
        for text in texts:
            if not self.limit.allow():
                logger.warning("mission notice dropped, 20 per hour: %s", scrub(text)[:80])
                continue
            if not await self.notifier.send(scrub(text)):
                return False
        return True

    def _notices_for(self, line: str) -> list[str]:
        texts: list[str] = []
        not_completed = NOT_COMPLETED_RE.search(line)
        if not_completed:
            mission, state = not_completed.group(1), not_completed.group(2)
            review = self._last_review(mission)
            texts.append(f"❌ Миссия {mission} не завершена (состояние {state}).{review}")
        completed = COMPLETED_RE.search(line)
        if completed:
            texts.append(f"✅ Миссия {completed.group(1)} завершена.")
        queue = _queue_notice(line)
        if queue is not None:
            texts.append(queue)
        return texts

    def _last_review(self, mission: str) -> str:
        reviews = [e for e in self.events.events(mission) if e.get("event_type") == "review_parsed"]
        if not reviews:
            return ""
        last = reviews[-1]
        reason = str(last.get("review_reason") or "").replace("\n", " ")
        return (
            f"\nПоследнее ревью: задача {_one_based(last.get('task_index'))}, "
            f"попытка {last.get('task_attempt')}, оценка {last.get('review_score')}.\n"
            f"{reason[:REVIEW_REASON_MAX_CHARS]}"
        )


class IdleGuard(Guard):
    """Sends one notice when the queue is alive but nothing runs for ``idle_minutes``.

    The idle clock starts at the first observation of an alive, idle queue and restarts
    whenever the state is not confirmed (unknown liveness or run count).  The notice is
    re-armed only by seen activity, a run in ``running`` state.
    """

    def __init__(
        self,
        alive: Callable[[], bool | None],
        running: Callable[[], int | None],
        notifier: Notifier,
        interval: float = DEFAULT_INTERVAL_S,
        idle_minutes: int = IDLE_MINUTES_DEFAULT,
        dry_run: Callable[[], bool] = lambda: False,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        super().__init__("idle", interval)
        self.alive = alive
        self.running = running
        self.notifier = notifier
        self.idle_minutes = idle_minutes
        self.idle_s = idle_minutes * MINUTE_S
        self.dry_run = dry_run
        self._now = now
        self._idle_since: float | None = None
        self._noticed = False

    async def check(self) -> list[Finding]:
        running = self.running()
        if running is not None and running > 0:
            self._idle_since = None
            self._noticed = False
            return []
        if running is None or self.alive() is not True:
            self._idle_since = None
            return []
        now = self._now()
        if self._idle_since is None:
            self._idle_since = now
        if self._noticed or now - self._idle_since < self.idle_s:
            return []
        return [Finding(detail=IDLE_NOTICE.format(minutes=self.idle_minutes))]

    async def act(self, findings: list[Finding]) -> str | None:
        if not findings or self.dry_run():
            return None
        if await self.notifier.send(findings[0].detail):
            self._noticed = True
            return "notice sent"
        return "notice failed"
