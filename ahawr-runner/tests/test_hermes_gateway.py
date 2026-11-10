from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from ahawr_runner import hermes_gateway
from ahawr_runner.config import ProviderProfile


def _profile(name: str, base_url: str = "http://strata:8080", key: str = "k-1") -> ProviderProfile:
    env = {"ANTHROPIC_BASE_URL": base_url}
    if key:
        env["ANTHROPIC_AUTH_TOKEN"] = key
    return ProviderProfile(name=name, env=env)


def test_the_catalog_lists_profiles_with_an_endpoint_and_a_key() -> None:
    profiles = {
        "STRATA": _profile("STRATA"),
        "NOKEY": _profile("NOKEY", key=""),
        "NOURL": ProviderProfile(name="NOURL", env={"ANTHROPIC_AUTH_TOKEN": "x"}),
        "CODEXLOCAL": ProviderProfile(
            name="CODEXLOCAL", engine="codex", env={"ANTHROPIC_BASE_URL": "http://a", "X": "y"}
        ),
        "VIAHERMES": ProviderProfile(
            name="VIAHERMES",
            engine="hermes",
            env={"ANTHROPIC_BASE_URL": "http://b", "ANTHROPIC_AUTH_TOKEN": "k"},
        ),
    }

    catalog = hermes_gateway.provider_catalog(profiles)

    assert list(catalog) == ["STRATA", "VIAHERMES"]
    assert catalog["STRATA"] == {
        "base_url": "http://strata:8080",
        "key_env": "AHAWR_PROVIDER_KEY_STRATA",
        "api_mode": "anthropic_messages",
    }


def test_the_config_file_has_no_key_value() -> None:
    text = hermes_gateway.render_config(hermes_gateway.provider_catalog({"S": _profile("S")}))

    assert "k-1" not in text
    assert json.loads(text)["providers"]["S"]["key_env"] == "AHAWR_PROVIDER_KEY_S"


def test_the_gateway_env_carries_the_keys_and_the_api_server_settings(tmp_path: Path) -> None:
    env = hermes_gateway.gateway_env({"S": _profile("S")}, tmp_path, 8642, "gw-key")

    assert env["AHAWR_PROVIDER_KEY_S"] == "k-1"
    assert env["API_SERVER_KEY"] == "gw-key"
    assert env["API_SERVER_HOST"] == "127.0.0.1"
    assert env["API_SERVER_PORT"] == "8642"
    assert env["HERMES_HOME"] == str(tmp_path)
    assert not any(name.startswith("CLAUDE_RUNNER_") for name in env)


def test_the_fingerprint_follows_the_config_and_every_env_value() -> None:
    base = hermes_gateway.fingerprint("a", {"K": "1"})

    assert hermes_gateway.fingerprint("a", {"K": "1"}) == base
    assert hermes_gateway.fingerprint("b", {"K": "1"}) != base
    assert hermes_gateway.fingerprint("a", {"K": "2"}) != base


def test_the_restart_delay_grows_and_is_capped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hermes_gateway, "RESTART_DELAY_START_SECONDS", 2.0)
    monkeypatch.setattr(hermes_gateway, "RESTART_DELAY_MAX_SECONDS", 10.0)

    delays = [hermes_gateway.restart_delay(n) for n in (0, 1, 2, 3, 20)]

    assert delays == [2.0, 2.0, 4.0, 8.0, 10.0]


class FakeProc:
    def __init__(self, exits_after: float | None) -> None:
        self.pid = 1
        self.returncode: int | None = None
        self.terminated = False
        self._exits_after = exits_after

    async def wait(self) -> int:
        if self._exits_after is None:
            while self.returncode is None:
                await asyncio.sleep(0.001)
        else:
            await asyncio.sleep(self._exits_after)
            self.returncode = 1
        return int(self.returncode or 0)

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = 0

    def kill(self) -> None:
        self.returncode = -9


def _supervisor(tmp_path: Path, profiles: dict[str, ProviderProfile]) -> Any:
    return hermes_gateway.GatewaySupervisor(
        lambda: profiles, home=tmp_path / "home", binary="hermes", port=8642, api_key="gw"
    )


@pytest.fixture(autouse=True)
def _fast(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hermes_gateway, "POLL_SECONDS", 0.05)
    monkeypatch.setattr(hermes_gateway, "RESTART_DELAY_START_SECONDS", 0.001)
    monkeypatch.setattr(hermes_gateway, "RESTART_DELAY_MAX_SECONDS", 0.002)


def _spawner(monkeypatch: pytest.MonkeyPatch, procs: list[FakeProc]) -> list[FakeProc]:
    started: list[FakeProc] = []

    async def fake_exec(*_args: Any, **_kwargs: Any) -> FakeProc:
        proc = procs.pop(0) if procs else FakeProc(None)
        started.append(proc)
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    return started


def test_prepare_writes_the_config_file(tmp_path: Path) -> None:
    sup = _supervisor(tmp_path, {"S": _profile("S")})

    env, stamp = sup.prepare()

    written = json.loads((tmp_path / "home" / hermes_gateway.CONFIG_FILE_NAME).read_text())
    assert "S" in written["providers"] and stamp and env["API_SERVER_KEY"] == "gw"


def test_a_changed_provider_list_restarts_the_gateway(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    profiles = {"S": _profile("S")}
    started = _spawner(monkeypatch, [])

    async def scenario() -> None:
        task = asyncio.create_task(_supervisor(tmp_path, profiles).run())
        await asyncio.sleep(0.3)
        profiles["S"] = _profile("S", key="k-2")
        await asyncio.sleep(0.4)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())

    assert len(started) == 2
    assert started[0].terminated and started[1].terminated  # the cancel stops the last one too


def test_a_process_that_keeps_exiting_is_given_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    started = _spawner(monkeypatch, [FakeProc(0.001) for _ in range(20)])

    asyncio.run(_supervisor(tmp_path, {"S": _profile("S")}).run())

    assert len(started) == hermes_gateway.MAX_FAST_FAILURES


def test_the_config_names_a_default_provider_preferring_the_hermes_engine() -> None:
    profiles = {
        "A": _profile("A"),
        "H": ProviderProfile(
            name="H",
            engine="hermes",
            env={"ANTHROPIC_BASE_URL": "http://h", "ANTHROPIC_AUTH_TOKEN": "k"},
        ),
    }
    catalog = hermes_gateway.provider_catalog(profiles)

    assert hermes_gateway.default_provider(profiles, catalog) == "H"
    assert hermes_gateway.default_provider({"A": profiles["A"]}, {"A": {}}) == "A"
    assert hermes_gateway.default_provider({}, {}) == ""
    text = hermes_gateway.render_config(catalog, "H")
    assert json.loads(text)["model"] == {"provider": "H"}
    assert "model" not in json.loads(hermes_gateway.render_config(catalog))


def test_render_config_limits_toolsets_and_sets_the_compression_trigger() -> None:
    text = hermes_gateway.render_config({}, "local", ("terminal", "file"), 131072, 90000)
    config = json.loads(text)
    assert config["platform_toolsets"] == {"api_server": ["terminal", "file"]}
    assert config["model"] == {"provider": "local", "context_length": 131072}
    assert config["compression"] == {"threshold_tokens": 90000}
    plain = json.loads(hermes_gateway.render_config({}, "local"))
    assert "platform_toolsets" not in plain and "compression" not in plain
