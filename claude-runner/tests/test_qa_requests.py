from __future__ import annotations

import itertools
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from claude_runner.qa_requests import (
    QA_AGENT_FLAG,
    QA_AGENT_LAYER,
    QA_CLAIM_WAIT_MINUTES,
    QA_MAX_STORED_REQUESTS,
    QA_RUN_BOUND_MINUTES,
    QA_TRANSITIONS,
    QA_UI_FILE_PATTERN,
    QaRequest,
    QaRequestStore,
    QaTransitionError,
    request_layers,
    transition,
)
from claude_runner.roles_config import QA_DEFAULT_LAYERS

STATES = ("queued", "running", "done", "error", "timeout")
ILLEGAL_TRANSITIONS = [
    (source, target)
    for source, target in itertools.product(STATES, STATES)
    if (source, target) not in QA_TRANSITIONS
]
START = datetime(2026, 1, 1, tzinfo=UTC)


class Clock:
    def __init__(self) -> None:
        self.now = START

    def __call__(self) -> str:
        return self.now.isoformat()

    def advance(self, **delta: float) -> None:
        self.now += timedelta(**delta)


def _store(tmp_path: Path, clock: Clock) -> QaRequestStore:
    return QaRequestStore(str(tmp_path / "qa_requests.json"), clock=clock)


def _request(status: str) -> QaRequest:
    return QaRequest("r1", "m1", "/w", "final", status, "n1", START.isoformat())


@pytest.mark.parametrize(
    ("source", "target"),
    sorted(QA_TRANSITIONS),
)
def test_legal_transition_moves_status_and_stamps_time(source: str, target: str) -> None:
    request = _request(source)
    at = START + timedelta(minutes=1)
    transition(request, target, at.isoformat())
    assert request.status == target
    if target == "running":
        assert (request.claimed_at, request.finished_at) == (at.isoformat(), None)
    else:
        assert (request.claimed_at, request.finished_at) == (None, at.isoformat())


@pytest.mark.parametrize(("source", "target"), ILLEGAL_TRANSITIONS)
def test_illegal_transition_is_refused_and_leaves_request_unchanged(
    source: str, target: str
) -> None:
    request = _request(source)
    with pytest.raises(QaTransitionError, match=f"{source} to {target}"):
        transition(request, target, START.isoformat())
    assert (request.status, request.claimed_at, request.finished_at) == (source, None, None)


def test_create_returns_same_request_for_same_mission_and_nonce(tmp_path: Path) -> None:
    store = _store(tmp_path, Clock())
    first = store.create("m1", "/w", "final", "n1")
    assert store.create("m1", "/w", "final", "n1").id == first.id
    assert len(store.list_requests()) == 1


def test_create_with_new_nonce_makes_new_request(tmp_path: Path) -> None:
    store = _store(tmp_path, Clock())
    first = store.create("m1", "/w", "final", "n1")
    second = store.create("m1", "/w", "final", "n2")
    assert second.id != first.id
    assert len(store.list_requests()) == 2


def test_queued_request_expires_only_after_claim_wait(tmp_path: Path) -> None:
    clock = Clock()
    store = _store(tmp_path, clock)
    request = store.create("m1", "/w", "ui_tasks", "n1")
    clock.advance(minutes=QA_CLAIM_WAIT_MINUTES)
    assert store.get(request.id).status == "queued"  # type: ignore[union-attr]
    clock.advance(seconds=1)
    assert store.get(request.id).status == "timeout"  # type: ignore[union-attr]


def test_running_request_expires_after_run_bound(tmp_path: Path) -> None:
    clock = Clock()
    store = _store(tmp_path, clock)
    request = store.create("m1", "/w", "final", "n1")
    store.claim(request.id)
    clock.advance(minutes=QA_RUN_BOUND_MINUTES, seconds=1)
    assert store.get(request.id).status == "timeout"  # type: ignore[union-attr]


def test_finish_after_run_bound_is_refused(tmp_path: Path) -> None:
    clock = Clock()
    store = _store(tmp_path, clock)
    request = store.create("m1", "/w", "final", "n1")
    store.claim(request.id)
    clock.advance(minutes=QA_RUN_BOUND_MINUTES + 1)
    with pytest.raises(QaTransitionError):
        store.finish(request.id, ["pass"], "")
    assert store.get(request.id).status == "timeout"  # type: ignore[union-attr]


def test_store_keeps_newest_requests_at_bound(tmp_path: Path) -> None:
    store = _store(tmp_path, Clock())
    created = [
        store.create("m1", "/w", "final", f"n{index}")
        for index in range(QA_MAX_STORED_REQUESTS + 5)
    ]
    kept = created[-QA_MAX_STORED_REQUESTS:]
    assert [r.id for r in store.list_requests()] == [r.id for r in kept]


def test_store_drops_finished_request_before_queued_one(tmp_path: Path) -> None:
    store = _store(tmp_path, Clock())
    finished = store.create("m0", "/w", "final", "n0")
    store.claim(finished.id)
    store.finish(finished.id, ["pass"], "")
    queued = store.create("m1", "/w", "final", "n1")
    for index in range(QA_MAX_STORED_REQUESTS - 1):
        store.create("m2", "/w", "final", f"n{index}")
    ids = [r.id for r in store.list_requests()]
    assert len(ids) == QA_MAX_STORED_REQUESTS
    assert finished.id not in ids
    assert queued.id in ids


def test_store_round_trips_through_json_file(tmp_path: Path) -> None:
    clock = Clock()
    store = _store(tmp_path, clock)
    request = store.create("m1", "/w", "final", "n1")
    store.claim(request.id)
    store.finish(request.id, ["pass", "fail"], "")
    reloaded = _store(tmp_path, clock).get(request.id)
    assert reloaded == store.get(request.id)
    assert reloaded is not None and reloaded.verdicts == ["pass", "fail"]


def test_ui_file_pattern_is_non_empty() -> None:
    assert QA_UI_FILE_PATTERN


def test_old_request_without_layers_reads_as_default_and_omits_key(tmp_path: Path) -> None:
    store = _store(tmp_path, Clock())
    request = store.create("m1", "/w", "final", "n1")
    assert request.layers is None
    assert request_layers(request) == list(QA_DEFAULT_LAYERS) == ["ui"]
    assert "layers" not in store.request_dict(request)


def test_valid_layers_are_stored_and_returned(tmp_path: Path) -> None:
    store = _store(tmp_path, Clock())
    request = store.create("m1", "/w", "final", "n1", layers=["regression", "ui"])
    assert request.layers == ["regression", "ui"]
    assert request_layers(request) == ["regression", "ui"]
    assert store.request_dict(request)["layers"] == ["regression", "ui"]
    reloaded = _store(tmp_path, Clock()).get(request.id)
    assert reloaded is not None and reloaded.layers == ["regression", "ui"]


def test_unknown_layer_is_refused(tmp_path: Path) -> None:
    store = _store(tmp_path, Clock())
    with pytest.raises(ValueError, match="unknown QA layer: bogus"):
        store.create("m1", "/w", "final", "n1", layers=["bogus"])
    assert store.list_requests() == []


def test_golden_old_payload_keys_and_round_trip(tmp_path: Path) -> None:
    store = _store(tmp_path, Clock())
    request = store.create("m1", "/w", "final", "n1")
    keys = {
        "id",
        "mission_id",
        "working_directory",
        "mode",
        "status",
        "nonce",
        "created_at",
        "claimed_at",
        "finished_at",
        "verdicts",
        "error",
    }
    assert set(store.request_dict(request)) == keys
    store.claim(request.id)
    store.finish(request.id, ["pass", "fail"], "")
    raw = store.request_dict(store.get(request.id) or request)
    assert "layers" not in raw
    reloaded = _store(tmp_path, Clock()).get(request.id)
    assert reloaded == store.get(request.id)
    assert reloaded is not None and reloaded.layers is None
    assert store.request_dict(reloaded) == raw


def _serialised(store: QaRequestStore, request_id: str) -> str:
    found = store.get(request_id)
    if found is None:
        raise AssertionError(f"request {request_id} not found in store")
    return json.dumps(store.request_dict(found), sort_keys=True)


def test_pinned_serialised_request_and_result(tmp_path: Path) -> None:
    clock = Clock()
    store = _store(tmp_path, clock)
    request = store.create("m1", "/w", "final", "n1")
    clock.advance(seconds=1)
    store.claim(request.id)
    clock.advance(seconds=1)
    store.finish(request.id, ["pass", "fail"], "")
    pinned = _serialised(store, request.id)
    assert '"layers"' not in pinned
    assert '"agent"' not in pinned
    assert '"qa_agent"' not in pinned
    assert '"status": "done"' in pinned
    assert '"verdicts": ["pass", "fail"]' in pinned
    assert f'"id": "{request.id}"' in pinned


def test_flag_off_agent_layer_is_rejected_and_output_matches_pinned(tmp_path: Path) -> None:
    clock = Clock()
    store = _store(tmp_path, clock)
    request = store.create("m1", "/w", "final", "n1")
    with pytest.raises(ValueError, match="unknown QA layer: agent"):
        store.create("m1", "/w", "final", "n2", layers=["agent"])
    assert store.list_requests() == [request]
    clock.advance(seconds=1)
    store.claim(request.id)
    clock.advance(seconds=1)
    store.finish(request.id, ["pass", "fail"], "")
    fresh = _store(tmp_path, clock)
    assert _serialised(store, request.id) == _serialised(fresh, request.id)


def test_flag_on_agent_layer_is_accepted(tmp_path: Path) -> None:
    store = _store(tmp_path, Clock())
    request = store.create(
        "m1", "/w", "final", "n1", layers=[QA_AGENT_LAYER], options={QA_AGENT_FLAG: True}
    )
    assert request.layers == ["agent"]
    assert request_layers(request) == ["agent"]
    assert store.request_dict(request)["layers"] == ["agent"]
    assert '"layers": ["agent"]' in _serialised(store, request.id)
