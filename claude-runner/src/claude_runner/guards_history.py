"""Bounded history of guard checks, kept for the dashboard's Guards tab.

One ring buffer per guard holds the last ``HISTORY_MAX_ENTRIES`` checks (time,
result, a short action text, duration).  The history is saved atomically to a
JSON file in the guards state directory at most every ``SAVE_INTERVAL_S`` seconds
and at shutdown, and loaded at start; a missing or broken file means an empty
history.  Entries carry no environment values.
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections import deque

logger = logging.getLogger("claude_runner.guards_history")

HISTORY_MAX_ENTRIES = 500
THIN_MAX_POINTS = 200
SAVE_INTERVAL_S = 60.0
ACTION_MAX_CHARS = 200
WINDOWS_S: dict[str, float] = {"1h": 3600.0, "24h": 86400.0, "7d": 604800.0}
DEFAULT_WINDOW = "24h"

Entry = dict[str, object]


class GuardHistory:
    """Ring buffers of guard checks with atomic persistence."""

    def __init__(self, path: str = "", max_entries: int = HISTORY_MAX_ENTRIES) -> None:
        self.path = path
        self.max_entries = max(max_entries, 1)
        self._entries: dict[str, deque[Entry]] = {}
        self._last_save = 0.0
        self._load()

    def record(
        self,
        name: str,
        result: str,
        action: str | None,
        duration_ms: int,
        now: float | None = None,
    ) -> None:
        stamp = time.time() if now is None else now
        entry: Entry = {
            "t": stamp,
            "result": result,
            "action": action[:ACTION_MAX_CHARS] if action else None,
            "ms": max(duration_ms, 0),
        }
        self._entries.setdefault(name, deque(maxlen=self.max_entries)).append(entry)
        if stamp - self._last_save >= SAVE_INTERVAL_S:
            self.save(stamp)

    def window(self, window: str, now: float | None = None) -> dict[str, list[Entry]]:
        """Entries of every guard inside ``window``, thinned to ``THIN_MAX_POINTS``."""
        stamp = time.time() if now is None else now
        since = stamp - WINDOWS_S.get(window, WINDOWS_S[DEFAULT_WINDOW])
        return {
            name: _thin([e for e in entries if float(str(e["t"])) >= since])
            for name, entries in self._entries.items()
        }

    def health(self, name: str, window: str, now: float | None = None) -> dict[str, object]:
        """Checks and errors of one guard inside ``window``. Only the last ``max_entries`` checks
        are kept, so a long window of a fast guard covers less time than it names."""
        stamp = time.time() if now is None else now
        since = stamp - WINDOWS_S.get(window, WINDOWS_S[DEFAULT_WINDOW])
        recent = [e for e in self._entries.get(name, ()) if float(str(e["t"])) >= since]
        errors = sum(1 for e in recent if e["result"] == "error")
        ok_pct = round(100 * (len(recent) - errors) / len(recent), 1) if recent else None
        return {"window": window, "checks": len(recent), "errors": errors, "ok_pct": ok_pct}

    def summary(self) -> dict[str, dict[str, int]]:
        """Counts per guard over the stored entries: checks, errors, actions."""
        return {
            name: {
                "checks": len(entries),
                "errors": sum(1 for e in entries if e["result"] == "error"),
                "actions": sum(1 for e in entries if e["action"]),
            }
            for name, entries in self._entries.items()
        }

    def save(self, now: float | None = None) -> None:
        self._last_save = time.time() if now is None else now
        if not self.path:
            return
        data = {name: list(entries) for name, entries in self._entries.items()}
        tmp = self.path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as handle:
                json.dump(data, handle)
            os.replace(tmp, self.path)
        except OSError as exc:
            logger.warning("guard history not saved: %s", exc)

    def _load(self) -> None:
        if not self.path or not os.path.exists(self.path):
            return
        try:
            with open(self.path, encoding="utf-8") as handle:
                data = json.load(handle)
            for name, entries in data.items():
                buf: deque[Entry] = deque(maxlen=self.max_entries)
                for e in entries:
                    buf.append({"t": float(e["t"]), "result": str(e["result"]),
                                "action": e.get("action"), "ms": int(e.get("ms", 0))})
                self._entries[str(name)] = buf
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
            self._entries = {}
            logger.warning("guard history file ignored (%s): %s", self.path, exc)


def _thin(entries: list[Entry]) -> list[Entry]:
    """At most THIN_MAX_POINTS entries; every error and every action is kept."""
    if len(entries) <= THIN_MAX_POINTS:
        return entries
    keep = [e for e in entries if e["result"] != "ok" or e["action"]]
    plain = [e for e in entries if e["result"] == "ok" and not e["action"]]
    room = max(THIN_MAX_POINTS - len(keep), 0)
    if room and plain:
        step = len(plain) / room
        plain = [plain[int(i * step)] for i in range(min(room, len(plain)))]
    else:
        plain = []
    return sorted(keep + plain, key=lambda e: float(str(e["t"])))
