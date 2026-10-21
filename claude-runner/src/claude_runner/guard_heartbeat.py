"""Reads the host watchers' heartbeat files and sends one notice per stale episode.

The host scripts write ``<name>.json`` into a read-only mount.  The guard only reads
them.  A missing file or an empty heartbeat directory is ``unknown``: it never counts as
stale and never triggers an action.  A file that exists but cannot be parsed is an
``error``, reported once per episode without stopping the guard.
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime

from claude_runner.guards import DEFAULT_INTERVAL_S, Finding, Guard, Notifier

logger = logging.getLogger("claude_runner.guard_heartbeat")

HEARTBEAT_NAMES = ("docker_watchdog", "gpu_power_governor")
STALE_SECONDS_DEFAULT = 300.0
TIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


@dataclass(frozen=True)
class HeartbeatSettings:
    heartbeat_dir: str
    stale_seconds: float

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> HeartbeatSettings:
        try:
            stale = float(env.get("DASHBOARD_GUARDS_HEARTBEAT_STALE_S", ""))
        except ValueError:
            stale = STALE_SECONDS_DEFAULT
        return cls(
            heartbeat_dir=env.get("DASHBOARD_GUARDS_HEARTBEAT_DIR", "").strip(),
            stale_seconds=max(stale, 0.0),
        )


class HostHeartbeatGuard(Guard):
    """Reports heartbeat files that go stale, error, or recover; never writes."""

    def __init__(
        self,
        settings: HeartbeatSettings,
        notifier: Notifier,
        interval: float = DEFAULT_INTERVAL_S,
        dry_run: Callable[[], bool] = lambda: False,
        now: Callable[[], float] = time.time,
    ) -> None:
        super().__init__("host_heartbeat", interval)
        self.settings = settings
        self.notifier = notifier
        self.dry_run = dry_run
        self._now = now
        self._alerted: dict[str, str] = {}

    def status_entries(self) -> list[dict[str, object]]:
        return [self._read(name) for name in HEARTBEAT_NAMES]

    async def check(self) -> list[Finding]:
        findings: list[Finding] = []
        for entry in self.status_entries():
            name = str(entry["name"])
            status = str(entry["status"])
            if status == "unknown":
                continue
            if status == "fresh":
                if name in self._alerted:
                    del self._alerted[name]
                    findings.append(Finding(f"heartbeat {name} recovered", severity="info"))
                continue
            if self._alerted.get(name) != status:
                self._alerted[name] = status
                findings.append(Finding(f"heartbeat {name} {status}: {entry['detail']}"))
        return findings

    async def act(self, findings: list[Finding]) -> str | None:
        if not findings:
            return None
        if self.dry_run():
            logger.info("heartbeat notices would be sent: %d", len(findings))
            return None
        sent = 0
        for finding in findings:
            if await self.notifier.send(finding.detail):
                sent += 1
        return f"notices sent: {sent} of {len(findings)}"

    def _read(self, name: str) -> dict[str, object]:
        entry: dict[str, object] = {
            "name": name,
            "time": None,
            "last_action": None,
            "status": "unknown",
            "detail": "no heartbeat file",
        }
        if not self.settings.heartbeat_dir:
            return entry
        path = os.path.join(self.settings.heartbeat_dir, f"{name}.json")
        try:
            with open(path, encoding="utf-8") as f:
                raw = json.load(f)
        except FileNotFoundError:
            return entry
        except (OSError, ValueError) as exc:
            return {**entry, "status": "error", "detail": f"unreadable heartbeat: {exc}"}
        if not isinstance(raw, dict):
            return {**entry, "status": "error", "detail": "heartbeat is not a JSON object"}
        stamp = raw.get("time")
        try:
            written = datetime.strptime(str(stamp), TIME_FORMAT).replace(tzinfo=UTC).timestamp()
        except ValueError:
            return {**entry, "status": "error", "detail": f"bad heartbeat time: {stamp!r}"}
        age = self._now() - written
        status = "fresh" if age <= self.settings.stale_seconds else "stale"
        return {
            **entry,
            "time": stamp,
            "last_action": raw.get("last_action"),
            "status": status,
            "detail": f"last heartbeat {int(age)} s ago",
        }
