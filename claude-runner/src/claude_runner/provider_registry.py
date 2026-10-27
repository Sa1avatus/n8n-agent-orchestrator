"""The runner's provider list: the env blocks, plus the database behind ``providers_db``.

A database provider overrides an env block of the same name. The list reloads when the
store's ``providers_updated_at`` stamp moves (docs/providers.md, sections 11 and 12).
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Any, Protocol

from claude_runner.config import (
    CREDENTIAL_VARS,
    PROVIDERS_RELOAD_POLL_SECONDS,
    ProviderProfile,
    provider_key,
)
from claude_runner.provider_api_types import route_for
from claude_runner.provider_secrets import ProviderSecrets

logger = logging.getLogger(__name__)

RELOAD_MAX_BACKOFF_SECONDS = 300
# Credential env vars that mark a provider as keyed (mirrors api.PROVIDER_KEY_VARS).
_KEY_VARS = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN")


class ProviderStore(Protocol):
    def providers_updated_at(self) -> str: ...

    def list_providers(self) -> list[dict[str, Any]]: ...


@dataclass(frozen=True)
class ProviderEntry:
    """One provider as the registry keeps it: the profile plus the view data the API
    reports (api_type and models). ``key_state`` is read live from the secrets store
    by ``views()`` so it never goes stale. ``raw_name`` is the store's own provider name
    (the secrets store is keyed by it); env providers keep their own name."""

    profile: ProviderProfile
    api_type: str
    models: tuple[str, ...]
    raw_name: str


def poll_delay_seconds(poll_seconds: int, failures: int) -> int:
    if failures == 0:
        return poll_seconds
    return min(poll_seconds << failures, RELOAD_MAX_BACKOFF_SECONDS)


def _profile_from_row(row: dict[str, Any], api_key: str | None) -> ProviderProfile:
    # shortcut: api_type is not mapped, every row becomes Anthropic env; upgrade with routes
    env: dict[str, str] = {}
    if row.get("base_url"):
        env["ANTHROPIC_BASE_URL"] = str(row["base_url"])
    if api_key:
        env["ANTHROPIC_AUTH_TOKEN"] = api_key
    return ProviderProfile(
        name=provider_key(row["name"]),
        env=env,
        tools=row.get("tools") or "",
        compact_pct=row.get("compact_pct"),
        compact_min_pct=row.get("compact_min_pct"),
        max_output_tokens=row.get("max_output_tokens"),
        search_first=row.get("search_first"),
        retry_fresh_session=row.get("retry_fresh_session"),
    )


def _entry_from_row(row: dict[str, Any], api_key: str | None) -> ProviderEntry:
    api_type = str(row.get("api_type") or "anthropic")
    models = tuple(model.get("model", "") for model in row.get("models", []))
    return ProviderEntry(
        profile=_profile_from_row(row, api_key),
        api_type=api_type,
        models=models,
        raw_name=str(row.get("name", "")),
    )


ENV_KEY_VARS = tuple(var for var in CREDENTIAL_VARS if var != "ANTHROPIC_BASE_URL")


def env_provider_row(profile: ProviderProfile) -> dict[str, Any]:
    """What an import proposal needs of an env block: name, base URL and whether a key is set."""
    return {
        "name": profile.name,
        "base_url": profile.env.get("ANTHROPIC_BASE_URL", ""),
        "has_key": any(profile.env.get(var, "").strip() for var in ENV_KEY_VARS),
    }


class ProviderRegistry:
    def __init__(
        self,
        env_profiles: dict[str, ProviderProfile],
        *,
        providers_db: bool,
        store: ProviderStore,
        secrets: ProviderSecrets,
    ) -> None:
        self._env_profiles = dict(env_profiles)
        self._providers_db = providers_db
        self._store = store
        self._secrets = secrets
        self._lock = threading.Lock()
        self._stamp = ""
        self._entries: dict[str, ProviderEntry] = {
            name: ProviderEntry(profile, "anthropic", (), name)
            for name, profile in self._env_profiles.items()
        }
        # a failing store must not stop the app: the entries stay the env blocks
        # and the stamp stays empty, so the first good reload picks everything up
        if providers_db:
            try:
                self.reload()
            except Exception:
                with self._lock:
                    self._entries = {
                        name: ProviderEntry(profile, "anthropic", (), name)
                        for name, profile in self._env_profiles.items()
                    }
                    self._stamp = ""

    def env_profiles(self) -> dict[str, ProviderProfile]:
        return dict(self._env_profiles)

    def __repr__(self) -> str:
        with self._lock:
            names = sorted(self._entries)
        return f"ProviderRegistry(providers={names}, providers_db={self._providers_db})"

    def profiles(self) -> dict[str, ProviderProfile]:
        with self._lock:
            return {name: entry.profile for name, entry in self._entries.items()}

    def views(self) -> dict[str, dict[str, Any]]:
        """The view each provider's API row reports: name, api_type, route, models and
        the key state (has_key and set_at). The key value itself is never returned.
        has_key comes from the profile's env block (env blocks hold the key vars; a
        database provider's env block holds the key from the secrets store). set_at
        comes from the secrets store (None for env providers, which have no set_at)."""
        with self._lock:
            result = {}
            for name, entry in self._entries.items():
                has_key = any(entry.profile.env.get(k) for k in _KEY_VARS)
                set_at = self._secrets.key_state(entry.raw_name).set_at
                result[name] = {
                    "name": name,
                    "api_type": entry.api_type,
                    "route": route_for(entry.api_type),
                    "models": list(entry.models),
                    "key_state": {"has_key": has_key, "set_at": set_at},
                }
            return result

    def reload(self) -> None:
        stamp = self._store.providers_updated_at() if self._providers_db else ""
        self._apply(stamp)

    def refresh_if_changed(self) -> bool:
        if not self._providers_db:
            return False
        stamp = self._store.providers_updated_at()
        with self._lock:
            if stamp == self._stamp:
                return False
        self._apply(stamp)
        return True

    def start_polling(self) -> threading.Event | None:
        if not self._providers_db:
            return None
        stop = threading.Event()
        threading.Thread(
            target=self.run_poll_loop,
            args=(stop,),
            name="provider-registry-poll",
            daemon=True,
        ).start()
        return stop

    def run_poll_loop(self, stop: threading.Event) -> None:
        failures = 0
        while not stop.is_set():
            try:
                self.refresh_if_changed()
                failures = 0
            except Exception as exc:
                failures += 1
                logger.warning("provider reload failed (%d in a row): %s", failures, exc)
            stop.wait(poll_delay_seconds(PROVIDERS_RELOAD_POLL_SECONDS, failures))

    def _apply(self, stamp: str) -> None:
        entries = self._merged()
        with self._lock:
            self._entries = entries
            self._stamp = stamp
        logger.info("providers loaded: %d (database: %s)", len(entries), self._providers_db)

    def _merged(self) -> dict[str, ProviderEntry]:
        merged = {
            name: ProviderEntry(profile, "anthropic", (), name)
            for name, profile in self._env_profiles.items()
        }
        if not self._providers_db:
            return merged
        for row in self._store.list_providers():
            if not row["enabled"]:
                continue
            api_key = self._secrets.get_key(row["name"])
            merged[provider_key(row["name"])] = _entry_from_row(row, api_key)
        return merged
