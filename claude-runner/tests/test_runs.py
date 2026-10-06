from __future__ import annotations

import time
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from claude_runner.api import create_app
from claude_runner.config import Settings
from claude_runner.store import Store

from .conftest import calls, wait_for


def start(client: TestClient, **body: object) -> dict[str, object]:
    payload = {"input": "hello", "model": "sonnet", "provider": "anthropic", **body}
    response = client.post("/v1/runs", json=payload)
    assert response.status_code == 200, response.text
    return response.json()  # type: ignore[no-any-return]


def test_start_poll_complete_and_resume(client: TestClient, tmp_path: Path) -> None:
    first = start(client, role="worker")
    assert first["run_id"] and first["status"] in ("queued", "running")
    assert first["session_created"] is True
    sid = first["session_id"]
    done = wait_for(client, first["run_id"])
    assert done["status"] == "completed"
    assert done["output"].startswith("echo[1]: hello")
    assert done["session_id"] == sid and done["error"] == ""
    assert done["context_tokens"] == 1015 and done["num_turns"] == 1

    second = start(client, role="worker", session_id=sid, input="continue")
    assert second["session_id"] == sid and second["session_created"] is False
    done2 = wait_for(client, second["run_id"])
    assert done2["output"].startswith("echo[2]: continue")
    # Claude Code reports session totals; each run carries only its own share
    assert done["cost_usd"] == 0.001 and done["api_ms"] == 1000
    assert done2["cost_usd"] == 0.001 and done2["api_ms"] == 1000

    first_call, second_call = calls(tmp_path)
    assert "--session-id" in first_call["args"] and "--resume" in second_call["args"]
    args = second_call["args"]
    assert args[args.index("--permission-mode") + 1] == "bypassPermissions"
    assert "Bash(git push *)" in args[args.index("--disallowedTools") + 1]
    assert args[args.index("--model") + 1] == "sonnet"


def test_retry_fresh_session_starts_a_new_session(settings: Settings, tmp_path: Path) -> None:
    from claude_runner.runs import _RETRY_MARK

    object.__setattr__(settings, "retry_fresh_session", True)
    object.__setattr__(settings, "retry_input_chars", 2000)
    with TestClient(create_app(settings)) as client:
        first = start(client, role="worker", input="task")
        sid = first["session_id"]
        wait_for(client, first["run_id"])

        digest = "d" * 5000
        retry_input = "TASK: redo\n\nFINDINGS: x\n\nREPORT: y\n\n" + digest
        retry = start(client, role="worker", session_id=sid, input=retry_input)
        assert retry["session_id"] == sid
        assert retry["session_created"] is True
        assert retry["claude_session_id"] != first["claude_session_id"]
        done = wait_for(client, retry["run_id"])
        assert done["status"] == "completed"
        # The fake CLI received the cut prompt, and the second call is a fresh session.
        first_call, second_call = calls(tmp_path)
        assert "--session-id" in second_call["args"] and "--resume" not in second_call["args"]
        prompt = second_call["prompt"]
        assert len(prompt) == 2000
        assert prompt.endswith(_RETRY_MARK)
        assert prompt.startswith("TASK: redo\n\nFINDINGS: x\n\nREPORT: y\n\n")
        # the digest is cut to exactly budget - fixed parts - separators - marker
        fixed = "TASK: redo\n\nFINDINGS: x\n\nREPORT: y"
        parts = prompt.split("\n\n")
        assert len(parts) == 5
        assert parts[0] == "TASK: redo" and parts[1] == "FINDINGS: x" and parts[2] == "REPORT: y"
        assert parts[3] == "d" * (2000 - len(fixed) - 4 - len(_RETRY_MARK))
        assert parts[4] == _RETRY_MARK
        # No /compact run was started for the old session before the retry.
        assert all(c["prompt"] != "/compact" for c in calls(tmp_path))


def test_retry_fresh_session_default_off_keeps_resuming(settings: Settings, tmp_path: Path) -> None:
    object.__setattr__(settings, "retry_input_chars", 2000)
    with TestClient(create_app(settings)) as client:
        first = start(client, role="worker", input="task")
        sid = first["session_id"]
        wait_for(client, first["run_id"])
        retry = start(client, role="worker", session_id=sid, input="x" * 5000)
        assert retry["session_created"] is False
        assert retry["claude_session_id"] == first["claude_session_id"]
        done = wait_for(client, retry["run_id"])
        assert done["output"].startswith("echo[2]: ")
        second_call = calls(tmp_path)[-1]
        assert "--resume" in second_call["args"]
        assert second_call["prompt"] == "x" * 5000


def test_retry_fresh_session_per_provider_override(settings: Settings) -> None:
    from claude_runner.config import _providers

    object.__setattr__(settings, "retry_fresh_session", False)
    object.__setattr__(
        settings,
        "providers",
        _providers(
            {
                "CLAUDE_RUNNER_PROVIDER_LOCAL__ANTHROPIC_BASE_URL": "http://litellm:4000",
                "CLAUDE_RUNNER_PROVIDER_LOCAL__ANTHROPIC_AUTH_TOKEN": "sk-litellm",
                "CLAUDE_RUNNER_PROVIDER_LOCAL__RETRY_FRESH_SESSION": "1",
            }
        ),
    )
    with TestClient(create_app(settings)) as client:
        first = start(client, role="worker", provider="local", input="task")
        sid = first["session_id"]
        wait_for(client, first["run_id"])
        on = start(client, role="worker", provider="local", session_id=sid, input="redo")
        assert on["session_created"] is True
        wait_for(client, on["run_id"])
        off = start(client, role="worker", provider="anthropic", session_id=sid, input="again")
        assert off["session_created"] is False
        assert off["claude_session_id"] == on["claude_session_id"]
        wait_for(client, off["run_id"])


def test_compact_skipped_when_fresh_session_retry_is_on(settings: Settings, tmp_path: Path) -> None:
    object.__setattr__(settings, "retry_fresh_session", True)
    with TestClient(create_app(settings)) as client:
        first = start(client, role="worker", input="task")
        sid = first["session_id"]
        wait_for(client, first["run_id"])
        resp = client.post(f"/v1/sessions/{sid}/compact")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "skipped"
        assert body["reason"] == "fresh_session_retry"
        assert all(c["prompt"] != "/compact" for c in calls(tmp_path))


def test_compact_runs_when_fresh_session_retry_is_off(settings: Settings, tmp_path: Path) -> None:
    object.__setattr__(settings, "retry_fresh_session", False)
    with TestClient(create_app(settings)) as client:
        first = start(client, role="worker", input="task")
        sid = first["session_id"]
        wait_for(client, first["run_id"])
        # Force the context above the threshold so compaction is not skipped for that reason.
        object.__setattr__(settings, "compact_min_tokens", 0)
        resp = client.post(f"/v1/sessions/{sid}/compact")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "completed"
        assert body["compression_completed"] is True
        compact_calls = [c for c in calls(tmp_path) if c["prompt"].startswith("/compact")]
        assert len(compact_calls) == 1


def test_retry_input_cut_keeps_the_fixed_parts(settings: Settings) -> None:
    from claude_runner.runs import _RETRY_MARK, cut_retry_input

    fixed = "A" * 3000
    digest = "d" * 5000
    # The digest is trimmed so the fixed parts survive, newest last, with a marker.
    cut = cut_retry_input(fixed + "\n\n" + digest, 4000)
    assert len(cut) == 4000
    assert cut.startswith(fixed)
    assert cut.endswith(_RETRY_MARK)
    assert cut.count("\n\n") == 2
    # The input is unchanged when it fits the budget.
    assert cut_retry_input(fixed + "\n\n" + digest, 10_000) == fixed + "\n\n" + digest
    # If the fixed parts alone exceed the budget the whole input is cut.
    assert cut_retry_input(fixed + "\n\n" + digest, 2000) == (fixed + "\n\n" + digest)[:2000]


def test_role_profiles_keep_reviewer_read_only(client: TestClient, tmp_path: Path) -> None:
    run = start(client, role="reviewer")
    wait_for(client, run["run_id"])
    args = calls(tmp_path)[0]["args"]
    assert args[args.index("--permission-mode") + 1] == "dontAsk"
    assert "Edit" in args[args.index("--disallowedTools") + 1].split(",")
    # the role can also arrive as a header; unknown roles fall back to the generic profile
    response = client.post(
        "/v1/runs", json={"input": "x", "model": "m"}, headers={"X-AHAWR-Role": "hacker"}
    )
    assert response.json()["role"] == "generic"


def test_unknown_and_foreign_session_ids(client: TestClient, tmp_path: Path) -> None:
    # A Hermes-era id is not a Claude Code session: bind a fresh one, keep the caller's id.
    run = start(client, session_id="hermes-session-42")
    assert run["session_id"] == "hermes-session-42" and run["session_created"] is True
    assert run["claude_session_id"] != "hermes-session-42"
    wait_for(client, run["run_id"])
    again = start(client, session_id="hermes-session-42", input="more")
    assert again["claude_session_id"] == run["claude_session_id"]
    assert again["session_created"] is False
    wait_for(client, again["run_id"])
    # An unknown UUID becomes the Claude Code session id itself.
    new_id = str(uuid.uuid4())
    fresh = start(client, session_id=new_id)
    assert fresh["claude_session_id"] == new_id and fresh["session_created"] is True
    wait_for(client, fresh["run_id"])
    assert "--session-id" in calls(tmp_path)[-1]["args"]


def test_session_created_outside_runner_is_resumed(client: TestClient, tmp_path: Path) -> None:
    sid = str(uuid.uuid4())
    project = Path(str(tmp_path / "claude-config")) / "projects" / "x"
    project.mkdir(parents=True)
    (project / f"{sid}.jsonl").write_text("{}\n")
    run = start(client, session_id=sid)
    assert run["session_created"] is False
    wait_for(client, run["run_id"])
    assert "--resume" in calls(tmp_path)[-1]["args"]


def test_repeated_start_attaches_to_active_run(client: TestClient) -> None:
    first = start(client, input="slow [[sleep:1]]")
    sid = first["session_id"]
    wait_for(client, first["run_id"], lambda r: r["status"] == "running", timeout=5)
    second = start(client, session_id=sid, input="duplicate")
    assert second["run_id"] == first["run_id"] and second["attached"] is True
    assert wait_for(client, first["run_id"])["status"] == "completed"


@pytest.mark.parametrize(
    ("directive", "code", "http", "word"),
    [
        ("[[fail:overloaded]]", "overloaded", 529, "overloaded"),
        ("[[fail:rate_limit]]", "rate_limit", 429, "rate limit"),
        ("[[fail:model]]", "api_error", None, "model"),
        ("[[max_turns]]", "max_turns", None, "max-turns"),
        ("[[crash]]", "cli_error", None, "something broke"),
    ],
)
def test_failures_map_to_hermes_contract(
    client: TestClient, directive: str, code: str, http: int | None, word: str
) -> None:
    run = start(client, input=f"do it {directive}")
    done = wait_for(client, run["run_id"])
    assert done["status"] == "failed"
    assert done["error"]["code"] == code
    assert done["http_code"] == http
    assert word in done["error"]["message"].lower()


def test_garbage_lines_are_ignored(client: TestClient) -> None:
    done = wait_for(client, start(client, input="[[garbage]] fine")["run_id"])
    assert done["status"] == "completed"


def test_timeout_and_cancel(settings: Settings) -> None:
    object.__setattr__(settings, "max_run_seconds", 1)
    with TestClient(create_app(settings)) as client:
        done = wait_for(client, start(client, input="[[sleep:30]]")["run_id"], timeout=30)
        assert done["status"] == "failed" and done["error"]["code"] == "timeout"
        assert "timed out" in done["error"]["message"]
    object.__setattr__(settings, "max_run_seconds", 60)
    pid_file = settings.data_dir / "child.pid"
    with TestClient(create_app(settings)) as client:
        run = start(client, input=f"[[child:{pid_file}]] [[sleep:30]]")
        wait_for(client, run["run_id"], lambda r: r["status"] == "running", timeout=5)
        deadline = time.monotonic() + 5
        while not pid_file.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        child = int(pid_file.read_text())
        assert _alive(child)
        cancelled = client.post(f"/v1/runs/{run['run_id']}/cancel").json()
        assert cancelled["status"] == "cancelled"
        # the tool process the CLI started does not outlive the cancelled run
        deadline = time.monotonic() + 5
        while _alive(child) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert not _alive(child)


def _alive(pid: int) -> bool:
    """Running, not a zombie waiting to be reaped."""
    try:
        state = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
    except (OSError, IndexError):
        return False
    return state != "Z"


def test_unknown_run_and_restart_recovery(settings: Settings) -> None:
    with TestClient(create_app(settings)) as client:
        missing = client.get("/v1/runs/run_nope")
        assert missing.status_code == 404
        assert missing.json()["error"]["code"] == "run_not_found"
        sid = start(client)["session_id"]
    # Simulate a runner crash that left a run "running".
    store = Store(settings.data_dir / "runner.sqlite")
    store.create_run(
        run_id="run_orphan", session_id=sid, claude_session_id=sid, cwd="/", status="running"
    )
    store.close()
    with TestClient(create_app(settings)) as client:
        assert client.get("/health").json()["interrupted_on_start"] == 1
        orphan = client.get("/v1/runs/run_orphan")
        assert orphan.status_code == 404
        assert orphan.json()["error"]["code"] == "run_not_found"
        # the Run Manager then resumes the saved session
        resumed = start(client, session_id=sid, input="Continue the existing task")
        assert resumed["session_created"] is False
        assert wait_for(client, resumed["run_id"])["status"] == "completed"


def test_working_directory_rules(client: TestClient, workspace: Path) -> None:
    run = start(client, working_directory="D:\\n8n\\workspace\\sub")
    assert wait_for(client, run["run_id"])["status"] == "completed"
    outside = client.post("/v1/runs", json={"input": "x", "working_directory": "/etc"})
    assert outside.status_code == 400
    assert outside.json()["error"]["code"] == "invalid_working_directory"
    missing = client.post(
        "/v1/runs", json={"input": "x", "working_directory": str(workspace / "nope")}
    )
    assert missing.json()["error"]["code"] == "invalid_working_directory"
    empty = client.post("/v1/runs", json={"input": "  "})
    assert empty.json()["error"]["code"] == "input_required"


def test_compaction(client: TestClient) -> None:
    assert client.post("/v1/sessions/nope/compact").json()["reason"] == "session_not_found"
    run = start(client)
    sid = run["session_id"]
    wait_for(client, run["run_id"])
    below = client.post(f"/v1/sessions/{sid}/compact", json={}).json()
    assert below["status"] == "skipped" and below["reason"] == "below_threshold"
    assert below["compression_failed"] is False
    wait_for(client, start(client, session_id=sid, input="grow")["run_id"])
    wait_for(client, start(client, session_id=sid, input="grow more")["run_id"])
    done = client.post(f"/v1/sessions/{sid}/compact").json()
    assert done["status"] == "completed" and done["compression_completed"] is True
    assert done["post_tokens"] == 2000
    info = client.get(f"/v1/sessions/{sid}").json()
    assert info["context_tokens"] == 2000
    forced = client.post(f"/v1/sessions/{sid}/compact", json={"mode": "always"}).json()
    assert forced["status"] == "completed"
    off = client.post(f"/v1/sessions/{sid}/compact", json={"mode": "off"}).json()
    assert off["reason"] == "compaction_disabled"
    after = start(client, session_id=sid, input="after compaction")
    assert wait_for(client, after["run_id"])["status"] == "completed"
    assert client.get("/v1/sessions/unknown").status_code == 404


def test_compaction_skips_busy_session(client: TestClient) -> None:
    run = start(client, input="[[sleep:1]]")
    wait_for(client, run["run_id"], lambda r: r["status"] == "running", timeout=5)
    busy = client.post(f"/v1/sessions/{run['session_id']}/compact", json={"mode": "always"})
    assert busy.json()["reason"] == "session_busy"
    wait_for(client, run["run_id"])


def test_bearer_auth_and_health(settings: Settings) -> None:
    object.__setattr__(settings, "api_key", "secret")
    with TestClient(create_app(settings)) as client:
        assert client.post("/v1/runs", json={"input": "x"}).status_code == 401
        ok = client.post(
            "/v1/runs", json={"input": "x"}, headers={"Authorization": "Bearer secret"}
        )
        assert ok.status_code == 200
        health = client.get("/health").json()  # health stays open for the healthcheck
        assert health["claude_code"].startswith("9.9.9")
        assert health["status"] == "ok"


def test_missing_cli_reports_unavailable(settings: Settings) -> None:
    object.__setattr__(settings, "claude_bin", "/nonexistent/claude")
    with TestClient(create_app(settings)) as client:
        assert client.get("/health").json()["claude_code"].startswith("unavailable")
        done = wait_for(client, start(client)["run_id"])
        assert done["status"] == "failed" and done["error"]["code"] == "cli_unavailable"
        assert done["http_code"] == 503


def test_shutdown_leaves_runs_resumable(settings: Settings) -> None:
    with TestClient(create_app(settings)) as client:
        run = start(client, input="[[sleep:30]]")
        wait_for(client, run["run_id"], lambda r: r["status"] == "running", timeout=5)
        time.sleep(1.0)  # let the CLI record the prompt in its transcript
    with TestClient(create_app(settings)) as client:
        gone = client.get(f"/v1/runs/{run['run_id']}")
        assert gone.status_code == 404
        assert "shutdown" in gone.json()["error"]["message"]
        again = start(client, session_id=run["session_id"], input="Continue")
        assert again["session_created"] is False
        assert wait_for(client, again["run_id"])["status"] == "completed"


def test_empty_authorization_header_without_api_key(client: TestClient) -> None:
    # The Run Manager always sends the header; it is empty when runner_api_key is not set.
    response = client.post(
        "/v1/runs", json={"input": "x", "model": "m"}, headers={"Authorization": ""}
    )
    assert response.status_code == 200


def test_run_cost_is_this_runs_share(tmp_path: Path) -> None:
    from claude_runner.runs import run_cost
    from claude_runner.store import Store

    assert run_cost(None, 1.0) is None
    assert run_cost(2.5, None) == 2.5  # first run of a session
    assert run_cost(4.3359, 3.5) == 0.8359  # resumed: the session total minus the previous one
    assert run_cost(0.4, 3.5) == 0.4  # total not restored: take it as is
    store = Store(tmp_path / "runs.sqlite")
    for rid, total, finished in (
        ("a", 1.0, "2026-09-29T01:00:00+00:00"),
        ("b", 3.5, "2026-09-29T02:00:00+00:00"),
    ):
        store.create_run(
            run_id=rid,
            kind="run",
            session_id="s",
            claude_session_id="c",
            role="worker",
            model="m",
            provider="local",
            cwd="/w",
            input="x",
        )
        store.update_run(rid, details={"total_cost_usd": total}, finished_at=finished)
    store.create_run(
        run_id="z",
        kind="run",
        session_id="s",
        claude_session_id="c",
        role="worker",
        model="m",
        provider="local",
        cwd="/w",
        input="x",
    )
    assert store.session_cost_before("c", "z") == 3.5
    assert store.session_cost_before("other", "z") is None
    assert store.session_total_before("c", "z", "duration_api_ms") is None
