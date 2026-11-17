from __future__ import annotations

import importlib
import importlib.util
import io
import json
import urllib.error
import urllib.request
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

OPS_DIR = Path(__file__).resolve().parents[1].parent / "data-tables" / "ops"
QUEUE_BOARD_FILE = OPS_DIR / "queue_board.py"
BASE = "http://127.0.0.1:9"
QUEUE_URL = BASE + "/api/board/queue"
LIVE_URL = BASE + "/api/board/live-states"
MISSIONS_URL = BASE + "/api/board/missions"


def _load(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


queue_board = _load("queue_board", QUEUE_BOARD_FILE)


def _entry(
    mission_id: str,
    position: int,
    enabled: bool = True,
    attempts_per_task: float | None = None,
    retry_failed: bool = False,
) -> dict[str, Any]:
    return {
        "position": position,
        "mission_id": mission_id,
        "enabled": enabled,
        "attempts_per_task": attempts_per_task,
        "retry_failed": retry_failed,
        "note": None,
    }


def test_completed_mission_is_skipped() -> None:
    entries = [_entry("m_done", 1), _entry("m_next", 2)]
    states = {"m_done": "completed", "m_next": "queued"}
    picked = queue_board.next_mission(entries, states, set())
    assert picked is not None and picked["mission_id"] == "m_next"


def test_failed_mission_without_retry_is_skipped() -> None:
    entries = [_entry("m_failed", 1), _entry("m_next", 2)]
    states = {"m_failed": "failed", "m_next": "queued"}
    picked = queue_board.next_mission(entries, states, set())
    assert picked is not None and picked["mission_id"] == "m_next"


def test_failed_mission_with_retry_flag_is_picked() -> None:
    entries = [_entry("m_failed", 1), _entry("m_next", 2)]
    states = {"m_failed": "failed", "m_next": "queued"}
    picked = queue_board.next_mission(entries, states, {"m_failed"})
    assert picked is not None and picked["mission_id"] == "m_failed"


def test_active_mission_resumes_first_even_when_not_at_position_one() -> None:
    entries = [_entry("m_queued", 1), _entry("m_active", 2)]
    states = {"m_queued": "queued", "m_active": "active"}
    picked = queue_board.next_mission(entries, states, set())
    assert picked is not None and picked["mission_id"] == "m_active"


def test_disabled_entry_is_skipped() -> None:
    entries = [_entry("m_off", 1, enabled=False), _entry("m_on", 2)]
    states = {"m_off": "queued", "m_on": "queued"}
    picked = queue_board.next_mission(entries, states, set())
    assert picked is not None and picked["mission_id"] == "m_on"


def test_mission_without_live_state_is_skipped_as_unknown() -> None:
    entries = [_entry("m_unknown", 1), _entry("m_known", 2)]
    states = {"m_known": "queued"}
    picked = queue_board.next_mission(entries, states, set())
    assert picked is not None and picked["mission_id"] == "m_known"


def test_empty_queue_gives_none() -> None:
    assert queue_board.next_mission([], {}, set()) is None


def test_empty_attempts_per_task_gives_shell_default() -> None:
    entry = _entry("m_next", 1, attempts_per_task=None)
    picked = queue_board.next_mission([entry], {"m_next": "queued"}, set())
    assert picked is not None
    assert queue_board._attempts(picked) == str(queue_board.DEFAULT_ATTEMPTS_PER_TASK)


def test_next_mission_order_unchanged_flag_off() -> None:
    """Pin of the flag-off order: a queue with an active, a failed, a completed
    and three waiting entries.  Repeated next_mission calls (each one skipping
    what was already picked) return the active mission first, then the waiting
    entries in position order; failed (no retry) and completed never come back,
    and the queue empties after the three waiting ones."""
    entries = [
        _entry("m_active", 1),
        _entry("m_failed", 2),
        _entry("m_done", 3),
        _entry("m_wait1", 4),
        _entry("m_wait2", 5),
        _entry("m_wait3", 6),
    ]
    states = {
        "m_active": "active",
        "m_failed": "failed",
        "m_done": "completed",
        "m_wait1": "queued",
        "m_wait2": "queued",
        "m_wait3": "queued",
    }
    order: list[str] = []
    skipped: frozenset[str] = frozenset()
    for _ in range(len(entries)):  # bound: one call per entry, breaks on None
        picked = queue_board.next_mission(entries, states, set(), skipped)
        if picked is None:
            break
        order.append(picked["mission_id"])
        skipped = frozenset({*skipped, picked["mission_id"]})
    assert order == ["m_active", "m_wait1", "m_wait2", "m_wait3"]


def _dependency_rule() -> ModuleType:
    """The runner's shared due/waiting/blocked rule, loaded as queue_board loads it."""
    rule: ModuleType = queue_board._shared_dependency_rule()
    return rule


def _flag_on_queue(entries: list[dict[str, Any]], states: dict[str, str]) -> list[dict[str, Any]]:
    """Queue rows in the shape GET /api/board/queue gives when mission_dependencies is on."""
    verdicts = _dependency_rule().classify_queue(entries, states)
    return [
        {
            **entry,
            "depends_on": verdicts[entry["mission_id"]]["depends_on"],
            "dependency_state": verdicts[entry["mission_id"]]["state"],
            "dependency_reason": verdicts[entry["mission_id"]]["reason"],
        }
        for entry in entries
    ]


def test_flag_on_skips_an_entry_with_unmet_dependency_and_picks_the_next_due_one() -> None:
    rule = _dependency_rule()
    entries = [
        _entry("m_pre", 1),
        {**_entry("m_after", 2), "depends_on": ["m_pre"]},
        _entry("m_free", 3),
    ]
    states = {"m_pre": "queued", "m_after": "queued", "m_free": "queued"}
    first = queue_board.next_mission(entries, states, set(), frozenset(), rule)
    assert first is not None and first["mission_id"] == "m_pre"
    second = queue_board.next_mission(entries, states, set(), frozenset({"m_pre"}), rule)
    assert second is not None and second["mission_id"] == "m_free"


def test_flag_on_blocked_dependency_entry_is_never_picked() -> None:
    rule = _dependency_rule()
    entries = [
        _entry("m_dep", 1),
        {**_entry("m_needs", 2), "depends_on": ["m_dep"]},
        _entry("m_free", 3),
    ]
    states = {"m_dep": "failed", "m_needs": "queued", "m_free": "queued"}
    order: list[str] = []
    skipped: frozenset[str] = frozenset()
    for _ in range(len(entries)):  # bound: one call per entry, breaks on None
        picked = queue_board.next_mission(entries, states, set(), skipped, rule)
        if picked is None:
            break
        order.append(picked["mission_id"])
        skipped = frozenset({*skipped, picked["mission_id"]})
    assert order == ["m_free"]
    assert "m_needs" not in order and "m_dep" not in order


def test_flag_on_dependency_completed_returns_the_previously_waiting_entry() -> None:
    rule = _dependency_rule()
    entries = [
        _entry("m_pre", 1),
        {**_entry("m_after", 2), "depends_on": ["m_pre"]},
        _entry("m_free", 3),
    ]
    states = {"m_pre": "queued", "m_after": "queued", "m_free": "queued"}
    first = queue_board.next_mission(entries, states, set(), frozenset({"m_pre"}), rule)
    assert first is not None and first["mission_id"] == "m_free"
    done_states = {"m_pre": "completed", "m_after": "queued", "m_free": "queued"}
    second = queue_board.next_mission(entries, done_states, set(), frozenset({"m_pre"}), rule)
    assert second is not None and second["mission_id"] == "m_after"


def test_flag_on_failed_dependency_is_blocked_and_listed_with_the_reason() -> None:
    rule = _dependency_rule()
    entries = [
        _entry("m_dep", 1),
        {**_entry("m_needs", 2), "depends_on": ["m_dep"]},
        _entry("m_free", 3),
    ]
    states = {"m_dep": "failed", "m_needs": "queued", "m_free": "queued"}
    verdict = rule.classify_queue(entries, states)["m_needs"]
    assert verdict["state"] == rule.BLOCKED_STATE
    assert verdict["reason"] == "blocked by m_dep: a dependency failed"
    assert queue_board._blocked_lines(entries, states, frozenset(), rule) == [
        "m_needs: blocked by m_dep: a dependency failed"
    ]
    order: list[str] = []
    skipped: frozenset[str] = frozenset()
    for _ in range(len(entries)):  # bound: one call per entry, breaks on None
        picked = queue_board.next_mission(entries, states, set(), skipped, rule)
        if picked is None:
            break
        order.append(picked["mission_id"])
        skipped = frozenset({*skipped, picked["mission_id"]})
    assert "m_needs" not in order
    # flag off the same queue would return the dependent, proving the rule changes the pick
    flag_off = queue_board.next_mission(entries, states, set(), frozenset({"m_dep"}), None)
    assert flag_off is not None and flag_off["mission_id"] == "m_needs"


def test_flag_on_retry_then_completed_dependency_unblocks_the_dependent() -> None:
    rule = _dependency_rule()
    entries = [
        _entry("m_dep", 1, retry_failed=True),
        {**_entry("m_needs", 2), "depends_on": ["m_dep"]},
    ]
    retry_flags = {"m_dep"}
    states = {"m_dep": "failed", "m_needs": "queued"}
    picked = queue_board.next_mission(entries, states, retry_flags, frozenset(), rule)
    assert picked is not None and picked["mission_id"] == "m_dep"
    completed = {"m_dep": "completed", "m_needs": "queued"}
    after = queue_board.next_mission(entries, completed, retry_flags, frozenset({"m_dep"}), rule)
    assert after is not None and after["mission_id"] == "m_needs"


def test_flag_on_dependency_removed_from_depends_on_makes_the_dependent_due() -> None:
    rule = _dependency_rule()
    states = {"m_dep": "failed", "m_needs": "queued"}
    before = [
        _entry("m_dep", 1),
        {**_entry("m_needs", 2), "depends_on": ["m_dep"]},
    ]
    assert queue_board.next_mission(before, states, set(), frozenset({"m_dep"}), rule) is None
    after = [
        _entry("m_dep", 1),
        {**_entry("m_needs", 2), "depends_on": []},
    ]
    picked = queue_board.next_mission(after, states, set(), frozenset({"m_dep"}), rule)
    assert picked is not None and picked["mission_id"] == "m_needs"


def test_flag_on_queue_with_only_waiting_and_blocked_entries_returns_no_mission() -> None:
    rule = _dependency_rule()
    entries = [
        {**_entry("m_wait", 1), "depends_on": ["m_pre"]},
        {**_entry("m_blocked", 2), "depends_on": ["m_dead"]},
    ]
    states = {"m_wait": "queued", "m_blocked": "queued", "m_dead": "failed"}
    assert queue_board.next_mission(entries, states, set(), frozenset(), rule) is None
    # without the rule the same queue would start the waiting entry: the rule is what stops it
    off = queue_board.next_mission(entries, states, set(), frozenset(), None)
    assert off is not None and off["mission_id"] == "m_wait"


def test_flag_on_active_mission_resumes_first_even_with_due_entries() -> None:
    rule = _dependency_rule()
    entries = [
        _entry("m_due_first", 1),
        _entry("m_active", 2),
        {**_entry("m_also_due", 3), "depends_on": []},
    ]
    states = {"m_due_first": "queued", "m_active": "active", "m_also_due": "queued"}
    picked = queue_board.next_mission(entries, states, set(), frozenset(), rule)
    assert picked is not None and picked["mission_id"] == "m_active"


def test_flag_on_split_mission_with_empty_depends_on_behaves_like_the_flag_off_order() -> None:
    """A split mission gets the empty ``depends_on`` list of its parts: the pick order
    must stay the flag-off order (active first, waiting in position order)."""
    rule = _dependency_rule()
    entries = [
        {**_entry("m_active", 1), "depends_on": []},
        {**_entry("m_failed", 2), "depends_on": []},
        {**_entry("m_done", 3), "depends_on": []},
        {**_entry("m_wait1", 4), "depends_on": []},
        {**_entry("m_wait2", 5), "depends_on": []},
    ]
    states = {
        "m_active": "active",
        "m_failed": "failed",
        "m_done": "completed",
        "m_wait1": "queued",
        "m_wait2": "queued",
    }
    order: list[str] = []
    skipped: frozenset[str] = frozenset()
    for _ in range(len(entries)):  # bound: one call per entry, breaks on None
        picked = queue_board.next_mission(entries, states, set(), skipped, rule)
        if picked is None:
            break
        order.append(picked["mission_id"])
        skipped = frozenset({*skipped, picked["mission_id"]})
    assert order == ["m_active", "m_wait1", "m_wait2"]


@pytest.mark.parametrize("rule", [None, _dependency_rule()], ids=["flag-off", "flag-on"])
def test_active_mission_resumes_first_with_and_without_the_rule(rule: Any) -> None:
    entries = [
        _entry("m_queued", 1),
        {**_entry("m_waiting", 2), "depends_on": ["m_queued"]},
        _entry("m_active", 3),
    ]
    states = {"m_queued": "queued", "m_waiting": "queued", "m_active": "active"}
    picked = queue_board.next_mission(entries, states, set(), frozenset(), rule)
    assert picked is not None and picked["mission_id"] == "m_active"


def test_blocked_entries_listed_on_stderr_stdout_keeps_the_parsed_line(
    capsys: pytest.CaptureFixture[str],
) -> None:
    entries = [
        _entry("m_done", 1),
        {**_entry("m_after", 2), "depends_on": ["m_done"]},
        _entry("m_failed", 3),
        {**_entry("m_needs", 4), "depends_on": ["m_failed"]},
    ]
    states = {
        "m_done": "completed",
        "m_after": "queued",
        "m_failed": "failed",
        "m_needs": "queued",
    }
    live_states = [{"state_key": key, "status": status} for key, status in states.items()]
    rc_off = _run(FakeBoard(queue=entries, live_states=live_states), "next")
    out_off = capsys.readouterr().out
    rc_on = _run(
        FakeBoard(queue=_flag_on_queue(entries, states), live_states=live_states), "next"
    )
    captured_on = capsys.readouterr()
    assert rc_off == rc_on == queue_board.EXIT_FOUND
    assert captured_on.out == out_off == "m_after 3 0\n"
    assert captured_on.err == (
        "queue_board: blocked m_needs: blocked by m_failed: a dependency failed\n"
    )


class FakeResponse:
    def __init__(self, payload: Any) -> None:
        self._body = json.dumps(payload).encode("utf-8")

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *exc_info: object) -> None:
        return None

    def read(self) -> bytes:
        return self._body


class FakeBoard:
    """Stands in for urlopen on the loopback test base only; records every request."""

    def __init__(
        self,
        queue: list[dict[str, Any]] | None = None,
        live_states: list[dict[str, Any]] | None = None,
    ) -> None:
        self.queue = queue or []
        self.live_states = live_states or []
        self.queued_missions: list[str] = []
        self.version = "v7"
        self.down = False
        self.failing_queue_reads = 0
        self.write_error: Exception | None = None
        self.urls: list[str] = []
        self.writes: list[dict[str, Any]] = []

    def __call__(self, target: str | urllib.request.Request, timeout: float) -> FakeResponse:
        url = target.full_url if isinstance(target, urllib.request.Request) else target
        if not url.startswith(BASE + "/"):
            raise AssertionError(f"request outside the fake board: {url}")
        self.urls.append(url)
        if self.down:
            raise urllib.error.URLError("connection refused")
        if isinstance(target, urllib.request.Request):
            return self._write(target)
        if url == QUEUE_URL:
            if self.failing_queue_reads:
                self.failing_queue_reads -= 1
                raise urllib.error.URLError("connection reset")
            return FakeResponse({"queue": self.queue, "version": self.version})
        if url == LIVE_URL:
            return FakeResponse({"live_states": self.live_states})
        if url == MISSIONS_URL:
            return FakeResponse({"groups": {"queued": self.queued_missions}})
        raise AssertionError(f"unexpected read {url}")

    def _write(self, request: urllib.request.Request) -> FakeResponse:
        headers = {key.lower(): value for key, value in request.headers.items()}
        body = json.loads(request.data) if request.data is not None else None
        self.writes.append(
            {
                "method": request.get_method(),
                "url": request.full_url,
                "headers": headers,
                "body": body,
            }
        )
        if self.write_error is not None:
            raise self.write_error
        return FakeResponse({"version": "v8"})


def _http_error(code: int, payload: Any) -> urllib.error.HTTPError:
    body = io.BytesIO(json.dumps(payload).encode("utf-8"))
    return urllib.error.HTTPError(BASE, code, "error", {}, body)


def _run(board: FakeBoard, *argv: str) -> int:
    return queue_board.main(["--dashboard", BASE, *argv], opener=board)


def _two_entry_board() -> FakeBoard:
    return FakeBoard(queue=[_entry("m_a", 1), _entry("m_b", 2)])


def _only_write(board: FakeBoard) -> dict[str, Any]:
    assert len(board.writes) == 1
    return board.writes[0]


def _no_wait(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    delays: list[float] = []
    monkeypatch.setattr(queue_board, "time", SimpleNamespace(sleep=delays.append))
    return delays


def test_list_prints_entries_in_position_order(capsys: pytest.CaptureFixture[str]) -> None:
    board = FakeBoard(
        queue=[
            _entry("m_b", 2, enabled=False, attempts_per_task=5.0),
            _entry("m_a", 1, retry_failed=True),
        ]
    )
    assert _run(board, "list") == queue_board.EXIT_FOUND
    assert capsys.readouterr().out == "1\tm_a\tTrue\t\tTrue\t\n2\tm_b\tFalse\t5\tFalse\t\n"
    assert board.writes == []


def test_next_prints_the_mission_its_attempts_and_its_retry_flag(
    capsys: pytest.CaptureFixture[str]
) -> None:
    board = FakeBoard(
        queue=[_entry("m_a", 1, attempts_per_task=5.0, retry_failed=True)],
        live_states=[{"state_key": "m_a", "status": "queued"}],
    )
    assert _run(board, "next") == queue_board.EXIT_FOUND
    assert capsys.readouterr().out == "m_a 5 1\n"


def test_mission_that_never_ran_is_picked_from_the_queued_group(
    capsys: pytest.CaptureFixture[str]
) -> None:
    board = FakeBoard(queue=[_entry("m_new", 1, attempts_per_task=5.0)])
    board.queued_missions = ["m_new"]
    assert _run(board, "next") == queue_board.EXIT_FOUND
    assert capsys.readouterr().out == "m_new 5 0\n"


def test_skipped_mission_is_not_returned_again(capsys: pytest.CaptureFixture[str]) -> None:
    board = FakeBoard(
        queue=[_entry("m_skipped", 1), _entry("m_next", 2)],
        live_states=[
            {"state_key": "m_skipped", "status": "queued"},
            {"state_key": "m_next", "status": "queued"},
        ],
    )
    assert _run(board, "next", "--skip", "m_skipped") == queue_board.EXIT_FOUND
    assert capsys.readouterr().out == "m_next 3 0\n"


def test_skip_tokens_in_one_word_are_split(capsys: pytest.CaptureFixture[str]) -> None:
    board = FakeBoard(
        queue=[_entry("m_a", 1), _entry("m_b", 2), _entry("m_c", 3)],
        live_states=[
            {"state_key": "m_a", "status": "queued"},
            {"state_key": "m_b", "status": "queued"},
            {"state_key": "m_c", "status": "queued"},
        ],
    )
    assert _run(board, "next", "--skip", " m_a m_b") == queue_board.EXIT_FOUND
    assert capsys.readouterr().out == "m_c 3 0\n"


@pytest.mark.parametrize(
    ("queue", "live_states"),
    [
        ([], []),
        ([_entry("m_a", 1)], [{"state_key": "m_a", "status": "completed"}]),
    ],
    ids=["empty-queue", "all-completed"],
)
def test_next_is_empty_when_nothing_is_due(
    queue: list[dict[str, Any]],
    live_states: list[dict[str, Any]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    board = FakeBoard(queue=queue, live_states=live_states)
    assert _run(board, "next") == queue_board.EXIT_EMPTY
    assert capsys.readouterr().out == ""


def test_next_exits_three_after_three_tries_when_the_board_is_unreachable(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    delays = _no_wait(monkeypatch)
    board = _two_entry_board()
    board.down = True
    assert _run(board, "next") == queue_board.EXIT_BOARD_UNAVAILABLE
    assert board.urls.count(QUEUE_URL) == 3
    assert delays == [queue_board.RETRY_DELAY_SECONDS] * 2
    captured = capsys.readouterr()
    assert "board unavailable after 3 tries" in captured.err
    assert captured.out == ""


def test_next_retries_a_failed_read_before_answering(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    delays = _no_wait(monkeypatch)
    board = FakeBoard(
        queue=[_entry("m_a", 1)],
        live_states=[{"state_key": "m_a", "status": "queued"}],
    )
    board.failing_queue_reads = 1
    assert _run(board, "next") == queue_board.EXIT_FOUND
    assert board.urls.count(QUEUE_URL) == 2
    assert delays == [queue_board.RETRY_DELAY_SECONDS]
    assert capsys.readouterr().out == "m_a 3 0\n"


def test_add_after_inserts_behind_the_named_mission(capsys: pytest.CaptureFixture[str]) -> None:
    board = _two_entry_board()
    rc = _run(board, "add", "m_c", "--after", "m_a", "--attempts", "5", "--retry-failed")
    write = _only_write(board)
    assert rc == queue_board.EXIT_OK
    assert write["method"] == "POST"
    assert write["url"] == BASE + "/api/board/queue/add"
    assert write["headers"]["x-ahawr-dashboard"] == "1"
    assert write["body"] == {
        "mission_id": "m_c",
        "expected_version": "v7",
        "retry_failed": True,
        "attempts_per_task": 5.0,
        "position": 2,
    }
    assert capsys.readouterr().out == "queue_board: add m_c done, queue version v8\n"


def test_add_after_a_mission_not_in_the_queue_is_refused(
    capsys: pytest.CaptureFixture[str],
) -> None:
    board = _two_entry_board()
    assert _run(board, "add", "m_c", "--after", "m_x") == queue_board.EXIT_REFUSED
    assert board.writes == []
    assert "not in the queue" in capsys.readouterr().err


def test_first_sends_the_mission_to_the_front(capsys: pytest.CaptureFixture[str]) -> None:
    board = _two_entry_board()
    assert _run(board, "first", "m_b") == queue_board.EXIT_OK
    write = _only_write(board)
    assert write["method"] == "POST"
    assert write["url"] == BASE + "/api/board/queue/first"
    assert write["headers"]["x-ahawr-dashboard"] == "1"
    assert write["body"] == {"mission_id": "m_b", "expected_version": "v7"}
    assert capsys.readouterr().out == "queue_board: first m_b done, queue version v8\n"


def test_remove_deletes_the_mission_route_without_a_version_read() -> None:
    board = _two_entry_board()
    assert _run(board, "remove", "m_a") == queue_board.EXIT_OK
    write = _only_write(board)
    assert write["method"] == "DELETE"
    assert write["url"] == BASE + "/api/board/queue/m_a"
    assert write["headers"]["x-ahawr-dashboard"] == "1"
    assert write["body"] is None
    assert QUEUE_URL not in board.urls


def test_move_sends_the_target_position() -> None:
    board = _two_entry_board()
    assert _run(board, "move", "m_b", "1") == queue_board.EXIT_OK
    write = _only_write(board)
    assert write["method"] == "POST"
    assert write["url"] == BASE + "/api/board/queue/move"
    assert write["headers"]["x-ahawr-dashboard"] == "1"
    assert write["body"] == {"mission_id": "m_b", "to_position": 1, "expected_version": "v7"}


@pytest.mark.parametrize(
    "argv", [("add", "m_c"), ("first", "m_b"), ("move", "m_b", "1")], ids=["add", "first", "move"]
)
def test_conflict_answer_exits_four(
    argv: tuple[str, ...], capsys: pytest.CaptureFixture[str]
) -> None:
    board = _two_entry_board()
    board.write_error = _http_error(409, {"error": {"message": "queue changed"}})
    assert _run(board, *argv) == queue_board.EXIT_CONFLICT
    err = capsys.readouterr().err
    assert "conflict (HTTP 409)" in err
    assert "queue changed" in err


def test_refused_write_exits_five(capsys: pytest.CaptureFixture[str]) -> None:
    board = _two_entry_board()
    board.write_error = _http_error(400, {"error": "bad body"})
    assert _run(board, "add", "m_c") == queue_board.EXIT_REFUSED
    assert "refused (HTTP 400): bad body" in capsys.readouterr().err


def test_lost_write_answer_exits_three(capsys: pytest.CaptureFixture[str]) -> None:
    board = _two_entry_board()
    board.write_error = urllib.error.URLError("connection reset")
    assert _run(board, "first", "m_b") == queue_board.EXIT_BOARD_UNAVAILABLE
    assert len(board.writes) == 1
    assert "connection reset" in capsys.readouterr().err


def _sync_row(
    mission_id: str, position: int, retry_failed: bool, attempts_per_task: int | None
) -> dict[str, Any]:
    return {
        "position": position,
        "mission_id": mission_id,
        "enabled": True,
        "retry_failed": retry_failed,
        "attempts_per_task": attempts_per_task,
    }


def test_sync_from_shell_sends_the_entries_the_shell_would_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    shell = tmp_path / "real_queue.sh"
    shell.write_text(
        'QUEUE="m_a m_b m_c m_d m_e m_gone"\n'
        'ATTEMPTS5="m_b"\n'
        'ATTEMPTS10="m_c"\n'
        'RETRY_FAILED="m_d"\n',
        encoding="utf-8",
    )
    states = {
        "m_a": "completed",
        "m_b": "queued",
        "m_c": "queued",
        "m_d": "failed",
        "m_e": "failed",
    }
    enabled = ["m_a", "m_b", "m_c", "m_d", "m_e"]

    monkeypatch.syspath_prepend(str(OPS_DIR))
    pgdb = importlib.import_module("pgdb")

    def fake_q(sql: str, params: Any = None) -> list[dict[str, str]]:
        if "state_key" in sql:
            return [{"k": key, "s": status} for key, status in states.items()]
        return [{"m": mission} for mission in enabled]

    monkeypatch.setattr(pgdb, "q", fake_q)
    board = FakeBoard(queue=[_entry("m_old", 1)])

    assert _run(board, "sync-from-shell", "--file", str(shell)) == queue_board.EXIT_OK
    write = _only_write(board)
    assert write["method"] == "POST"
    assert write["url"] == BASE + "/api/board/queue"
    assert write["headers"]["x-ahawr-dashboard"] == "1"
    assert write["body"] == {
        "entries": [
            _sync_row("m_b", 0, retry_failed=False, attempts_per_task=5),
            _sync_row("m_c", 1, retry_failed=False, attempts_per_task=10),
            _sync_row("m_d", 2, retry_failed=True, attempts_per_task=None),
        ],
        "expected_version": "v7",
    }
    assert capsys.readouterr().out == "queue_board: sync-from-shell done, queue version v8\n"


# --- relaunch of a mission whose Worker run ended by the runner time limit (2026-11-15) ---

RUNNER = "http://127.0.0.1:8"


class _Reply:
    def __init__(self, payload: Any) -> None:
        self.payload = payload

    def __enter__(self) -> _Reply:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


def _runner_opener(runs: dict[str, dict[str, Any]]) -> Any:
    def opener(url: str, timeout: float = 0) -> _Reply:
        run_id = url.removeprefix(RUNNER + "/v1/runs/")
        if run_id not in runs:
            raise urllib.error.URLError("unknown run")
        return _Reply(runs[run_id])

    return opener


def _live(mission_id: str, run_id: str, task_index: float = 5.0) -> dict[str, Any]:
    return {"state_key": mission_id, "worker_run_id": run_id, "task_index": task_index}


def test_timed_out_failed_mission_is_relaunched_with_its_counter_key() -> None:
    entries = [_entry("m_timeout", 1), _entry("m_other", 2)]
    states = {"m_timeout": "failed", "m_other": "failed"}
    live = [_live("m_timeout", "run_t"), _live("m_other", "run_o")]
    runs = {"run_t": {"error": {"code": "timeout"}}, "run_o": {"error": {"code": "cli_error"}}}
    found = queue_board.timeout_relaunches(
        entries, states, live, _runner_opener(runs), runner_url=RUNNER
    )
    assert found == {"m_timeout": "m_timeout:5.0"}


def test_timeout_relaunch_stops_after_the_limit_and_skips_flagged_or_active() -> None:
    entries = [_entry("m_a", 1), _entry("m_b", 2, retry_failed=True), _entry("m_c", 3)]
    states = {"m_a": "failed", "m_b": "failed", "m_c": "active"}
    live = [_live("m_a", "run_a"), _live("m_b", "run_b"), _live("m_c", "run_c")]
    runs = {run: {"error": {"code": "timeout"}} for run in ("run_a", "run_b", "run_c")}
    opener = _runner_opener(runs)
    counts = {"m_a:5.0": queue_board.MAX_TIMEOUT_RELAUNCHES}
    assert queue_board.timeout_relaunches(
        entries, states, live, opener, runner_url=RUNNER, counts=counts
    ) == {}


def test_unreachable_runner_does_not_make_a_mission_eligible(
    capsys: pytest.CaptureFixture[str],
) -> None:
    entries = [_entry("m_a", 1)]
    found = queue_board.timeout_relaunches(
        entries, {"m_a": "failed"}, [_live("m_a", "run_a")], _runner_opener({}), runner_url=RUNNER
    )
    assert found == {}
    assert "m_a skipped" in capsys.readouterr().err
