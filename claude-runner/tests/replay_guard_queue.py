"""Offline dry-run replay of the mission-notice and idle guards against the host queue logs.

Reads data-tables/ops/real_queue.log and queue_watch.log and compares what the guards would
send with what queue_watch.py sent.  Nothing is sent, nothing is written: the guards run with
dry_run, the SeenStore path is a file that must not exist, and the event source is empty.

Run from claude-runner/: python tests/replay_guard_queue.py
"""

from __future__ import annotations

import asyncio
import re
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

_SRC = str(Path(__file__).resolve().parent.parent / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from claude_runner.board_ops_redact import scrub  # noqa: E402
from claude_runner.guard_queue import (  # noqa: E402
    MINUTE_S,
    NOTICE_LIMIT_PER_HOUR,
    IdleGuard,
    MissionNoticeGuard,
    SeenStore,
)
from claude_runner.guards import Notifier  # noqa: E402

OPS_DIR = Path(__file__).resolve().parents[2] / "data-tables" / "ops"
QUEUE_LOG = OPS_DIR / "real_queue.log"
WATCH_LOG = OPS_DIR / "queue_watch.log"
ABSENT_STATE_PATH = Path(tempfile.gettempdir()) / "replay-absent" / "queue_state.json"
MISSION = "ahawr-worker-attempt-chain"
WINDOW_START_LINE = 2406
WINDOW_END_LINE = 2784
WINDOW_START_TS = "2026-10-20 03:43:26"
IDLE_SCAN_MINUTES = 60
TS_RE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")
SENT_RE = re.compile(r"^(\S+ \S+) sent: (.*)$")
TS_FORMAT = "%Y-%m-%d %H:%M:%S"
SHOW_CHARS = 110


@dataclass(frozen=True)
class Notice:
    at: str
    text: str


class ReplayQueueLog:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def get_queue_log(self, limit: int = 1000) -> list[dict[str, Any]]:
        return self._rows[:limit]


class NoEvents:
    def events(self, state_key: str) -> list[dict[str, Any]]:
        return []


def window_rows() -> list[dict[str, Any]]:
    lines = QUEUE_LOG.read_text(encoding="utf-8").splitlines()
    if len(lines) < WINDOW_END_LINE:
        raise ValueError(f"{QUEUE_LOG.name} has {len(lines)} lines, expected {WINDOW_END_LINE}")
    rows: list[dict[str, Any]] = []
    at = ""
    for number, line in enumerate(lines[:WINDOW_END_LINE], start=1):
        match = TS_RE.match(line)
        if match:
            at = match.group(1)
        if number >= WINDOW_START_LINE:
            rows.append({"at": at, "line": line})
    rows.reverse()
    return rows


async def would_be_notices(rows: list[dict[str, Any]]) -> list[Notice]:
    seen = SeenStore(str(ABSENT_STATE_PATH))
    notifier = Notifier(env={})
    notices: list[Notice] = []
    for row in reversed(rows):
        guard = MissionNoticeGuard(
            queue_log=ReplayQueueLog([row]),
            events=NoEvents(),
            seen=seen,
            notifier=notifier,
            dry_run=lambda: True,
        )
        findings = await guard.check()
        notices.extend(Notice(str(row["at"]), scrub(finding.detail)) for finding in findings)
    return notices


def sent_notices() -> list[Notice]:
    notices: list[Notice] = []
    for line in WATCH_LOG.read_text(encoding="utf-8").splitlines():
        match = SENT_RE.match(line)
        if match and match.group(1) >= WINDOW_START_TS:
            notices.append(Notice(match.group(1), scrub(match.group(2))))
    return notices


async def idle_notice_minute() -> int | None:
    clock: dict[str, float] = {"now": 0.0}
    guard = IdleGuard(
        alive=lambda: True,
        running=lambda: 0,
        notifier=Notifier(env={}),
        dry_run=lambda: True,
        now=lambda: clock["now"],
    )
    for minute in range(IDLE_SCAN_MINUTES + 1):
        clock["now"] = minute * MINUTE_S
        if await guard.check():
            return minute
    return None


def _head(text: str) -> str:
    return text.split("\n", 1)[0]


def _delay(first: str, second: str) -> str:
    start = datetime.strptime(first, TS_FORMAT)
    seconds = (datetime.strptime(second, TS_FORMAT) - start).total_seconds()
    return f"{int(seconds)} s later"


def _sent_only_reason(text: str) -> str:
    if text.startswith("🔁"):
        return "process-table restart (queue_watch.py:227-241); no queue-log line for it"
    return "no would-be notice matches; check manually"


def _match_difference(would_be: str, sent: str) -> str:
    if "Последнее ревью" in sent and "Последнее ревью" not in would_be:
        return "sent adds the last review block from DB events; the replay has no event source"
    return "texts differ"


def compare(would_be: list[Notice], sent: list[Notice]) -> list[str]:
    report: list[str] = []
    unmatched = list(would_be)
    for item in sent:
        match = next((w for w in unmatched if item.text.startswith(_head(w.text))), None)
        if match is None:
            report.append(f"SENT ONLY  {item.at}  {_head(item.text)[:SHOW_CHARS]}")
            report.append(f"    reason: {_sent_only_reason(item.text)}")
            continue
        unmatched.remove(match)
        report.append(f"MATCH      {match.at} -> sent {item.at} ({_delay(match.at, item.at)})")
        if item.text != match.text:
            report.append(f"    difference: {_match_difference(match.text, item.text)}")
    for item in unmatched:
        report.append(f"WOULD ONLY {item.at}  {_head(item.text)[:SHOW_CHARS]}")
        report.append("    reason: no sent line in queue_watch.log")
    return report


def main() -> int:
    if ABSENT_STATE_PATH.exists():
        print(f"refusing to run: {ABSENT_STATE_PATH} exists", file=sys.stderr)
        return 1
    rows = window_rows()
    would_be = asyncio.run(would_be_notices(rows))
    sent = sent_notices()
    print(f"Last finished mission: {MISSION} ({QUEUE_LOG.name}:2647)")
    print(f"Window: {QUEUE_LOG.name} lines {WINDOW_START_LINE}-{WINDOW_END_LINE}, {len(rows)} rows")
    print(f"Would-be notices: {len(would_be)} (limit {NOTICE_LIMIT_PER_HOUR} per hour)")
    for item in reversed(would_be):
        print(f"  {item.at}  {_head(item.text)[:SHOW_CHARS]}")
    print(f"Sent by queue_watch.py since {WINDOW_START_TS}: {len(sent)}")
    for item in sent:
        print(f"  {item.at}  {_head(item.text)[:SHOW_CHARS]}")
    print("Comparison:")
    for line in compare(would_be, sent):
        print(f"  {line}")
    minute = asyncio.run(idle_notice_minute())
    print(f"Idle guard (synthetic: running=0, alive=True, clock only): notice at minute {minute}")
    print("Idle replay: not replayable, the logs hold no historical run counts")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
