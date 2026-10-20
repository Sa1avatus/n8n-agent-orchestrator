"""Run record and event tests of a cloud run's thinking mode.

Made-up policy text and task numbers, the stub CLI (``tests/fake_claude.py``) and the
in-memory API: no network, no real Claude Code run.
"""

from __future__ import annotations

import sqlite3

from fastapi.testclient import TestClient

from claude_runner.api import create_app
from claude_runner.config import Settings
from claude_runner.store import SCHEMA

from .conftest import wait_for

# task 1 -> plan+pulse: thinks on the first attempt, off on a retry.
SAMPLE = (
    '{"variants":[{"name":"plan+pulse","weight":2,'
    '"first":[["think", 8], ["off", 25], ["think", 10], ["off", null]],'
    '"retry":[["off", 25], ["think", 10], ["off", null]]},'
    '{"name":"control","weight":1,"first":[["off", null]],"retry":[["think", 3], ["off", null]]}],'
    '"from_task":1}'
)


def start(client: TestClient, **body: object) -> dict[str, object]:
    payload = {
        "input": "TASK T001 hello",
        "model": "sonnet",
        "provider": "anthropic",
        "role": "worker",
        "think_policy": SAMPLE,
        "think_policy_from_task": 1,
        **body,
    }
    response = client.post("/v1/runs", json=payload)
    assert response.status_code == 200, response.text
    return response.json()  # type: ignore[no-any-return]


def thinking_states(client: TestClient, run_id: str) -> list[dict[str, object]]:
    entries, _ = client.app.state.manager.events.read(run_id)  # type: ignore[attr-defined]
    return [e for e in entries if e["kind"] == "thinking_state"]


def test_cloud_run_records_variant_and_mode(settings: Settings) -> None:
    object.__setattr__(settings, "cloud_think", True)
    with TestClient(create_app(settings)) as client:
        first = start(client)
        done = wait_for(client, first["run_id"])
        assert done["status"] == "completed"
        store = client.app.state.manager.store  # type: ignore[attr-defined]
        record = store.get_run(first["run_id"])
        assert record is not None
        assert record["think_variant"] == "plan+pulse"
        assert record["think_mode"] == "think"
        # the event carries the same keys as the local thinking_state entry
        [state] = thinking_states(client, first["run_id"])
        assert state["thinking"] == "on"
        assert state["variant"] == "plan+pulse" and state["retry"] is False


def test_cloud_retry_run_records_the_retry_mode(settings: Settings) -> None:
    object.__setattr__(settings, "cloud_think", True)
    with TestClient(create_app(settings)) as client:
        first = start(client)
        wait_for(client, first["run_id"])
        retry = start(client, session_id=first["session_id"], input="TASK T001 again")
        assert wait_for(client, retry["run_id"])["status"] == "completed"
        store = client.app.state.manager.store  # type: ignore[attr-defined]
        record = store.get_run(retry["run_id"])
        assert record is not None
        assert record["think_variant"] == "plan+pulse"
        # retry[0] of plan+pulse is "off"
        assert record["think_mode"] == "nothink"
        [state] = thinking_states(client, retry["run_id"])
        assert state["thinking"] == "off" and state["retry"] is True


def test_cloud_run_with_setting_off_keeps_the_old_shape(settings: Settings) -> None:
    with TestClient(create_app(settings)) as client:
        run = start(client)
        assert wait_for(client, run["run_id"])["status"] == "completed"
        store = client.app.state.manager.store  # type: ignore[attr-defined]
        record = store.get_run(run["run_id"])
        assert record is not None
        assert record["think_variant"] is None and record["think_mode"] is None
        # the policy itself is still recorded, as before
        assert record["think_policy"] == SAMPLE
        assert thinking_states(client, run["run_id"]) == []


def test_local_run_keeps_the_old_shape_with_setting_on(settings: Settings) -> None:
    object.__setattr__(settings, "cloud_think", True)
    with TestClient(create_app(settings)) as client:
        run = start(client, provider="local")
        assert wait_for(client, run["run_id"])["status"] == "completed"
        store = client.app.state.manager.store  # type: ignore[attr-defined]
        record = store.get_run(run["run_id"])
        assert record is not None
        assert record["think_variant"] is None and record["think_mode"] is None
        assert thinking_states(client, run["run_id"]) == []


def test_old_record_without_think_variant_loads(settings: Settings) -> None:
    # A database written before think_variant / think_mode existed.
    old_schema = SCHEMA.replace("    think_variant TEXT,\n    think_mode TEXT,\n", "")
    assert "think_variant" not in old_schema
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(settings.data_dir / "runner.sqlite")
    db.executescript(old_schema)
    db.execute(
        "INSERT INTO runs(run_id, session_id, claude_session_id, cwd, status, input, output, "
        "role, model, provider, created_at) "
        "VALUES ('run_old', 's', 'c', '/w', 'completed', 'x', 'old output', 'worker', 'm', "
        "'anthropic', '2026-09-29T01:00:00+00:00')"
    )
    db.commit()
    db.close()
    with TestClient(create_app(settings)) as client:
        body = client.get("/v1/runs/run_old").json()
        assert body["status"] == "completed" and body["output"] == "old output"
        store = client.app.state.manager.store  # type: ignore[attr-defined]
        record = store.get_run("run_old")
        assert record is not None
        assert record["think_variant"] is None and record["think_mode"] is None
