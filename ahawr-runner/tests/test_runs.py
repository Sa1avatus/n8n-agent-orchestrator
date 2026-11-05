from __future__ import annotations

import asyncio
import http.server
import json
import logging
import os
import signal
import stat
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient

from ahawr_runner import runs, think_policy
from ahawr_runner.api import create_app
from ahawr_runner.config import ProviderProfile, Settings
from ahawr_runner.events import EventLog
from ahawr_runner.handoff import session_digest
from ahawr_runner.runs import ProcSample, RunManager
from ahawr_runner.store import Store
from ahawr_runner.think_switch import ThinkSwitch

from .conftest import FAKE, calls, wait_for


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
    from ahawr_runner.runs import _RETRY_MARK

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


def test_local_tool_output_limits_env_reaches_only_the_local_worker(
    settings: Settings, tmp_path: Path
) -> None:
    object.__setattr__(settings, "local_tool_output_limits", True)
    object.__setattr__(
        settings,
        "providers",
        {
            "LOCAL": ProviderProfile(
                name="LOCAL",
                env={
                    "ANTHROPIC_BASE_URL": "http://litellm:4000",
                    "ANTHROPIC_AUTH_TOKEN": "sk-litellm",
                },
            )
        },
    )
    with TestClient(create_app(settings)) as client:
        worker = start(client, role="worker", provider="local", input="task")
        wait_for(client, worker["run_id"])
        reviewer = start(client, role="reviewer", provider="local", input="review")
        wait_for(client, reviewer["run_id"])
        architect = start(client, role="architect", provider="anthropic", input="plan")
        wait_for(client, architect["run_id"])
    worker_call, reviewer_call, architect_call = calls(tmp_path)
    # The local Worker's CLI process gets the Part B caps...
    assert worker_call["window"]["BASH_MAX_OUTPUT_LENGTH"] == "12000"
    assert worker_call["window"]["CLAUDE_CODE_FILE_READ_MAX_OUTPUT_TOKENS"] == "16000"
    # ...and no other role does, so the Reviewer/Architect env is unchanged.
    assert "BASH_MAX_OUTPUT_LENGTH" not in reviewer_call["window"]
    assert "CLAUDE_CODE_FILE_READ_MAX_OUTPUT_TOKENS" not in reviewer_call["window"]
    assert "BASH_MAX_OUTPUT_LENGTH" not in architect_call["window"]
    assert "CLAUDE_CODE_FILE_READ_MAX_OUTPUT_TOKENS" not in architect_call["window"]


def test_retry_fresh_session_per_provider_override(settings: Settings) -> None:
    from ahawr_runner.config import _providers

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
    from ahawr_runner.runs import _RETRY_MARK, cut_retry_input

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


def fake_gateway() -> tuple[http.server.HTTPServer, list[dict[str, Any]]]:
    """A stdlib gateway serving the Hermes /v1/runs shape the dispatch calls: every POST is
    recorded, every GET answers with a terminal "completed" payload.
    shortcut: one completed shape is enough for the dispatch tests; extend when the gateway
    needs running/failed/timeout routes."""
    posts: list[dict[str, Any]] = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def _respond(self, payload: dict[str, Any]) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:
            raw = self.rfile.read(int(self.headers.get("Content-Length", 0) or 0))
            posts.append(json.loads(raw.decode("utf-8")))
            self._respond({"run_id": "gw-run-1", "session_id": "gw-session-9"})

        def do_GET(self) -> None:
            self._respond({"run_id": "gw-run-1", "status": "completed", "output": "hermes answer"})

        def log_message(self, message: str, *args: Any) -> None:
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, posts


def test_hermes_flag_on_followup_sends_the_stored_gateway_session(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    server, posts = fake_gateway()
    monkeypatch.setenv("HERMES_API_URL", f"http://127.0.0.1:{server.server_port}")
    monkeypatch.setenv("API_SERVER_KEY", "test-key")
    object.__setattr__(settings, "hermes_engine_enabled", True)
    object.__setattr__(
        settings, "providers", {"HERMES": ProviderProfile(name="hermes", engine="hermes")}
    )
    try:
        with TestClient(create_app(settings)) as client:
            first = start(client, role="worker", provider="hermes")
            sid = str(first["session_id"])
            done = wait_for(client, first["run_id"])
            assert done["status"] == "completed", done
            assert done["output"] == "hermes answer"
            # the first start sends no session id at all
            assert "session_id" not in posts[0]
            follow = start(client, role="worker", session_id=sid, provider="hermes", input="more")
            assert wait_for(client, follow["run_id"])["status"] == "completed"
            # the follow-up start carries the gateway session id bound by the first run
            assert posts[-1]["session_id"] == "gw-session-9"
            assert posts[-1]["input"] == "more"
    finally:
        server.shutdown()


def test_hermes_flag_off_profile_never_calls_the_gateway(
    settings: Settings, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    server, posts = fake_gateway()
    monkeypatch.setenv("HERMES_API_URL", f"http://127.0.0.1:{server.server_port}")
    monkeypatch.setenv("API_SERVER_KEY", "test-key")
    # hermes_engine_enabled stays False (the flag default)
    object.__setattr__(
        settings, "providers", {"HERMES": ProviderProfile(name="hermes", engine="hermes")}
    )
    try:
        with TestClient(create_app(settings)) as client:
            run = start(client, role="worker", provider="hermes")
            done = wait_for(client, run["run_id"])
            assert done["status"] == "completed", done
        # the gateway was never reached: the run went through the fake claude CLI instead
        assert not posts
        assert calls(tmp_path)
    finally:
        server.shutdown()


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


def test_completed_and_cancelled_runs_remove_proc_entry_and_kill_children(
    settings: Settings, tmp_path: Path
) -> None:
    """The helper must run on every exit path: a run that exits normally leaves the
    background tool process it started dead, and a cancelled run's child dies too.
    In both cases ``_procs`` holds no entry for the finished run."""

    # 1) normal completion with a background child
    pid_file = tmp_path / "child_done.pid"
    with TestClient(create_app(settings)) as client:
        manager: RunManager = client.app.state.manager
        run = start(client, input=f"[[child:{pid_file}]]")
        done = wait_for(client, run["run_id"])
        assert done["status"] == "completed"
        child = int(pid_file.read_text())
        assert not _alive(child)
        assert run["run_id"] not in manager._procs

    # 2) cancellation while the CLI is still running
    pid_file = tmp_path / "child_cancel.pid"
    with TestClient(create_app(settings)) as client:
        manager = client.app.state.manager
        run = start(client, input=f"[[child:{pid_file}]] [[sleep:30]]")
        wait_for(client, run["run_id"], lambda r: r["status"] == "running", timeout=5)
        deadline = time.monotonic() + 5
        while not pid_file.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        child = int(pid_file.read_text())
        assert _alive(child)
        cancelled = client.post(f"/v1/runs/{run['run_id']}/cancel").json()
        assert cancelled["status"] == "cancelled"
        deadline = time.monotonic() + 5
        while _alive(child) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert not _alive(child)
        assert run["run_id"] not in manager._procs


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


_PROFILE_CATALOG = json.dumps(
    {
        "profiles": {
            "plan": {
                "variants": [{"name": "plan", "first": [["think", 8], ["off", None]]}],
                "cap_steps": 5,
            }
        }
    }
)
_PROFILES_ON = json.dumps({"think_profiles": {"enabled": True}})


def _record(client: TestClient, run_id: str) -> dict[str, Any]:
    """The stored run row: what the thinking switch and the QA read for the run."""
    return client.app.state.manager.store.get_run(run_id) or {}


def test_think_profile_is_inert_when_the_feature_is_off(client: TestClient) -> None:
    plain = start(client, role="worker", think_policy="plan|off")
    asked = start(
        client,
        role="worker",
        think_policy="plan|off",
        think_profile="plan",
        thinking_json=_PROFILE_CATALOG,
        features_json=json.dumps({"think_profiles": {"enabled": False}}),
    )
    plain_record = _record(client, plain["run_id"])
    asked_record = _record(client, asked["run_id"])
    assert asked_record["think_policy"] == plain_record["think_policy"] == "plan|off"
    assert asked_record["think_variant"] is plain_record["think_variant"] is None


def test_think_profile_sets_the_run_policy_and_the_variant(client: TestClient) -> None:
    run = start(
        client,
        role="worker",
        think_policy="plan|off",
        think_profile="plan",
        thinking_json=_PROFILE_CATALOG,
        features_json=_PROFILES_ON,
    )
    record = _record(client, run["run_id"])
    assert record["think_variant"] == "plan"
    policy, errors = think_policy.parse(record["think_policy"])
    assert errors == [] and policy is not None
    # the catalog entry capped to cap_steps 5, written back as the JSON the switch parses
    assert policy.variants[0].first == (("think", 5), ("off", None))
    assert record["think_from"] == 0 and record["think_after"] == -1


def test_think_profile_falls_back_to_the_global_policy(client: TestClient) -> None:
    unknown = start(
        client,
        role="worker",
        think_policy="plan|off",
        think_profile="heavy",
        thinking_json=_PROFILE_CATALOG,
        features_json=_PROFILES_ON,
    )
    missing = start(
        client,
        role="worker",
        think_policy="plan|off",
        thinking_json=_PROFILE_CATALOG,
        features_json=_PROFILES_ON,
    )
    for run in (unknown, missing):
        record = _record(client, run["run_id"])
        assert record["think_policy"] == "plan|off"
        assert record["think_variant"] is None


_PROFILES_ALL = json.dumps(
    {
        "profiles": {
            "none": {"variants": [{"first": [["off", None]]}], "cap_steps": 1},
            "plan": {
                "variants": [{"name": "plan", "first": [["think", 8], ["off", None]]}],
                "cap_steps": 5,
            },
            # heavy thinks to the end of the attempt: the cap cuts it to 4 model calls
            "heavy": {"variants": [{"first": [["think", None]]}], "cap_steps": 4},
        }
    }
)


def test_flag_on_sets_the_policy_and_variant_of_none_plan_heavy(client: TestClient) -> None:
    for name, first in (
        ("none", (("off", None),)),
        ("plan", (("think", 5), ("off", None))),  # the 8 think calls cut to cap_steps 5
        ("heavy", (("think", 4), ("off", None))),  # think-to-the-end cut to cap_steps 4
    ):
        run = start(
            client,
            role="worker",
            think_policy="plan|off",
            think_profile=name,
            thinking_json=_PROFILES_ALL,
            features_json=_PROFILES_ON,
        )
        record = _record(client, str(run["run_id"]))
        assert record["think_variant"] == name
        policy, errors = think_policy.parse(record["think_policy"])
        assert errors == [] and policy is not None
        assert policy.variants[0].first == first
        assert record["think_from"] == 0 and record["think_after"] == -1


def test_each_run_keeps_its_own_policy_in_the_thinking_switch(
    client: TestClient, settings: Settings, tmp_path: Path
) -> None:
    """Two consecutive runs with different profiles: the switch reads the policy of
    the run it is ticking, not the one the run before it had."""
    # a store over the same runner.sqlite the client's app writes into (see
    # test_shared_run_manager_symbol_for_handoff)
    store = RunManager(settings).store
    plan_run = start(
        client,
        role="worker",
        provider="local",
        input="TASK T011: plan run",
        think_policy="plan|off",
        think_profile="plan",
        thinking_json=_PROFILES_ALL,
        features_json=_PROFILES_ON,
    )
    plan_id = str(plan_run["run_id"])
    wait_for(client, plan_id)
    none_run = start(
        client,
        role="worker",
        provider="local",
        input="TASK T012: none run",
        think_policy="plan|off",
        think_profile="none",
        thinking_json=_PROFILES_ALL,
        features_json=_PROFILES_ON,
    )
    none_id = str(none_run["run_id"])
    wait_for(client, none_id)
    flag = tmp_path / "local_thinking"
    flag.write_text("off")
    switch = ThinkSwitch(store, flag, 3600, events=None, policy=True, policy_from_task=1)
    # the plan run's first model call thinks: thinking turns on for it
    store.update_run(plan_id, status="running")
    assert switch.tick() == "on" and flag.read_text() == "on"
    # the next run asked for 'none': thinking goes off, not carried over from the plan run
    store.update_run(plan_id, status="completed")
    store.update_run(none_id, status="running")
    assert switch.tick() == "off" and flag.read_text() == "off"
    store.close()


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


def test_stub_bin_by_bare_name_on_path_completes(settings: Settings, tmp_path: Path) -> None:
    # A bare claude_bin name is resolved through the child env's PATH, so a run
    # with claude_bin set to the stub's directory name completes normally.
    stub_dir = tmp_path / "claude_bin_dir"
    stub_dir.mkdir()
    stub = stub_dir / "claude"
    stub.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{FAKE}" "$@"\n')
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    object.__setattr__(settings, "claude_bin", "claude")
    with TestClient(create_app(settings)) as client:
        import os
        os.environ["PATH"] = f"{stub_dir}:{os.environ.get('PATH', '')}"
        run = start(client)
        done = wait_for(client, run["run_id"])
        assert done["status"] == "completed"


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
    from ahawr_runner.runs import run_cost
    from ahawr_runner.store import Store

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


def test_qa_run_records_a_verdict_event_and_other_roles_do_not(
    client: TestClient, settings: Settings
) -> None:
    import json

    qa = wait_for(client, str(start(client, role="qa", input="verify")["run_id"]))
    worker = wait_for(client, str(start(client, role="worker", input="build")["run_id"]))
    events_dir = settings.data_dir / "events"

    def kinds(run_id: str) -> list[dict[str, object]]:
        lines = (events_dir / f"{run_id}.jsonl").read_text(encoding="utf-8").splitlines()
        return [json.loads(line) for line in lines]

    verdicts = [e for e in kinds(qa["run_id"]) if e.get("kind") == "qa_verdict"]
    assert len(verdicts) == 1
    assert verdicts[0]["role"] == "qa" and verdicts[0]["status"] == "completed"
    assert str(verdicts[0]["summary"]).startswith("echo[1]: verify")
    assert not [e for e in kinds(worker["run_id"]) if e.get("kind") == "qa_verdict"]


def test_shared_run_manager_symbol_for_handoff(
    client: TestClient, settings: Settings
) -> None:
    """Characterization of the RunManager symbol handoff.py imports under TYPE_CHECKING:
    a manager built from Settings exposes the store and events the digest reads."""
    first = start(client, role="worker", input="[[tools]]")
    first_id = str(first["run_id"])
    sid = str(first["session_id"])
    wait_for(client, first_id)
    second = start(client, role="worker", session_id=sid, input="[[tools]] later")
    wait_for(client, str(second["run_id"]))

    manager = RunManager(settings)
    assert isinstance(manager, RunManager)
    assert isinstance(manager.store, Store) and isinstance(manager.events, EventLog)
    # list_runs is newest first, which session_digest reverses to get the oldest run first
    assert [run["run_id"] for run in manager.store.list_runs(500, session_id=sid)] == [
        second["run_id"],
        first_id,
    ]
    assert len(manager.store.list_runs(1, session_id=sid)) == 1
    entries, _ = manager.events.read(first_id)
    # a run records its own prompt as an event; the digest shows it only for the first run
    assert entries[0]["kind"] == "prompt" and entries[0]["text"] == "[[tools]]"
    digest = session_digest(manager, sid, 12_000)
    assert digest["runs"] == 2 and digest["entries"] > 0 and not digest["truncated"]
    manager.store.close()


# --------------------------------------------------------------------------- watchdog loop
# Fakes for the watchdog loop (`_watchdog_loop`) and the scan it runs (`watchdog_scan`):
# a sleep stand-in that records the interval and ends the loop the way task cancellation
# ends it today, and a process state the scan reads instead of /proc (no real process, no
# real sleep: the loop runs in microseconds).
class _StubProc:
    """A process the watchdog scan can see: the scan reads only ``pid`` from it."""

    def __init__(self, pid: int) -> None:
        self.pid = pid


def _fake_sleep(
    monkeypatch: pytest.MonkeyPatch, stop_after_scans: int, end_with: type[BaseException]
) -> list[float]:
    """asyncio.sleep stand-in: records each delay in seconds, then raises `end_with` on the
    sleep after `stop_after_scans` sleeps (the current way the unbounded loop is ended)."""
    delays: list[float] = []

    async def fake_sleep(delay: float) -> None:
        delays.append(delay)
        if len(delays) > stop_after_scans:
            raise end_with

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    return delays


def _watchdog_manager(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    samples: dict[int, ProcSample | None],
    *,
    interval_seconds: float,
) -> tuple[RunManager, list[tuple[int, signal.Signals]]]:
    """A RunManager whose scan sees `samples` instead of /proc and whose killpg records the
    (pid, signal) pairs it is sent."""
    killed: list[tuple[int, signal.Signals]] = []

    def fake_sample(pid: int) -> ProcSample | None:
        return samples.get(pid)

    def fake_killpg(pid: int, sig: signal.Signals) -> None:
        killed.append((pid, sig))

    settings = Settings.from_env({"CLAUDE_RUNNER_DATA_DIR": str(tmp_path / "data")})
    object.__setattr__(settings, "watchdog_interval_seconds", interval_seconds)
    monkeypatch.setattr(runs, "group_sample", fake_sample)
    monkeypatch.setattr(os, "killpg", fake_killpg)
    return RunManager(settings), killed


def _register_proc(
    manager: RunManager, run_id: str, pid: int, *, qa: bool, started_ago: float = 0.0
) -> None:
    proc = cast("asyncio.subprocess.Process", _StubProc(pid))
    manager._procs[run_id] = proc
    manager._proc_started_at[proc] = time.monotonic() - started_ago
    if qa:
        manager._qa_runs.add(run_id)


def test_watchdog_loop_exit_on_cancelled_sleep(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The loop has no bound today: it leaves only when the sleep raises CancelledError,
    one scan per interval-sleep of the settings."""
    manager, killed = _watchdog_manager(
        tmp_path, monkeypatch, {}, interval_seconds=1.5
    )
    scans: list[int] = []
    monkeypatch.setattr(manager, "watchdog_scan", lambda: scans.append(1))
    delays = _fake_sleep(monkeypatch, stop_after_scans=2, end_with=asyncio.CancelledError)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(manager._watchdog_loop())

    assert delays == [1.5, 1.5, 1.5]
    assert len(scans) == 3
    assert killed == []


def test_watchdog_loop_exit_on_sleep_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The sleep sits outside the try: any error it raises ends the loop, after the scans
    of the iterations that ran."""
    manager, _ = _watchdog_manager(
        tmp_path, monkeypatch, {}, interval_seconds=0.25
    )
    scans: list[int] = []
    monkeypatch.setattr(manager, "watchdog_scan", lambda: scans.append(1))
    delays = _fake_sleep(monkeypatch, stop_after_scans=2, end_with=RuntimeError)

    with pytest.raises(RuntimeError):
        asyncio.run(manager._watchdog_loop())

    assert delays == [0.25, 0.25, 0.25]
    assert len(scans) == 3


def test_watchdog_loop_exit_on_scan_base_exception(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """KeyboardInterrupt is not an Exception: it escapes the scan's try and leaves the loop
    without reaching the sleep."""
    manager, _ = _watchdog_manager(
        tmp_path, monkeypatch, {}, interval_seconds=1.0
    )

    def interrupt_scan() -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(manager, "watchdog_scan", interrupt_scan)
    delays = _fake_sleep(monkeypatch, stop_after_scans=5, end_with=asyncio.CancelledError)

    with pytest.raises(KeyboardInterrupt):
        asyncio.run(manager._watchdog_loop())

    assert delays == []


def test_watchdog_loop_no_exit_when_scan_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A scan failure is logged and swallowed: the loop does not exit, it scans again."""
    manager, _ = _watchdog_manager(
        tmp_path, monkeypatch, {}, interval_seconds=1.0
    )
    attempts: list[int] = []

    def failing_scan() -> None:
        attempts.append(len(attempts) + 1)
        raise RuntimeError("scan failed")

    monkeypatch.setattr(manager, "watchdog_scan", failing_scan)
    delays = _fake_sleep(monkeypatch, stop_after_scans=2, end_with=asyncio.CancelledError)

    with pytest.raises(asyncio.CancelledError), caplog.at_level(logging.ERROR):
        asyncio.run(manager._watchdog_loop())

    assert len(attempts) == 3
    assert delays == [1.0, 1.0, 1.0]
    assert "watchdog scan failed" in caplog.text


def test_watchdog_loop_exit_scan_kills_overdue_qa_run_each_iteration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each iteration scans the registered processes: a QA run started past its time limit
    is killed with SIGKILL every interval until the loop is cancelled."""
    manager, killed = _watchdog_manager(
        tmp_path,
        monkeypatch,
        {101: ProcSample(pid=101, ppid=os.getpid(), cpu_seconds=5.0)},
        interval_seconds=0.5,
    )
    _register_proc(manager, "qa-run", 101, qa=True, started_ago=21 * 60)
    delays = _fake_sleep(monkeypatch, stop_after_scans=2, end_with=asyncio.CancelledError)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(manager._watchdog_loop())

    assert killed == [(101, signal.SIGKILL)] * 3
    assert manager._watchdog_codes == {"qa-run": "qa_timeout"}
    assert delays == [0.5, 0.5, 0.5]


def test_watchdog_loop_stops_at_max_scans(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A never-ending condition: neither the scan nor the sleep ends the loop, so only the
    bound can. The loop stops at `WATCHDOG_MAX_SCANS` scans and logs why. Without the bound
    the sleep stand-in raises after `spin_limit_iterations` iterations, so the test fails
    instead of running forever."""
    manager, _ = _watchdog_manager(
        tmp_path, monkeypatch, {}, interval_seconds=0.0
    )
    scans: list[int] = []
    delays: list[float] = []
    spin_limit_iterations = 50  # iterations: how far an unbound loop would spin before failing

    async def never_ending_sleep(delay: float) -> None:
        if len(scans) >= spin_limit_iterations:
            raise AssertionError("watchdog loop did not stop at the bound")
        delays.append(delay)

    monkeypatch.setattr(manager, "watchdog_scan", lambda: scans.append(1))
    monkeypatch.setattr(runs, "WATCHDOG_MAX_SCANS", 4)
    monkeypatch.setattr(asyncio, "sleep", never_ending_sleep)

    with caplog.at_level(logging.WARNING):
        asyncio.run(manager._watchdog_loop())

    assert len(scans) == 4
    assert delays == [0.0] * 4
    assert "watchdog loop stopped" in caplog.text


def test_watchdog_scan_exit_when_sample_is_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exit of the scan loop: a process whose state is gone is skipped, nothing is killed
    and no code or cpu baseline is recorded for it."""
    manager, killed = _watchdog_manager(
        tmp_path, monkeypatch, {101: None}, interval_seconds=60.0
    )
    _register_proc(manager, "worker-run", 101, qa=False)

    manager.watchdog_scan()

    assert killed == []
    assert manager._watchdog_codes == {}
    assert manager._cpu_at_event == {}


def test_watchdog_scan_exit_when_verdict_is_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exit of the scan loop: a run under the cpu limit and inside its time limit continues
    the loop with no kill and no recorded code."""
    manager, killed = _watchdog_manager(
        tmp_path,
        monkeypatch,
        {101: ProcSample(pid=101, ppid=os.getpid(), cpu_seconds=1.0)},
        interval_seconds=60.0,
    )
    _register_proc(manager, "worker-run", 101, qa=False)

    manager.watchdog_scan()

    assert killed == []
    assert manager._watchdog_codes == {}
    assert manager._cpu_at_event == {"worker-run": (0.0, 1.0)}


def test_watchdog_scan_exit_when_killpg_is_suppressed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exit of the scan loop: a dead or denied process group raises inside the suppress and
    the loop carries on to the next run, which is still killed."""
    killed: list[tuple[int, signal.Signals]] = []

    def fake_killpg(pid: int, sig: signal.Signals) -> None:
        if pid == 101:
            raise ProcessLookupError
        if pid == 102:
            raise PermissionError
        killed.append((pid, sig))

    cpu_seconds: dict[int, float] = {101: 1000.0, 102: 1000.0, 103: 1000.0}

    def fake_sample(pid: int) -> ProcSample:
        return ProcSample(pid=pid, ppid=os.getpid(), cpu_seconds=cpu_seconds.get(pid, 0.0))

    settings = Settings.from_env({"CLAUDE_RUNNER_DATA_DIR": str(tmp_path / "data")})
    object.__setattr__(settings, "watchdog_cpu_seconds", 900.0)
    monkeypatch.setattr(runs, "group_sample", fake_sample)
    monkeypatch.setattr(os, "killpg", fake_killpg)
    manager = RunManager(settings)
    _register_proc(manager, "dead-run", 101, qa=False)
    _register_proc(manager, "denied-run", 102, qa=False)
    _register_proc(manager, "alive-run", 103, qa=False)

    manager.watchdog_scan()  # first pass records the cpu baseline, nothing trips yet
    assert killed == []
    for pid in cpu_seconds:
        cpu_seconds[pid] = 2000.0  # 1000 s of cpu since the baseline: over the 900 limit
    manager.watchdog_scan()

    assert killed == [(103, signal.SIGKILL)]
    assert manager._watchdog_codes == {
        "dead-run": "child_hung",
        "denied-run": "child_hung",
        "alive-run": "child_hung",
    }
