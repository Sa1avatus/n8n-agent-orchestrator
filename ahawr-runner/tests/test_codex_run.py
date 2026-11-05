from __future__ import annotations

import json
import stat
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ahawr_runner import codex_run
from ahawr_runner.api import create_app
from ahawr_runner.config import ProviderProfile, Settings
from ahawr_runner.runs import CODEX_HANDOFF_PREAMBLE
from ahawr_runner.store import Store

from .conftest import calls, wait_for
from .test_runs import start

FAKE_CODEX = Path(__file__).with_name("fake_codex.py")
THREAD_ID = "0195a1c2-7d3e-7c10-9a4b-2f6e1d8c0a11"
NEW_THREAD_ID = "0195a1c2-7d3e-7c10-9a4b-2f6e1d8c0a22"
THREAD_SWITCH_NAME = "switch-to-new-thread"
ARGV_END = "%%END%%"
FINAL_MESSAGE = "Added sub() to calc.py."


@pytest.fixture
def codex_log(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    log = tmp_path / "codex-argv.txt"
    wrapper = tmp_path / "codex"
    switch = tmp_path / THREAD_SWITCH_NAME
    wrapper.write_text(
        "#!/bin/sh\n"
        f'printf \'%s\\n\' "$@" "{ARGV_END}" >> "{log}"\n'
        f'if [ -f "{switch}" ]; then export FAKE_CODEX_THREAD_ID={NEW_THREAD_ID}; fi\n'
        f'exec "{sys.executable}" "{FAKE_CODEX}" "$@"\n',
        encoding="utf-8",
    )
    wrapper.chmod(wrapper.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setattr(codex_run, "CODEX_BIN", str(wrapper))
    return log


@pytest.fixture
def codex_settings(settings: Settings) -> Settings:
    providers = {
        "CODEXLOCAL": ProviderProfile(
            name="codexlocal",
            env={"OPENAI_API_KEY": "sk-test-value"},
            engine="codex",
            price_usd_per_million_tokens=(1.0, 2.0, 0.5, 0.0),
        ),
        "PLAIN": ProviderProfile(name="plain", env={}),
    }
    object.__setattr__(settings, "providers", providers)
    return settings


def codex_argvs(log: Path) -> list[list[str]]:
    if not log.exists():
        return []
    chunks = log.read_text(encoding="utf-8").split(f"{ARGV_END}\n")
    return [chunk.splitlines() for chunk in chunks if chunk.strip()]


def kinds_of(settings: Settings, run_id: str) -> list[dict[str, object]]:
    lines = (settings.data_dir / "events" / f"{run_id}.jsonl").read_text(encoding="utf-8")
    return [json.loads(line) for line in lines.splitlines()]


def test_codex_run_streams_events_result_and_session(
    codex_settings: Settings, codex_log: Path
) -> None:
    with TestClient(create_app(codex_settings)) as client:
        run = start(client, provider="codexlocal", role="worker", input="add sub()")
        done = wait_for(client, str(run["run_id"]))
    assert done["status"] == "completed", done
    assert done["output"] == FINAL_MESSAGE
    assert done["error"] == ""
    assert done["cost_usd"] is not None and done["num_turns"] == 1

    events = kinds_of(codex_settings, str(run["run_id"]))
    assert events[0]["kind"] == "prompt" and events[0]["resume"] is False
    assert any(e["kind"] == "text" and e["text"] == FINAL_MESSAGE for e in events)
    assert events[-1]["kind"] == "end" and events[-1]["status"] == "completed"

    session = Store(codex_settings.data_dir / "runner.sqlite").get_session(str(run["session_id"]))
    assert session is not None and session["claude_session_id"] == THREAD_ID

    (first_argv,) = codex_argvs(codex_log)
    assert first_argv[:2] == ["exec", "--json"] and "resume" not in first_argv


def test_codex_follow_up_resumes_the_stored_thread(
    codex_settings: Settings, codex_log: Path
) -> None:
    with TestClient(create_app(codex_settings)) as client:
        first = start(client, provider="codexlocal", role="worker", input="add sub()")
        wait_for(client, str(first["run_id"]))
        second = start(
            client,
            provider="codexlocal",
            role="worker",
            session_id=str(first["session_id"]),
            input="continue",
        )
        done = wait_for(client, str(second["run_id"]))
    assert second["session_created"] is False
    assert done["status"] == "completed" and done["output"] == FINAL_MESSAGE

    assert len(codex_argvs(codex_log)) == 2
    second_argv = codex_argvs(codex_log)[1]
    assert "resume" in second_argv and THREAD_ID in second_argv
    assert second_argv[-1] == "continue"
    assert kinds_of(codex_settings, str(second["run_id"]))[0]["resume"] is True


def test_codex_compact_starts_a_new_thread_seeded_with_the_task_and_digest(
    codex_settings: Settings, codex_log: Path
) -> None:
    with TestClient(create_app(codex_settings)) as client:
        first = start(client, provider="codexlocal", role="worker", input="add sub()")
        wait_for(client, str(first["run_id"]))
        codex_log.with_name(THREAD_SWITCH_NAME).touch()
        compacted = client.post(
            f"/v1/sessions/{first['session_id']}/compact", json={"mode": "always"}
        ).json()
        session = Store(codex_settings.data_dir / "runner.sqlite").get_session(
            str(first["session_id"])
        )
        assert session is not None and session["claude_session_id"] == NEW_THREAD_ID
        follow_up = start(
            client,
            provider="codexlocal",
            role="worker",
            session_id=str(first["session_id"]),
            input="continue",
        )
        assert wait_for(client, str(follow_up["run_id"]))["status"] == "completed"

    assert compacted["status"] == "completed", compacted
    assert compacted["compression_completed"] is True
    assert compacted["pre_tokens"] is None and compacted["post_tokens"] is None

    argvs = codex_argvs(codex_log)
    assert len(argvs) == 3
    compact_argv = argvs[1]
    assert "resume" not in compact_argv
    handoff = "\n".join(compact_argv)
    assert CODEX_HANDOFF_PREAMBLE in handoff
    assert "TASK: add sub()" in handoff
    assert f"ASSISTANT: {FINAL_MESSAGE}" in handoff
    follow_up_argv = argvs[2]
    assert "resume" in follow_up_argv and NEW_THREAD_ID in follow_up_argv


def test_codex_auth_failure_is_a_failed_run_with_its_class(
    codex_settings: Settings, codex_log: Path
) -> None:
    with TestClient(create_app(codex_settings)) as client:
        run = start(
            client,
            provider="codexlocal",
            role="worker",
            input="[[fixture:permanent_auth]]",
        )
        done = wait_for(client, str(run["run_id"]))
    assert done["status"] == "failed"
    assert done["error"]["code"] == "auth_error"


def test_run_without_engine_still_runs_the_claude_path(
    codex_settings: Settings, codex_log: Path, tmp_path: Path
) -> None:
    with TestClient(create_app(codex_settings)) as client:
        run = start(client, provider="plain", input="hello")
        done = wait_for(client, str(run["run_id"]))
    assert done["status"] == "completed"
    assert calls(tmp_path)[-1]["prompt"] == "hello"
    assert codex_argvs(codex_log) == []
