"""The Hermes gateway inside the runner container: its provider catalog and its process.

Hermes (installed in the image with ``INSTALL_HERMES=true``) answers ``POST /v1/runs`` with the
``model`` and ``provider`` the runner sends (hermes_run.start_run). It looks the provider name up
in the ``providers:`` section of its ``config.yaml``. This module writes that section from the
runner's own provider registry, so there is one list of providers, and runs the gateway as a
child process: it restarts the process with a growing delay when it exits, and at once when the
provider list or a key changes. Keys reach the gateway only through its process environment,
never through the file.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import os
import re
from collections.abc import Callable, Mapping
from pathlib import Path

from .config import ProviderProfile

log = logging.getLogger(__name__)

GATEWAY_PORT = 8642
CONFIG_FILE_NAME = "config.yaml"
KEY_ENV_PREFIX = "AHAWR_PROVIDER_KEY_"
KEY_VARS = ("ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY")
BASE_URL_VAR = "ANTHROPIC_BASE_URL"
# shortcut: every runner profile is an Anthropic endpoint; map api_type for an OpenAI-only one
API_MODE = "anthropic_messages"
SHELL_VARS = ("PATH", "LANG", "TERM")
POLL_SECONDS = 5.0
RESTART_DELAY_START_SECONDS = 2.0
RESTART_DELAY_MAX_SECONDS = 60.0
STABLE_RUN_SECONDS = 60.0
MAX_FAST_FAILURES = 8
STOP_GRACE_SECONDS = 10.0


def key_env_name(provider: str) -> str:
    return KEY_ENV_PREFIX + re.sub(r"[^A-Z0-9]+", "_", provider.upper()).strip("_")


def _key_of(profile: ProviderProfile) -> str:
    return next((profile.env[var] for var in KEY_VARS if profile.env.get(var)), "")


def provider_catalog(profiles: Mapping[str, ProviderProfile]) -> dict[str, dict[str, str]]:
    """The ``providers:`` entries: the profiles that have an endpoint and a key (a profile of the
    Hermes engine too: its name is what the runner sends as ``provider``).  The key itself is not
    here, only the name of the environment variable that holds it."""
    catalog: dict[str, dict[str, str]] = {}
    for profile in profiles.values():
        base_url = profile.env.get(BASE_URL_VAR, "").strip()
        if profile.engine == "codex" or not base_url or not _key_of(profile):
            continue
        catalog[profile.name] = {
            "base_url": base_url,
            "key_env": key_env_name(profile.name),
            "api_mode": API_MODE,
        }
    return catalog


def default_provider(
    profiles: Mapping[str, ProviderProfile], catalog: Mapping[str, object]
) -> str:
    """The gateway resolves its own default provider before it applies the request's, so
    config.yaml names one: the first Hermes-engine profile in the catalog, else the first entry."""
    hermes = [name for name, profile in profiles.items() if profile.engine == "hermes"]
    return next((name for name in hermes if name in catalog), next(iter(catalog), ""))


def render_config(catalog: Mapping[str, dict[str, str]], default: str = "") -> str:
    # JSON is valid YAML, so the file needs no YAML writer.
    config: dict[str, object] = {"providers": dict(catalog)}
    if default:
        config["model"] = {"provider": default}
    return json.dumps(config, indent=2, sort_keys=True) + "\n"


def gateway_env(
    profiles: Mapping[str, ProviderProfile], home: Path, port: int, api_key: str
) -> dict[str, str]:
    env = {name: os.environ[name] for name in SHELL_VARS if name in os.environ}
    env.update(
        HOME=str(home),
        HERMES_HOME=str(home),
        API_SERVER_ENABLED="true",
        API_SERVER_HOST="127.0.0.1",
        API_SERVER_PORT=str(port),
        API_SERVER_KEY=api_key,
    )
    catalog = provider_catalog(profiles)
    for profile in profiles.values():
        if profile.name in catalog:
            env[key_env_name(profile.name)] = _key_of(profile)
    return env


def fingerprint(config_text: str, env: Mapping[str, str]) -> str:
    """Changes when the catalog or any value the gateway gets changes; the values are not kept."""
    digest = hashlib.sha256(config_text.encode())
    for name in sorted(env):
        digest.update(f"\0{name}={env[name]}".encode())
    return digest.hexdigest()


def restart_delay(fast_failures: int) -> float:
    delay = RESTART_DELAY_START_SECONDS * float(2 ** max(fast_failures - 1, 0))
    return min(delay, RESTART_DELAY_MAX_SECONDS)


class GatewaySupervisor:
    def __init__(
        self,
        profiles: Callable[[], Mapping[str, ProviderProfile]],
        *,
        home: Path,
        binary: str,
        port: int,
        api_key: str,
    ) -> None:
        self._profiles = profiles
        self._home = home
        self._binary = binary
        self._port = port
        self._api_key = api_key
        self.restarts = 0

    def prepare(self) -> tuple[dict[str, str], str]:
        """Write config.yaml and return the process environment with the fingerprint of both."""
        profiles = self._profiles()
        catalog = provider_catalog(profiles)
        text = render_config(catalog, default_provider(profiles, catalog))
        self._home.mkdir(parents=True, exist_ok=True)
        (self._home / CONFIG_FILE_NAME).write_text(text, encoding="utf-8")
        env = gateway_env(profiles, self._home, self._port, self._api_key)
        return env, fingerprint(text, env)

    async def run(self) -> None:
        """Keep one gateway alive until cancelled; stop after MAX_FAST_FAILURES fast exits."""
        fast_failures = 0
        loop = asyncio.get_running_loop()
        while fast_failures < MAX_FAST_FAILURES:
            env, stamp = self.prepare()
            started = loop.time()
            proc = await asyncio.create_subprocess_exec(
                self._binary, "gateway", "run", env=env, cwd=str(self._home),
                stdin=asyncio.subprocess.DEVNULL,
            )
            log.info("hermes gateway started (pid %s)", proc.pid)
            try:
                changed = await self._watch(proc, stamp)
            finally:
                await self._stop(proc)
            if changed:  # a new provider list is a planned restart, not a failure
                fast_failures = 0
                self.restarts += 1
                continue
            fast_failures = 0 if loop.time() - started >= STABLE_RUN_SECONDS else fast_failures + 1
            self.restarts += 1
            log.warning("gateway exited (%s), restart %d", proc.returncode, self.restarts)
            await asyncio.sleep(restart_delay(fast_failures))
        log.error("hermes gateway gave up after %d quick exits in a row", MAX_FAST_FAILURES)

    async def _watch(self, proc: asyncio.subprocess.Process, stamp: str) -> bool:
        """True when the provider list changed, False when the process exited."""
        while proc.returncode is None:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(proc.wait(), timeout=POLL_SECONDS)
            if proc.returncode is None and self.prepare()[1] != stamp:
                return True
        return False

    @staticmethod
    async def _stop(proc: asyncio.subprocess.Process) -> None:
        if proc.returncode is not None:
            return
        proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), timeout=STOP_GRACE_SECONDS)
        except TimeoutError:
            proc.kill()
            await proc.wait()
