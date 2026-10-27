"""LiteLLM model sync (litellm_sync, docs/providers.md section 13).

``register`` adds a provider model to a LiteLLM proxy with POST /model/new; ``sync_state`` reports
whether GET /model/info lists it. Disabled, no request is made. Anthropic routes go direct and
are never registered here.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import httpx

from claude_runner.provider_api_types import get_api_type

SYNCED = "synced"
NOT_SYNCED = "not synced"
REQUEST_TIMEOUT_SECONDS = 10.0
MAX_ATTEMPTS = 3
RETRY_DELAY_SECONDS = 1.0


class LitellmError(RuntimeError):
    pass


@dataclass(frozen=True)
class LitellmRoute:
    alias: str
    api_type: str
    upstream_model: str
    api_base: str

    def litellm_model(self) -> str:
        return get_api_type(self.api_type)["litellm_prefix"] + self.upstream_model


class LitellmClient:
    def __init__(
        self,
        base_url: str,
        master_key: str,
        *,
        enabled: bool,
        retry_delay_seconds: float = RETRY_DELAY_SECONDS,
    ) -> None:
        if enabled and not base_url:
            raise ValueError("litellm URL is not set")
        self._base_url = base_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {master_key}"} if master_key else {}
        self._enabled = enabled
        self._retry_delay_seconds = retry_delay_seconds

    def register(self, route: LitellmRoute) -> bool:
        if not self._applies(route):
            return False
        body = {
            "model_name": route.alias,
            "litellm_params": {"model": route.litellm_model(), "api_base": route.api_base},
        }
        response = self._call("POST", "/model/new", body)
        if not response.is_success:
            raise LitellmError(f"litellm /model/new: HTTP {response.status_code}")
        return True

    def sync_state(self, route: LitellmRoute) -> str | None:
        if not self._applies(route):
            return None
        response = self._call("GET", "/model/info")
        if not response.is_success:
            raise LitellmError(f"litellm /model/info: HTTP {response.status_code}")
        listed = response.json().get("data") or []
        for entry in listed:
            params = entry.get("litellm_params") or {}
            listed_model = params.get("model")
            if entry.get("model_name") == route.alias and listed_model == route.litellm_model():
                return SYNCED
        return NOT_SYNCED

    def _applies(self, route: LitellmRoute) -> bool:
        return self._enabled and route.api_type != "anthropic"

    def _call(self, method: str, path: str, body: dict[str, Any] | None = None) -> httpx.Response:
        failure = ""
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                response = httpx.request(
                    method,
                    self._base_url + path,
                    headers=self._headers,
                    json=body,
                    timeout=REQUEST_TIMEOUT_SECONDS,
                )
            except httpx.TransportError as exc:
                failure = type(exc).__name__
            else:
                if response.status_code < 500:
                    return response
                failure = f"HTTP {response.status_code}"
            if attempt < MAX_ATTEMPTS:
                time.sleep(self._retry_delay_seconds * attempt)
        raise LitellmError(
            f"litellm {method} {path} failed after {MAX_ATTEMPTS} attempts: {failure}"
        )
