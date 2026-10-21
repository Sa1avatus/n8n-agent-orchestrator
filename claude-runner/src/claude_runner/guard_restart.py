"""Requests a queue restart by writing one request file; the host launcher acts on it.

The guard never starts the queue.  It writes a request only when the queue process is
gone, no AHAWR execution runs and fewer than RESTART_LIMIT_PER_HOUR requests were written
in the last hour.  Unknown state blocks the request.
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from collections import deque
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Protocol

from claude_runner.guards import DEFAULT_INTERVAL_S, Finding, Guard

logger = logging.getLogger("claude_runner.guard_restart")

RESTART_LIMIT_PER_HOUR = 4
HOUR_S = 3600
REASON = "queue_not_alive"


class QueueStateSource(Protocol):
    def is_alive(self) -> bool | None: ...

    def running_count(self) -> int | None: ...


class QueueRestartGuard(Guard):
    """Writes one restart request file when the queue is gone and nothing runs."""

    def __init__(
        self,
        state: QueueStateSource,
        request_path: str,
        interval: float = DEFAULT_INTERVAL_S,
        dry_run: Callable[[], bool] = lambda: False,
        now: Callable[[], float] = time.time,
    ) -> None:
        super().__init__("queue_restart", interval)
        self.queue = state
        self.request_path = request_path
        self.dry_run = dry_run
        self._now = now
        self._written: deque[float] = deque()

    async def check(self) -> list[Finding]:
        if self.queue.is_alive() is not False or self.queue.running_count() != 0:
            return []
        if os.path.exists(self.request_path):
            return []
        now = self._now()
        while self._written and now - self._written[0] >= HOUR_S:
            self._written.popleft()
        if len(self._written) >= RESTART_LIMIT_PER_HOUR:
            return []
        return [Finding(detail=REASON)]

    async def act(self, findings: list[Finding]) -> str | None:
        if not findings:
            return None
        if self.dry_run():
            logger.info("queue restart would be requested: %s", REASON)
            return None
        now = self._now()
        request: dict[str, object] = {
            "request_id": str(uuid.uuid4()),
            "requested_at": datetime.fromtimestamp(now, tz=UTC).isoformat(timespec="seconds"),
            "reason": REASON,
            "limits": {"per_hour": RESTART_LIMIT_PER_HOUR, "window_s": HOUR_S},
        }
        tmp_path = self.request_path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(request, f)
        os.replace(tmp_path, self.request_path)
        self._written.append(now)
        logger.info("queue restart requested: %s", request["request_id"])
        return "restart requested"
