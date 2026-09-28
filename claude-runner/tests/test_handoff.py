from __future__ import annotations

from fastapi.testclient import TestClient

from .conftest import wait_for
from .test_runs import start


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
