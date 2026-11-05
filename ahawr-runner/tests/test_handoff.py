from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from fastapi.testclient import TestClient

from ahawr_runner.config import Settings
from ahawr_runner.handoff import session_digest
from ahawr_runner.runs import RunManager

from .conftest import wait_for
from .test_runs import start

_SRC = str(Path(__file__).resolve().parent.parent / "src")


def test_session_digest_lists_what_the_session_did(client: TestClient) -> None:
    first = start(client, role="worker", input="TASK T002: triage logs\n[[tools]]")
    wait_for(client, first["run_id"])
    sid = first["session_id"]
    second = start(client, role="worker", session_id=sid, input="[[fail:model]] continue")
    wait_for(client, second["run_id"])

    body = client.get(f"/v1/sessions/{sid}/digest").json()
    assert body["session_id"] == sid and body["runs"] == 2 and not body["truncated"]
    digest = body["digest"]
    # the task itself is sent with the handoff: only later prompts are repeated here
    assert "PROMPT: TASK T002" not in digest and "PROMPT: [[fail:model]] continue" in digest
    assert "TOOL Read: calc.py" in digest and "→ 1 def sub(a, b): 2 return a + b" in digest
    assert "RESULT:" in digest
    assert digest.index(first["run_id"]) < digest.index(second["run_id"])
    header = next(line for line in digest.splitlines() if second["run_id"] in line)
    assert "failed" in header

    # a longer session is cut to its newest entries
    for _ in range(3):
        wait_for(client, start(client, role="worker", session_id=sid, input="[[tools]]")["run_id"])
    last = start(client, role="worker", session_id=sid, input="wrap up")
    wait_for(client, last["run_id"])
    short = client.get(f"/v1/sessions/{sid}/digest", params={"max_chars": 500}).json()
    assert short["truncated"] and len(short["digest"]) < 600
    assert "earlier entries omitted" in short["digest"].splitlines()[0]
    assert last["run_id"] in short["digest"] and first["run_id"] not in short["digest"]

    assert client.get("/v1/sessions/nope/digest").status_code == 404
    assert client.get(f"/v1/sessions/{sid}/digest", params={"max_chars": 10}).status_code == 422


def test_digest_reads_store_and_events_of_the_shared_run_manager(
    client: TestClient, settings: Settings
) -> None:
    """Characterization of what handoff.session_digest reads off the RunManager it is given:
    store.list_runs(limit, session_id=...) is newest first, events.read(run_id) is (entries, block),
    the digest lists the runs oldest first and a cut keeps the newest lines."""
    first = start(client, role="worker", input="[[tools]]")
    wait_for(client, str(first["run_id"]))
    sid = str(first["session_id"])
    second = start(client, role="worker", session_id=sid, input="[[tools]] later")
    wait_for(client, str(second["run_id"]))

    manager = RunManager(settings)
    assert isinstance(manager, RunManager)

    runs = manager.store.list_runs(500, session_id=sid)
    assert [run["run_id"] for run in runs] == [second["run_id"], first["run_id"]]
    assert len(manager.store.list_runs(1, session_id=sid)) == 1

    entries, block = manager.events.read(str(first["run_id"]))
    assert isinstance(entries, list)
    assert block is None or isinstance(block, dict)
    # a run records its own prompt as an event; the digest shows it only for the first run
    assert entries[0]["kind"] == "prompt" and entries[0]["text"] == "[[tools]]"
    assert {"tool_use", "tool_result", "result"} <= {entry["kind"] for entry in entries}

    digest = session_digest(manager, sid, 12_000)
    assert digest["session_id"] == sid and digest["runs"] == 2 and not digest["truncated"]
    assert digest["digest"].index(first["run_id"]) < digest["digest"].index(second["run_id"])

    cut = session_digest(manager, sid, 300)
    assert cut["truncated"] and "earlier entries omitted" in cut["digest"].splitlines()[0]
    assert second["run_id"] in cut["digest"] and first["run_id"] not in cut["digest"]
    manager.store.close()


def test_fresh_interpreter_imports_handoff_before_runs() -> None:
    """handoff.py imports RunManager only under TYPE_CHECKING: the hidden cycle runs.py <->
    handoff.py must not break a fresh interpreter that imports handoff first."""
    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            "import ahawr_runner.handoff as handoff\n"
            "import ahawr_runner.runs as runs\n"
            "print('ok', handoff.session_digest.__name__, runs.RunManager.__name__)",
        ],
        cwd=_SRC,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "ok session_digest RunManager"


def test_fresh_interpreter_imports_runs_before_handoff() -> None:
    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            "import ahawr_runner.runs as runs\n"
            "import ahawr_runner.handoff as handoff\n"
            "print('ok', runs.RunManager.__name__, handoff.session_digest.__name__)",
        ],
        cwd=_SRC,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "ok RunManager session_digest"
