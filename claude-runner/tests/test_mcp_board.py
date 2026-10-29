from __future__ import annotations

import json
from typing import Any

import pytest

from claude_runner.board import InMemoryBoardStore, Mission, QueueEntry
from claude_runner.mcp_board import call_tool, read_message

PLAN = {
    "tasks": [
        {"id": "T001", "title": "one", "objective": "o1"},
        {"id": "T002", "title": "two", "objective": "o2"},
    ]
}


def _board() -> InMemoryBoardStore:
    store = InMemoryBoardStore()
    store._missions["m1"] = Mission(mission_id="m1", state_namespace="m1")
    store.update_mission("m1", None, name="Mission")
    store.set_queue([QueueEntry(position=0, mission_id="m1")], expected_version=None)
    store.upsert_live_state(
        "m1",
        None,
        plan=PLAN,
        status="active",
        task_index=0,
        task_attempt=1,
        who="seed",
    )
    return store


def _line(message: dict[str, Any]) -> bytes:
    return (json.dumps(message) + "\n").encode()


def _request(request_id: int, method: str, params: dict[str, Any] | None = None) -> bytes:
    message: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        message["params"] = params
    return _line(message)


def _call(
    store: InMemoryBoardStore, request_id: int, name: str, arguments: dict[str, Any]
) -> dict[str, Any]:
    reply = read_message(
        _request(request_id, "tools/call", {"name": name, "arguments": arguments}), store
    )
    assert reply is not None
    return reply


def _text(reply: dict[str, Any]) -> str:
    content = reply["result"]["content"]
    assert isinstance(content, list)
    item = content[0]
    assert isinstance(item, dict)
    return str(item["text"])


def test_initialize_reports_protocol_version_and_tools_capability() -> None:
    store = _board()
    reply = read_message(_request(1, "initialize", {}), store)

    assert reply is not None
    assert reply["result"]["protocolVersion"] == "2024-11-05"
    assert reply["result"]["capabilities"] == {"tools": {}}
    assert reply["result"]["serverInfo"]["name"] == "ahawr-board"


def test_tools_list_publishes_the_write_schemas_with_required_version() -> None:
    store = _board()
    reply = read_message(_request(2, "tools/list"), store)

    assert reply is not None
    tools = reply["result"]["tools"]
    by_name = {tool["name"]: tool["inputSchema"] for tool in tools}
    assert by_name["task_edit"]["required"] == ["mission_id", "task_key", "expected_version"]
    assert by_name["add_hint"]["required"] == ["mission_id", "task_key", "expected_version"]
    assert by_name["queue_move"]["required"] == ["mission_id", "to_position", "expected_version"]
    assert by_name["enable_mission"]["required"] == ["mission_id", "enabled", "expected_version"]
    assert by_name["pause_after_current"] == {"type": "object", "properties": {}}
    assert by_name["dangerous_action_help"] == {"type": "object", "properties": {}}


def test_stale_expected_version_is_a_version_conflict_tool_text() -> None:
    store = _board()
    reply = _call(
        store,
        3,
        "task_edit",
        {
            "mission_id": "m1",
            "task_key": "T002",
            "expected_version": "stale",
            "fields": {"title": "two!"},
        },
    )

    assert reply["result"]["isError"] is True
    assert "version conflict: reload the plan and retry" in _text(reply)
    live = store.get_live_state("m1")
    assert live is not None
    assert live.plan["tasks"][1]["title"] == "two"


def test_editing_a_running_task_is_read_only_tool_text() -> None:
    store = _board()
    version = store.get_live_state("m1").updated_at
    reply = _call(
        store,
        4,
        "task_edit",
        {
            "mission_id": "m1",
            "task_key": "T001",
            "expected_version": version,
            "fields": {"title": "x"},
        },
    )

    assert reply["result"]["isError"] is True
    assert "task already ran or is running and is read-only" in _text(reply)
    live = store.get_live_state("m1")
    assert live is not None
    assert live.plan["tasks"][0]["title"] == "one"


def test_valid_task_edit_updates_the_plan_and_writes_a_changelog_row() -> None:
    store = _board()
    version = store.get_live_state("m1").updated_at
    reply = _call(
        store,
        5,
        "task_edit",
        {
            "mission_id": "m1",
            "task_key": "T002",
            "expected_version": version,
            "fields": {"title": "two!"},
        },
    )

    assert "isError" not in reply["result"]
    payload = json.loads(_text(reply))
    assert payload["plan"]["tasks"][1]["title"] == "two!"
    live = store.get_live_state("m1")
    assert live is not None
    assert live.plan["tasks"][1]["title"] == "two!"
    assert any(e.action == "live state upsert" for e in store.changelog())


def test_add_hint_writes_the_task_override_row() -> None:
    store = _board()
    version = store.get_live_state("m1").updated_at
    reply = _call(
        store,
        6,
        "add_hint",
        {
            "mission_id": "m1",
            "task_key": "T002",
            "expected_version": version,
            "hint": "use module X",
        },
    )

    assert "isError" not in reply["result"]
    payload = json.loads(_text(reply))
    assert payload["override"] == {"task_key": "T002", "hint": "use module X"}
    overrides = store.task_overrides("m1")
    assert len(overrides) == 1
    assert overrides[0]["hint"] == "use module X"
    assert overrides[0]["updated_by"] == "mcp"
    assert any(e.action == "task_override set" for e in store.changelog())


def test_queue_move_refuses_a_running_mission_and_a_stale_version_conflicts() -> None:
    store = _board()
    version = store._queue_version
    reply = _call(
        store, 7, "queue_move",
        {"mission_id": "m1", "to_position": 1, "expected_version": version},
    )
    assert reply["result"]["isError"] is True
    assert "mission 'm1' is running" in _text(reply)

    store.upsert_live_state("m1", None, status="queued", who="seed")
    # a second queued mission so the move reorders the queue
    store._missions["m2"] = Mission(mission_id="m2", state_namespace="m2")
    store.update_mission("m2", None, name="Mission two")
    store.add_queue("m2", expected_version=None)
    queue_version = store._queue_version
    reply = _call(
        store, 8, "queue_move",
        {"mission_id": "m1", "to_position": 1, "expected_version": "stale"},
    )
    assert reply["result"]["isError"] is True
    assert "version conflict: reload the plan and retry" in _text(reply)

    reply = _call(
        store, 9, "queue_move",
        {"mission_id": "m1", "to_position": 1, "expected_version": queue_version},
    )
    assert "isError" not in reply["result"]
    payload = json.loads(_text(reply))
    assert [e["mission_id"] for e in payload["queue"]] == ["m2", "m1"]


def test_enable_mission_flips_the_enabled_flag() -> None:
    store = _board()
    version = store.get_mission("m1").updated_at
    reply = _call(
        store, 10, "enable_mission",
        {"mission_id": "m1", "enabled": False, "expected_version": version},
    )

    assert "isError" not in reply["result"]
    payload = json.loads(_text(reply))
    assert payload["mission"]["enabled"] is False
    assert store.get_mission("m1").enabled is False


def test_missing_expected_version_is_invalid_params_not_a_fallback() -> None:
    store = _board()
    reply = _call(
        store, 11, "task_edit",
        {"mission_id": "m1", "task_key": "T002", "fields": {"title": "z"}},
    )

    assert reply["error"]["code"] == -32602
    assert "expected_version must be a non-empty string" in reply["error"]["message"]


def test_dangerous_tools_return_the_confirm_text_without_a_token() -> None:
    store = _board()
    for name in ("pause_after_current", "dangerous_action_help"):
        result = call_tool(store, {"name": name, "arguments": {}})

        assert "isError" not in result
        text = result["content"][0]["text"]
        assert "pause_at_boundary" in text
        assert "dashboard ops panel" in text
        assert "token" not in text


def test_pause_after_current_creates_no_command_and_changes_nothing() -> None:
    store = _board()
    changelog_before = len(store.changelog())
    reply = _call(store, 21, "pause_after_current", {})

    assert "isError" not in reply["result"]
    assert "confirm_token" not in _text(reply)
    assert len(store.changelog()) == changelog_before
    live = store.get_live_state("m1")
    assert live is not None
    assert live.status == "active"


def test_queue_list_returns_the_queue_in_run_order() -> None:
    store = _board()
    reply = _call(store, 12, "queue_list", {})

    assert "isError" not in reply["result"]
    payload = json.loads(_text(reply))
    assert [entry["mission_id"] for entry in payload] == ["m1"]


def test_mission_get_returns_the_mission_row_and_reports_an_unknown_one() -> None:
    store = _board()
    payload = json.loads(_text(_call(store, 13, "mission_get", {"mission_id": "m1"})))
    assert payload["mission_id"] == "m1"
    assert payload["name"] == "Mission"

    reply = _call(store, 14, "mission_get", {"mission_id": "nope"})
    assert reply["result"]["isError"] is True
    assert "mission not found: nope" in _text(reply)


def test_task_list_returns_one_view_per_task() -> None:
    store = _board()
    reply = _call(store, 15, "task_list", {"mission_id": "m1"})

    assert "isError" not in reply["result"]
    payload = json.loads(_text(reply))
    assert [view["task_id"] for view in payload] == ["T001", "T002"]


def test_settings_get_returns_the_hermes_config() -> None:
    store = _board()
    store.update_hermes_config(None, worker_model="claude-x")
    reply = _call(store, 16, "settings_get", {})

    assert json.loads(_text(reply))["worker_model"] == "claude-x"


def test_health_reports_the_store_type() -> None:
    store = _board()
    reply = _call(store, 17, "health", {})

    assert json.loads(_text(reply)) == {"ok": True, "store": "InMemoryBoardStore"}


def test_protocol_keeps_answering_after_a_store_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    store = _board()

    def failing_queue() -> list[QueueEntry]:
        raise RuntimeError("store is down")

    monkeypatch.setattr(store, "queue", failing_queue)
    reply = _call(store, 18, "queue_list", {})

    assert reply["result"]["isError"] is True
    assert "RuntimeError: store is down" in _text(reply)
    later = read_message(_request(19, "tools/list"), store)
    assert later is not None
    assert "tools" in later["result"]
    assert "isError" not in _call(store, 20, "mission_get", {"mission_id": "m1"})["result"]
