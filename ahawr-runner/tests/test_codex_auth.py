from __future__ import annotations

import json
import sys
import tomllib
from pathlib import Path

import pytest

from ahawr_runner.codex_auth import AUTH_FILE_NAME, stage_auth, sync_auth_back
from ahawr_runner.codex_config import render_config_toml
from ahawr_runner.config import DEFAULT_CODEX_LOGIN_HOME, ProviderProfile

PROVIDER = "CLAUDE_RUNNER_PROVIDER_CODEXSUB__"


def _chatgpt() -> ProviderProfile:
    return ProviderProfile(name="codexsub", codex_auth="chatgpt")


def test_the_login_is_copied_into_the_run_home_and_kept_private(tmp_path: Path) -> None:
    login, home = tmp_path / "login", tmp_path / "home"
    login.mkdir()
    (login / AUTH_FILE_NAME).write_text('{"tokens": {"a": 1}}', encoding="utf-8")
    assert stage_auth(str(login), home) is True
    assert json.loads((home / AUTH_FILE_NAME).read_text(encoding="utf-8")) == {"tokens": {"a": 1}}


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX file modes")
def test_the_staged_login_is_readable_by_the_owner_only(tmp_path: Path) -> None:
    login, home = tmp_path / "login", tmp_path / "home"
    login.mkdir()
    (login / AUTH_FILE_NAME).write_text("{}", encoding="utf-8")
    stage_auth(str(login), home)
    assert (home / AUTH_FILE_NAME).stat().st_mode & 0o077 == 0


@pytest.mark.parametrize("content", [None, "not json"])
def test_no_usable_login_is_reported(tmp_path: Path, content: str | None) -> None:
    login = tmp_path / "login"
    login.mkdir()
    if content is not None:
        (login / AUTH_FILE_NAME).write_text(content, encoding="utf-8")
    assert stage_auth(str(login), tmp_path / "home") is False
    assert not (tmp_path / "home" / AUTH_FILE_NAME).exists()


def test_a_refreshed_token_is_written_back_and_an_unchanged_one_is_not(tmp_path: Path) -> None:
    login, home = tmp_path / "login", tmp_path / "home"
    login.mkdir()
    (login / AUTH_FILE_NAME).write_text('{"t": 1}', encoding="utf-8")
    stage_auth(str(login), home)
    assert sync_auth_back(str(login), home) is False
    (home / AUTH_FILE_NAME).write_text('{"t": 2}', encoding="utf-8")
    assert sync_auth_back(str(login), home) is True
    assert json.loads((login / AUTH_FILE_NAME).read_text(encoding="utf-8")) == {"t": 2}


def test_a_broken_file_in_the_run_home_never_replaces_the_login(tmp_path: Path) -> None:
    login, home = tmp_path / "login", tmp_path / "home"
    login.mkdir()
    home.mkdir()
    (login / AUTH_FILE_NAME).write_text('{"t": 1}', encoding="utf-8")
    (home / AUTH_FILE_NAME).write_text("{broken", encoding="utf-8")
    assert sync_auth_back(str(login), home) is False
    assert json.loads((login / AUTH_FILE_NAME).read_text(encoding="utf-8")) == {"t": 1}


def test_chatgpt_config_uses_codexs_own_provider_and_no_key() -> None:
    config = tomllib.loads(render_config_toml(_chatgpt(), "worker", "gpt-5", "/w", None))
    assert config["model"] == "gpt-5"
    assert "model_provider" not in config
    assert "model_providers" not in config
    assert config["sandbox_mode"] == "workspace-write"


def test_api_key_config_is_unchanged() -> None:
    profile = ProviderProfile(name="openai")
    config = tomllib.loads(render_config_toml(profile, "worker", "gpt-5", "/w", None))
    assert config["model_provider"] == "openai"
    assert "openai" in config["model_providers"]


def test_the_provider_setting_is_parsed_and_checked() -> None:
    from ahawr_runner.config import Settings

    base = {"CLAUDE_RUNNER_API_KEY": "x"}
    env = {**base, f"{PROVIDER}ENGINE": "codex", f"{PROVIDER}CODEX_AUTH": "chatgpt"}
    profile = Settings.from_env(env).providers["CODEXSUB"]
    assert (profile.codex_auth, profile.codex_login_home) == ("chatgpt", DEFAULT_CODEX_LOGIN_HOME)
    default = Settings.from_env({**base, f"{PROVIDER}ENGINE": "codex"}).providers["CODEXSUB"]
    assert default.codex_auth == "api_key"
    with pytest.raises(ValueError, match="CODEX_AUTH must be one of api_key, chatgpt"):
        Settings.from_env({**base, f"{PROVIDER}ENGINE": "codex", f"{PROVIDER}CODEX_AUTH": "key"})
