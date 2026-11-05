from __future__ import annotations

from pathlib import Path

import pytest

from ahawr_runner import codex_cli
from ahawr_runner.config import ProviderProfile

SESSION_ID = "0190a1b2-c3d4-7e5f-8a9b-0c1d2e3f4a5b"
LAST_MESSAGE = "/d/mission/last-message.txt"
PROVIDER = ProviderProfile(
    name="codexlocal",
    env={"OPENAI_API_KEY": "sk-test-value", "ANTHROPIC_API_KEY": "sk-ant-test-value"},
    engine="codex",
)


def test_new_run_argv_has_no_sandbox_flag(role: str = "worker") -> None:
    argv = codex_cli.build_command(
        "codex", workspace="/d/mission", role=role, last_message_path=LAST_MESSAGE, prompt="fix"
    )
    assert argv == [
        "codex",
        "exec",
        "--json",
        "-C",
        "/d/mission",
        "-o",
        LAST_MESSAGE,
        "--skip-git-repo-check",
        "fix",
    ]


def test_resume_argv_sets_sandbox_by_config_override() -> None:
    argv = codex_cli.build_resume_command(
        "codex",
        session_id=SESSION_ID,
        role="worker",
        last_message_path=LAST_MESSAGE,
        prompt="continue",
    )
    assert argv == [
        "codex",
        "exec",
        "resume",
        "--json",
        "-o",
        LAST_MESSAGE,
        "--skip-git-repo-check",
        SESSION_ID,
        "continue",
    ]
    assert "-s" not in argv and "-c" not in argv
    assert "-C" not in argv


def test_resume_rejects_a_session_id_that_is_not_a_uuid() -> None:
    with pytest.raises(ValueError):
        codex_cli.build_resume_command(
            "codex",
            session_id="--last",
            role="worker",
            last_message_path=LAST_MESSAGE,
            prompt="continue",
        )


def test_child_env_keeps_only_basics_codex_home_and_the_one_key(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    for key, value in {
        "PATH": "/usr/bin",
        "HOME": "/home/worker",
        "LANG": "C.UTF-8",
        "TERM": "xterm",
        "GITHUB_TOKEN": "gh-test-value",
        "DB_PASSWORD": "db-test-value",
        "ANTHROPIC_API_KEY": "sk-ant-container-value",
        "CLAUDE_RUNNER_PROVIDER_CODEXLOCAL__OPENAI_API_KEY": "unused",
    }.items():
        monkeypatch.setenv(key, value)
    codex_home = tmp_path / "codex"

    env = codex_cli.child_env(PROVIDER, codex_home)

    assert env == {
        "PATH": "/usr/bin",
        "HOME": "/home/worker",
        "LANG": "C.UTF-8",
        "TERM": "xterm",
        "OPENAI_API_KEY": "sk-test-value",
        "CODEX_HOME": str(codex_home),
    }
