"""The api_types registry: one row per upstream API shape, and pure helpers over it.

A row with ``verified`` False is OFF by default. A capability value of None means not verified yet.
"""

from __future__ import annotations

import ipaddress
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal, TypedDict
from urllib.parse import urlsplit

AuthStyle = Literal["x-api-key", "bearer", "none"]
Route = Literal["direct", "litellm"]

NO_TOOLS_WARNING = "no_tools"
TEMPLATES_PATH = Path(__file__).with_name("provider_templates.json")
TEMPLATE_FIELDS = frozenset({"name", "base_url", "api_type", "hint", "verified"})

CAPABILITY_KEYS = frozenset(
    {
        "tools",
        "parallel_tools",
        "streaming",
        "system_message_position",
        "prompt_caching",
        "reasoning_or_thinking",
        "max_tokens_field",
        "usage_in_stream",
        "known_quirks",
    }
)


class ApiType(TypedDict):
    id: str
    label: str
    endpoint_path: str
    auth_style: AuthStyle
    needs_translator: bool
    litellm_prefix: str
    extra_fields: list[str]
    capabilities: dict[str, Any]
    verified: bool


API_TYPES: tuple[ApiType, ...] = (
    {
        "id": "anthropic",
        "label": "Anthropic Messages",
        "endpoint_path": "/v1/messages",
        "auth_style": "x-api-key",
        "needs_translator": False,
        "litellm_prefix": "anthropic/",
        "extra_fields": ["anthropic-version"],
        "capabilities": {
            "tools": None,
            "parallel_tools": None,
            "streaming": None,
            "system_message_position": "top-level system",
            "prompt_caching": None,
            "reasoning_or_thinking": None,
            "max_tokens_field": "max_tokens",
            "usage_in_stream": None,
            "known_quirks": "anthropic-version header; fetched summary called it optional",
        },
        "verified": True,
    },
    {
        "id": "openai-chat",
        "label": "OpenAI Chat Completions",
        "endpoint_path": "/chat/completions",
        "auth_style": "bearer",
        "needs_translator": True,
        "litellm_prefix": "openai/",
        "extra_fields": [],
        "capabilities": {
            "tools": None,
            "parallel_tools": None,
            "streaming": None,
            "system_message_position": None,
            "prompt_caching": None,
            "reasoning_or_thinking": None,
            "max_tokens_field": "max_tokens",
            "usage_in_stream": None,
            "known_quirks": "many local servers implement only part of the spec; "
            "LiteLLM drops unsupported fields",
        },
        "verified": True,
    },
    {
        "id": "openai-responses",
        "label": "OpenAI Responses",
        "endpoint_path": "/responses",
        "auth_style": "bearer",
        "needs_translator": True,
        "litellm_prefix": "openai/",
        "extra_fields": [],
        "capabilities": {
            "tools": None,
            "parallel_tools": None,
            "streaming": None,
            "system_message_position": None,
            "prompt_caching": None,
            "reasoning_or_thinking": None,
            "max_tokens_field": None,
            "usage_in_stream": None,
            "known_quirks": "off by default",
        },
        "verified": False,
    },
)

_BY_ID: dict[str, ApiType] = {row["id"]: row for row in API_TYPES}


def get_api_type(api_type: str) -> ApiType:
    if api_type not in _BY_ID:
        raise ValueError(f"unknown api_type: {api_type}")
    return _BY_ID[api_type]


def route_for(api_type: str) -> Route:
    return "litellm" if get_api_type(api_type)["needs_translator"] else "direct"


def effective_api_type(provider: Mapping[str, Any], model: str) -> str:
    override: Mapping[str, Any] = provider.get("models", {}).get(model, {})
    return get_api_type(override.get("api_type", provider["api_type"]))["id"]


def effective_capabilities(
    api_type: str, model_overrides: Mapping[str, Any] | None
) -> dict[str, Any]:
    capabilities = dict(get_api_type(api_type)["capabilities"])
    for key, value in (model_overrides or {}).items():
        if key not in CAPABILITY_KEYS:
            raise ValueError(f"unknown capability: {key}")
        capabilities[key] = value
    return capabilities


def worker_warning(capabilities: Mapping[str, Any]) -> str | None:
    return NO_TOOLS_WARNING if capabilities.get("tools") is False else None


class ProviderTemplate(TypedDict):
    name: str
    base_url: str
    api_type: str
    hint: str
    verified: bool


def check_public_base_url(base_url: str) -> None:
    if not base_url:
        return
    parts = urlsplit(base_url)
    host = parts.hostname
    if parts.scheme not in ("http", "https") or not host or "@" in parts.netloc:
        raise ValueError("base_url must be a public http(s) URL without credentials")
    if host == "localhost":
        raise ValueError("base_url must not be localhost")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return
    if not address.is_global:
        raise ValueError("base_url must not be a private or reserved IP address")


def load_templates(path: Path = TEMPLATES_PATH) -> list[ProviderTemplate]:
    rows: list[ProviderTemplate] = json.loads(path.read_text(encoding="utf-8"))
    for row in rows:
        if set(row) != TEMPLATE_FIELDS:
            name = row.get("name")
            raise ValueError(f"template {name!r} has fields {sorted(row)}, not the allowed set")
        get_api_type(row["api_type"])
        check_public_base_url(row["base_url"])
    return rows
