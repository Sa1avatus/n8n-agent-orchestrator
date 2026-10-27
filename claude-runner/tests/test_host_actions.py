from __future__ import annotations

import itertools
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from claude_runner.host_actions import (
    CLAIM_TIMEOUT_MESSAGE,
    CLAIM_TIMEOUT_SECONDS,
    DEFAULT_WHITELIST,
    FINISHED_RETAIN_HOURS,
    HOST_TRANSITIONS,
    MAX_MESSAGE_CHARS,
    MAX_PENDING_HOURS,
    MAX_REASON_CHARS,
    MAX_REQUESTS,
    MIN_SECONDS_BETWEEN_ACTIONS,
    ActiveRun,
    HostActionFacts,
    HostActionRequest,
    HostActionStore,
    HostActionStoreFullError,
    HostActionTransitionError,
    WhitelistError,
    decide,
    host_actions_enabled,
    load_whitelist,
    parse_whitelist,
    transition,
    validate_request,
)

DOC = Path(__file__).resolve().parents[1] / "docs" / "host-actions.md"
STATES = ("pending", "confirmed", "running", "done", "failed", "rejected", "expired")
ILLEGAL_TRANSITIONS = [
    (source, target)
    for source, target in itertools.product(STATES, STATES)
    if (source, target) not in HOST_TRANSITIONS
]
START = datetime(2026, 1, 1, tzinfo=UTC)


class Clock:
    def __init__(self) -> None:
        self.now = START

    def __call__(self) -> str:
        return self.now.isoformat()

    def advance(self, **delta: float) -> None:
        self.now += timedelta(**delta)


def _store(tmp_path: Path, clock: Clock) -> HostActionStore:
    return HostActionStore(str(tmp_path / "host_actions.json"), clock=clock)


def _request(state: str) -> HostActionRequest:
    return HostActionRequest(
        id="ha1", action="recreate", service="ahawr-qa", reason="r", tier="AUTO",
        state=state, created_at=START.isoformat(), updated_at=START.isoformat(),
        requested_by="worker",
    )


def _state(store: HostActionStore, request_id: str) -> str:
    request = store.get(request_id)
    assert request is not None
    return request.state


def _doc_whitelist() -> dict[str, object]:
    text = DOC.read_text(encoding="utf-8")
    block = text.split("```json\n", 1)[1].split("```", 1)[0]
    return json.loads(block)


def _write_whitelist(tmp_path: Path, data: object) -> Path:
    path = tmp_path / "whitelist.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_default_whitelist_equals_doc_json() -> None:
    assert parse_whitelist(_doc_whitelist()) == DEFAULT_WHITELIST


def test_missing_file_gives_default(tmp_path: Path) -> None:
    assert load_whitelist(tmp_path / "absent.json") == DEFAULT_WHITELIST


def test_unknown_action_and_service_name_the_value() -> None:
    with pytest.raises(WhitelistError, match="'rebuild'"):
        validate_request("rebuild", "ahawr-qa")
    with pytest.raises(WhitelistError, match="'ahawr-db'"):
        validate_request("recreate", "ahawr-db")
    with pytest.raises(WhitelistError, match="'ahawr-postgres' is not whitelisted"):
        validate_request("recreate", "ahawr-postgres")


def test_validate_request_returns_tier() -> None:
    assert validate_request("recreate", "ahawr-qa") == "AUTO"
    assert validate_request("restart", "n8n") == "CONFIRM"


@pytest.mark.parametrize(
    ("service", "tier"),
    [("claude-runner", "CONFIRM"), ("n8n", "CONFIRM"), ("ahawr-dashboard", "AUTO")],
)
def test_named_services_have_their_tier(service: str, tier: str) -> None:
    assert validate_request("restart", service) == tier


def test_n8n_service_maps_to_autonomous_agents_container() -> None:
    assert DEFAULT_WHITELIST.services["n8n"].container == "n8n-autonomous-agents"


def test_llama_server_is_refused_as_unknown_service() -> None:
    with pytest.raises(WhitelistError, match="unknown service 'llama'"):
        validate_request("restart", "llama")


def test_unknown_tier_in_file_is_readable(tmp_path: Path) -> None:
    data = _doc_whitelist()
    data["services"]["n8n"]["tier"] = "MAYBE"  # type: ignore[index]
    path = tmp_path / "whitelist.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(WhitelistError, match="service 'n8n' has unknown tier 'MAYBE'"):
        load_whitelist(path)


def test_whitelist_file_that_is_not_json_gives_readable_error(tmp_path: Path) -> None:
    path = tmp_path / "whitelist.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(WhitelistError, match="not valid JSON"):
        load_whitelist(path)


def test_whitelist_file_with_unknown_action_is_readable_error(tmp_path: Path) -> None:
    data = _doc_whitelist()
    data["actions"]["rebuild"] = "make"  # type: ignore[index]
    with pytest.raises(WhitelistError, match="unknown action 'rebuild'"):
        load_whitelist(_write_whitelist(tmp_path, data))


def test_whitelist_service_without_container_is_readable_error(tmp_path: Path) -> None:
    data = _doc_whitelist()
    del data["services"]["n8n"]["container"]  # type: ignore[index]
    with pytest.raises(WhitelistError, match="service 'n8n' container must be a non-empty string"):
        load_whitelist(_write_whitelist(tmp_path, data))


def test_flag_is_off_unless_one_or_true() -> None:
    assert host_actions_enabled({}) is False
    assert host_actions_enabled({"HOST_ACTIONS": "0"}) is False
    assert host_actions_enabled({"HOST_ACTIONS": "1"}) is True
    assert host_actions_enabled({"HOST_ACTIONS": "true"}) is True


@pytest.mark.parametrize(("source", "target"), sorted(HOST_TRANSITIONS))
def test_legal_transition_moves_state_and_stamps_time(source: str, target: str) -> None:
    request = _request(source)
    at = (START + timedelta(minutes=1)).isoformat()
    transition(request, target, at)
    assert (request.state, request.updated_at) == (target, at)


@pytest.mark.parametrize(("source", "target"), ILLEGAL_TRANSITIONS)
def test_illegal_transition_is_refused_and_leaves_request_unchanged(
    source: str, target: str
) -> None:
    request = _request(source)
    with pytest.raises(HostActionTransitionError, match=f"from {source} to {target}"):
        transition(request, target, START.isoformat())
    assert (request.state, request.updated_at) == (source, START.isoformat())


def test_create_refuses_unlisted_service_and_unknown_action(tmp_path: Path) -> None:
    store = _store(tmp_path, Clock())
    with pytest.raises(WhitelistError, match="'ahawr-postgres' is not whitelisted"):
        store.create("recreate", "ahawr-postgres", "r", "worker")
    with pytest.raises(WhitelistError, match="unknown service 'llama'"):
        store.create("restart", "llama", "r", "worker")
    with pytest.raises(WhitelistError, match="unknown action 'rebuild'"):
        store.create("rebuild", "ahawr-qa", "r", "worker")
    assert store.list_requests() == []


def test_create_stores_tier_from_whitelist(tmp_path: Path) -> None:
    store = _store(tmp_path, Clock())
    assert store.create("restart", "claude-runner", "r", "worker").tier == "CONFIRM"
    assert store.create("recreate", "ahawr-dashboard", "r", "worker").tier == "AUTO"


def test_pending_request_expires_only_after_max_pending_hours(tmp_path: Path) -> None:
    clock = Clock()
    store = _store(tmp_path, clock)
    request = store.create("recreate", "ahawr-qa", "r", "worker")
    clock.advance(hours=MAX_PENDING_HOURS)
    assert _state(store, request.id) == "pending"
    clock.advance(seconds=1)
    assert _state(store, request.id) == "expired"


def test_running_request_is_failed_after_claim_timeout(tmp_path: Path) -> None:
    clock = Clock()
    store = _store(tmp_path, clock)
    request = store.create("restart", "n8n", "r", "worker")
    store.confirm(request.id)
    claimed = store.claim()
    assert claimed is not None and claimed.id == request.id
    clock.advance(seconds=CLAIM_TIMEOUT_SECONDS)
    assert _state(store, request.id) == "running"
    clock.advance(seconds=1)
    failed = store.get(request.id)
    assert failed is not None
    assert (failed.state, failed.ok, failed.message) == ("failed", False, CLAIM_TIMEOUT_MESSAGE)


def test_store_refuses_request_when_full_of_unfinished_requests(tmp_path: Path) -> None:
    store = _store(tmp_path, Clock())
    for _ in range(MAX_REQUESTS):
        store.create("recreate", "ahawr-qa", "r", "worker")
    with pytest.raises(HostActionStoreFullError, match=str(MAX_REQUESTS)):
        store.create("recreate", "ahawr-qa", "r", "worker")
    assert len(store.list_requests()) == MAX_REQUESTS


def test_finished_request_is_pruned_after_retain_hours(tmp_path: Path) -> None:
    clock = Clock()
    store = _store(tmp_path, clock)
    request = store.create("recreate", "ahawr-qa", "r", "worker")
    store.confirm(request.id)
    store.claim()
    store.report(request.id, True, "")
    clock.advance(hours=FINISHED_RETAIN_HOURS)
    assert store.get(request.id) is not None
    clock.advance(seconds=1)
    assert store.get(request.id) is None


def test_report_message_is_truncated(tmp_path: Path) -> None:
    store = _store(tmp_path, Clock())
    request = store.create("recreate", "ahawr-qa", "r", "worker")
    store.confirm(request.id)
    store.claim()
    reported = store.report(request.id, True, "m" * (MAX_MESSAGE_CHARS + 1))
    assert len(reported.message) == MAX_MESSAGE_CHARS


def test_reason_is_truncated_at_create(tmp_path: Path) -> None:
    store = _store(tmp_path, Clock())
    request = store.create("recreate", "ahawr-qa", "r" * (MAX_REASON_CHARS + 1), "worker")
    assert len(request.reason) == MAX_REASON_CHARS


def _facts(**changes: Any) -> HostActionFacts:
    base = HostActionFacts(
        active_runs=(),
        queue_at_boundary=True,
        queue_paused=False,
        last_action_at={},
        requester_run_id=None,
        min_seconds_between_actions=MIN_SECONDS_BETWEEN_ACTIONS,
    )
    return replace(base, **changes)


def _waiting(state: str = "confirmed", tier: str = "AUTO") -> HostActionRequest:
    return replace(_request(state), tier=tier)


NOW = START.isoformat()


@pytest.mark.parametrize(("state", "tier"), [("pending", "AUTO"), ("confirmed", "AUTO")])
def test_decide_allows_auto_tier_pending_or_confirmed(state: str, tier: str) -> None:
    assert decide(_waiting(state, tier), _facts(), NOW) == (True, "may run")


def test_decide_refuses_confirm_tier_until_operator_confirms() -> None:
    assert decide(_waiting("pending", "CONFIRM"), _facts(), NOW) == (
        False,
        "CONFIRM tier needs the operator to confirm the request first",
    )


def test_decide_allows_confirm_tier_once_confirmed() -> None:
    assert decide(_waiting("confirmed", "CONFIRM"), _facts(), NOW) == (True, "may run")


def test_decide_refuses_tier_not_whitelisted() -> None:
    assert decide(_waiting("confirmed", "NOT WHITELISTED"), _facts(), NOW) == (
        False,
        "service 'ahawr-qa' is not whitelisted",
    )


@pytest.mark.parametrize("state", ["running", "done", "failed", "rejected"])
def test_decide_refuses_request_not_waiting_to_run(state: str) -> None:
    assert decide(_waiting(state), _facts(), NOW) == (
        False,
        f"request is {state}, not waiting to run",
    )


def test_decide_refuses_state_expired() -> None:
    assert decide(_waiting("expired"), _facts(), NOW) == (False, "request has expired")


def test_decide_allows_pending_at_max_pending_hours() -> None:
    at = START + timedelta(hours=MAX_PENDING_HOURS)
    assert decide(_waiting("pending"), _facts(), at.isoformat()) == (True, "may run")


def test_decide_never_runs_pending_past_max_pending_hours() -> None:
    at = START + timedelta(hours=MAX_PENDING_HOURS, seconds=1)
    assert decide(_waiting("pending"), _facts(), at.isoformat()) == (
        False,
        "request has expired",
    )


@pytest.mark.parametrize("role", ["worker", "reviewer", "qa"])
def test_decide_refuses_while_blocking_run_is_active(role: str) -> None:
    facts = _facts(active_runs=(ActiveRun(run_id="run-1", role=role),))
    assert decide(_waiting(), facts, NOW) == (False, f"{role} run run-1 is active")


def test_decide_allows_when_no_blocking_run_is_active() -> None:
    assert decide(_waiting(), _facts(active_runs=()), NOW) == (True, "may run")


def test_decide_refuses_queue_mid_task_and_not_paused() -> None:
    facts = _facts(queue_at_boundary=False, queue_paused=False)
    assert decide(_waiting(), facts, NOW) == (
        False,
        "queue is in the middle of a task: not at a task boundary and not paused",
    )


def test_decide_allows_queue_paused_mid_task() -> None:
    facts = _facts(queue_at_boundary=False, queue_paused=True)
    assert decide(_waiting(), facts, NOW) == (True, "may run")


def test_decide_allows_queue_at_task_boundary() -> None:
    facts = _facts(queue_at_boundary=True, queue_paused=False)
    assert decide(_waiting(), facts, NOW) == (True, "may run")


def test_decide_refuses_action_just_under_min_seconds_between_actions() -> None:
    last = START.isoformat()
    under = START + timedelta(seconds=MIN_SECONDS_BETWEEN_ACTIONS - 1)
    facts = _facts(last_action_at={"ahawr-qa": last})
    assert decide(_waiting(), facts, under.isoformat()) == (
        False,
        f"'ahawr-qa' had an action less than {MIN_SECONDS_BETWEEN_ACTIONS} seconds ago",
    )


def test_decide_allows_action_just_over_min_seconds_between_actions() -> None:
    last = START.isoformat()
    over = START + timedelta(seconds=MIN_SECONDS_BETWEEN_ACTIONS + 1)
    facts = _facts(last_action_at={"ahawr-qa": last})
    assert decide(_waiting(), facts, over.isoformat()) == (True, "may run")


def test_decide_min_seconds_gap_is_per_service() -> None:
    last = START.isoformat()
    facts = _facts(last_action_at={"ahawr-dashboard": last})
    assert decide(_waiting(), facts, NOW) == (True, "may run")


def test_decide_refuses_claude_runner_recreate_while_requester_runs() -> None:
    request = replace(_waiting("confirmed", "CONFIRM"), service="claude-runner")
    facts = _facts(
        active_runs=(ActiveRun(run_id="run-7", role="worker"),), requester_run_id="run-7"
    )
    assert decide(request, facts, NOW) == (
        False,
        "claude-runner recreate waits: the requesting run is still active",
    )


def test_decide_allows_claude_runner_recreate_when_requester_is_done() -> None:
    request = replace(_waiting("confirmed", "CONFIRM"), service="claude-runner")
    facts = _facts(requester_run_id="run-7")
    assert decide(request, facts, NOW) == (True, "may run")
