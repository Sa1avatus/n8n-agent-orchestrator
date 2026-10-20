from __future__ import annotations

import json
import uuid
from pathlib import Path

from fastapi.testclient import TestClient

from claude_runner.dashboard import _with_display_status

from .conftest import calls, wait_for
from .test_runs import start


def _create(store, session_id: str, run_id: str, status: str, created_at: str, **extra: object):
    store.create_run(
        run_id=run_id,
        session_id=session_id,
        claude_session_id=str(uuid.uuid4()),
        session_created=0,
        role="worker",
        model="sonnet",
        provider="anthropic",
        cwd="/tmp",
        input=f"row {run_id}",
        status=status,
        created_at=created_at,
        owner_pid=0,
        **extra,
    )


def test_superseded_queued_run_is_cancelled_and_never_started(
    client: TestClient, tmp_path: Path
) -> None:
    manager = client.app.state.manager  # type: ignore[attr-defined]
    # two slots held by sleeping runs, so the queued run waits for a slot
    busy_a = start(client, input="[[sleep:1.5]] busy a", session_id=str(uuid.uuid4()))
    busy_c = start(client, input="[[sleep:1.5]] busy c", session_id=str(uuid.uuid4()))
    session_b = str(uuid.uuid4())
    queued = start(client, input="QUEUED-MARKER", session_id=session_b)
    assert queued["status"] == "queued"
    # a newer run of the same session appears while the queued run waits
    _create(manager.store, session_b, "run_newer_" + uuid.uuid4().hex, "completed", "9999")

    done = wait_for(client, queued["run_id"])
    assert done["status"] == "cancelled"
    assert done["error"]["code"] == "superseded"
    wait_for(client, busy_a["run_id"])
    wait_for(client, busy_c["run_id"])
    assert all("QUEUED-MARKER" not in json.dumps(call) for call in calls(tmp_path))


def test_parent_interrupted_after_child_queued_supersedes_child(client: TestClient) -> None:
    store = client.app.state.manager.store  # type: ignore[attr-defined]
    session = str(uuid.uuid4())
    _create(store, session, "run_parent", "interrupted", "2030-01-01T00:00:00+00:00",
            finished_at="2030-01-01T00:00:05+00:00")
    _create(store, session, "run_child", "queued", "2030-01-01T00:00:01+00:00")

    assert store.supersede_queued(session) == ["run_child"]
    child = store.get_run("run_child")
    assert child is not None
    assert child["status"] == "cancelled" and child["error_code"] == "superseded"


def test_parent_interrupted_before_child_queued_does_not_supersede(client: TestClient) -> None:
    store = client.app.state.manager.store  # type: ignore[attr-defined]
    session = str(uuid.uuid4())
    _create(store, session, "run_parent", "interrupted", "2030-01-01T00:00:00+00:00",
            finished_at="2030-01-01T00:00:02+00:00")
    _create(store, session, "run_child", "queued", "2030-01-01T00:00:05+00:00")

    assert store.supersede_queued(session) == []
    child = store.get_run("run_child")
    assert child is not None and child["status"] == "queued"


def test_dashboard_status_queued_running_never_interrupted_superseded_cancelled() -> None:
    queued = {"status": "queued", "error": None}
    running = {"status": "running", "error": None}
    superseded = {"status": "cancelled", "error": {"code": "superseded", "message": "x"}}
    interrupted = {"status": "interrupted", "error": {"code": "run_not_found", "message": "y"}}

    assert _with_display_status(queued)["status"] == "queued"
    assert _with_display_status(running)["status"] == "running"
    assert _with_display_status(superseded)["status"] == "cancelled"
    assert _with_display_status(interrupted)["status"] == "interrupted"
