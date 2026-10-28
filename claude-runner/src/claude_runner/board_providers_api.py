"""Provider routes under /api/board/providers: list, get, create, update, delete, models, the
write-only key, the test and probe calls, and the import proposal from the env blocks.

Every response carries ``key_state`` (``has_key`` and ``set_at``) from the secrets file, never a
key. A PUT with ``dry_run=true`` returns the diff without writing, the confirm step of the settings.
The test and probe routes send requests through an injected ``post`` callable.
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Callable, Mapping
from typing import Any, Protocol, cast
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, FastAPI, Query, Request

from claude_runner.board import ConflictError, Mission
from claude_runner.board_api import _require_dashboard_header, _store_guard
from claude_runner.config import ProviderProfile
from claude_runner.provider_api_types import (
    CAPABILITY_KEYS,
    ApiType,
    get_api_type,
    load_templates,
    worker_warning,
)
from claude_runner.provider_registry import env_provider_row
from claude_runner.provider_secrets import ProviderSecrets
from claude_runner.roles_config import MODEL_SEPARATOR, split_model

logger = logging.getLogger(__name__)

_PROVIDER_NAME_RE = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")
_MODEL_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}")
_CONTROL_KEYS = frozenset({"expected_version", "dry_run"})

PostFn = Callable[[str, dict[str, str], dict[str, Any], float], int]
GetFn = Callable[[str, dict[str, str], float], tuple[int, Any]]
PROVIDER_CALL_TIMEOUT_SECONDS = 30.0
ANTHROPIC_VERSION = "2023-06-01"
KEY_MAX_CHARS = 4096
MODELS_PATHS = {"anthropic": "/v1/models"}  # every other API type lists at /models
ANTHROPIC_HOST = "api.anthropic.com"
MODELS_PAGE_SIZE = 1000  # the Anthropic list is paged (20 by default); 1000 is its largest page
MODELS_MAX_PAGES = 10
TEST_MAX_TOKENS = 1
PROBE_MAX_TOKENS = 16
PROBE_PROMPT = "Reply with one word."
PROBE_SYSTEM = "You are a terse assistant."
PROBE_TOOL_NAME = "probe_ping"

Validator = Callable[[str, Any], None]


class ProviderStore(Protocol):
    def list_providers(self) -> list[dict[str, Any]]: ...
    def get_provider(self, name: str) -> dict[str, Any] | None: ...
    def create_provider(self, name: str, fields: dict[str, Any]) -> None: ...
    def update_provider(
        self, name: str, fields: dict[str, Any], expected_version: str | None
    ) -> None: ...
    def delete_provider(self, name: str, expected_version: str | None) -> None: ...
    def add_model(self, provider: str, model: str, fields: dict[str, Any]) -> None: ...
    def update_model(
        self, provider: str, model: str, fields: dict[str, Any], expected_version: str | None
    ) -> None: ...
    def remove_model(self, provider: str, model: str, expected_version: str | None) -> None: ...
    def set_model_probe(self, provider: str, model: str, probe_result: dict[str, Any]) -> None: ...
    def hermes_config(self) -> dict[str, Any] | None: ...
    def list_missions(self) -> list[Mission]: ...
    def mission_config(self, mission_id: str) -> dict[str, Any] | None: ...


def _flag_value(key: str, value: Any) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{key} must be true or false")
    return value


def _text(max_chars: int) -> Validator:
    def check(key: str, value: Any) -> None:
        if not isinstance(value, str) or len(value) > max_chars:
            raise ValueError(f"{key} must be text of at most {max_chars} characters")

    return check


def _number(low: float, high: float, *, whole: bool) -> Validator:
    def check(key: str, value: Any) -> None:
        kind = "a whole number" if whole else "a number"
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise ValueError(f"{key} must be {kind}")
        if whole and not isinstance(value, int):
            raise ValueError(f"{key} must be {kind}")
        if not low <= value <= high:
            raise ValueError(f"{key} must be between {low:g} and {high:g}")

    return check


def _flag(key: str, value: Any) -> None:
    _flag_value(key, value)


def _api_type(key: str, value: Any) -> None:
    if not isinstance(value, str):
        raise ValueError(f"{key} must be text")
    get_api_type(value)


def _base_url(key: str, value: Any) -> None:
    if not isinstance(value, str) or len(value) > 2048:
        raise ValueError(f"{key} must be text of at most 2048 characters")
    parts = urlsplit(value)
    if parts.scheme not in ("http", "https") or not parts.hostname or "@" in parts.netloc:
        raise ValueError(f"{key} must be an http or https URL with a host and no userinfo")


def _capabilities(key: str, value: Any) -> None:
    if not isinstance(value, dict) or not set(value) <= CAPABILITY_KEYS:
        raise ValueError(f"{key} must be an object with keys from {sorted(CAPABILITY_KEYS)}")


PROVIDER_CHECKS: dict[str, Validator] = {
    "label": _text(100),
    "api_type": _api_type,
    "base_url": _base_url,
    "enabled": _flag,
    "context_tokens": _number(1, 10_000_000, whole=True),
    "compact_pct": _number(1, 100, whole=False),
    "compact_min_pct": _number(1, 100, whole=False),
    "max_output_tokens": _number(1, 1_000_000, whole=True),
    "tools": _text(500),
    "search_first": _flag,
    "retry_fresh_session": _flag,
    "notes": _text(2000),
}
PROVIDER_NON_NULL = frozenset({"enabled", "api_type", "base_url"})
PROVIDER_REQUIRED = frozenset({"api_type", "base_url"})

MODEL_CHECKS: dict[str, Validator] = {
    "alias": _text(100),
    "price_input_per_mtok_usd": _number(0, 1000, whole=False),
    "price_output_per_mtok_usd": _number(0, 1000, whole=False),
    "supports_thinking": _flag,
    "api_type": _api_type,
    "capability_overrides": _capabilities,
}
MODEL_NON_NULL = frozenset({"supports_thinking"})


def _check_fields(
    fields: dict[str, Any],
    checks: dict[str, Validator],
    non_null: frozenset[str],
    required: frozenset[str] = frozenset(),
) -> None:
    unknown = sorted(set(fields) - set(checks))
    if unknown:
        raise ValueError(f"unknown field(s): {', '.join(unknown)}")
    missing = sorted(required - set(fields))
    if missing:
        raise ValueError(f"missing field(s): {', '.join(missing)}")
    for key, value in fields.items():
        if value is None and key in non_null:
            raise ValueError(f"{key} must not be null")
        if value is not None:
            checks[key](key, value)


def _name(value: Any, pattern: re.Pattern[str], what: str) -> str:
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise ValueError(f"{what} must match {pattern.pattern}")
    return value


def _expected(value: Any) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("expected_version is required")
    return value


def _roles_providers(value: Any) -> set[str]:
    if isinstance(value, str):
        return {split_model(value)[0]} if MODEL_SEPARATOR in value else set()
    if isinstance(value, dict):
        children: list[Any] = list(value.values())
    elif isinstance(value, list):
        children = value
    else:
        return set()
    found: set[str] = set()
    for child in children:
        found |= _roles_providers(child)
    return found


def _uses_provider(config: dict[str, Any], name: str) -> bool:
    if any(value == name for key, value in config.items() if key.endswith("_provider")):
        return True
    roles = config.get("roles_json") or ""
    return bool(roles) and name in _roles_providers(json.loads(roles))


def _provider_users(store: ProviderStore, name: str) -> list[str]:
    users = ["the default config"] if _uses_provider(store.hermes_config() or {}, name) else []
    for mission in store.list_missions():
        if _uses_provider(store.mission_config(mission.mission_id) or {}, name):
            users.append(mission.mission_id)
    return users


def _require(store: ProviderStore, name: str) -> dict[str, Any]:
    view = store.get_provider(name)
    if view is None:
        raise KeyError(f"provider {name!r} not found")
    return view


def _check_expected(view: dict[str, Any], expected: str) -> None:
    if view["version"] != expected:
        raise ConflictError(
            f"provider {view['name']!r}: expected version {expected}, got {view['version']}"
        )


def _diff(current: dict[str, Any], fields: dict[str, Any]) -> dict[str, Any]:
    return {
        key: {"before": current.get(key), "after": value}
        for key, value in fields.items()
        if current.get(key) != value
    }


def _key_state(name: str, secrets: ProviderSecrets) -> dict[str, Any]:
    state = secrets.key_state(name)
    return {"has_key": state.has_key, "set_at": state.set_at}


def _with_key_state(view: dict[str, Any], secrets: ProviderSecrets) -> dict[str, Any]:
    return {**view, "key_state": _key_state(view["name"], secrets)}


def _key_value(body: dict[str, Any]) -> str:
    unknown = sorted(set(body) - {"key"})
    if unknown:
        raise ValueError(f"unknown field(s): {', '.join(unknown)}")
    key = body.get("key")
    if not isinstance(key, str) or not key.strip() or len(key) > KEY_MAX_CHARS:
        raise ValueError(f"key must be text of 1 to {KEY_MAX_CHARS} characters")
    return key


def _model_row(provider: dict[str, Any], body: dict[str, Any]) -> dict[str, Any]:
    unknown = sorted(set(body) - {"model"})
    if unknown:
        raise ValueError(f"unknown field(s): {', '.join(unknown)}")
    model = _name(body.get("model"), _MODEL_NAME_RE, "model")
    rows: list[dict[str, Any]] = provider["models"]
    for row in rows:
        if row["model"] == model:
            return row
    raise KeyError(f"model {model!r} not found")


def _tested_api_type(provider: dict[str, Any], row: dict[str, Any]) -> ApiType:
    api = get_api_type(row.get("api_type") or provider["api_type"])
    if not api["verified"]:
        raise ValueError(f"api_type {api['id']!r} is not verified")
    return api


def _auth_headers(key: str, api: ApiType) -> dict[str, str]:
    headers: dict[str, str] = {}
    if api["auth_style"] == "x-api-key":
        headers["x-api-key"] = key
    elif api["auth_style"] == "bearer":
        headers["Authorization"] = f"Bearer {key}"
    if "anthropic-version" in api["extra_fields"]:
        headers["anthropic-version"] = ANTHROPIC_VERSION
    return headers


def _call_target(
    name: str, provider: dict[str, Any], api: ApiType, secrets: ProviderSecrets
) -> tuple[str, dict[str, str]]:
    key = secrets.get_key(name) or ""
    if api["auth_style"] != "none" and not key:
        raise ConflictError(f"provider {name!r} has no key; set one first")
    return provider["base_url"].rstrip("/") + api["endpoint_path"], _auth_headers(key, api)


def _httpx_get(url: str, headers: dict[str, str], timeout: float) -> tuple[int, Any]:
    response = httpx.get(url, headers=headers, timeout=timeout)
    try:
        return response.status_code, response.json()
    except ValueError:
        return response.status_code, None


def _per_mtok(price: Any) -> float | None:
    """OpenRouter-style prices are USD per token as text; the table keeps USD per 1M tokens."""
    try:
        return round(float(price) * 1_000_000, 6)
    except (TypeError, ValueError):
        return None


def _listed_model(item: Any) -> dict[str, Any] | None:
    if not isinstance(item, dict) or not isinstance(item.get("id"), str):
        return None
    pricing = item.get("pricing") if isinstance(item.get("pricing"), dict) else {}
    price_in, price_out = _per_mtok(pricing.get("prompt")), _per_mtok(pricing.get("completion"))
    free = item["id"].endswith(":free") or (price_in == 0 and price_out == 0)
    known = bool(pricing) or item["id"].endswith(":free")
    return {
        "id": item["id"],
        "name": str(item.get("name") or item.get("display_name") or item["id"]),
        "price_in": price_in,
        "price_out": price_out,
        "free": free if known else None,
    }


def _listing_headers(key: str, api: ApiType, base_url: str) -> dict[str, str]:
    """Anthropic headers only for Anthropic's own host: a gateway (OpenRouter, LiteLLM) answers them
    with a paged Anthropic-style list without prices, and its own list is OpenAI-shaped."""
    if api["id"] == "anthropic" and urlsplit(base_url).hostname != ANTHROPIC_HOST:
        return {"Authorization": f"Bearer {key}"} if key else {}
    return _auth_headers(key, api)


def _list_models(get: GetFn, url: str, headers: dict[str, str]) -> tuple[int, list[dict[str, Any]]]:
    """The listed models, following the pages of an Anthropic-style list (``has_more``)."""
    models: list[dict[str, Any]] = []
    after = ""
    for _ in range(MODELS_MAX_PAGES):
        query = f"?limit={MODELS_PAGE_SIZE}" + (f"&after_id={after}" if after else "")
        status, payload = get(url + query, headers, PROVIDER_CALL_TIMEOUT_SECONDS)
        if status != 200:
            return status, []
        models.extend(_listed_models(payload))
        after = str(payload.get("last_id") or "") if isinstance(payload, dict) else ""
        if not (isinstance(payload, dict) and payload.get("has_more") is True and after):
            break
    return 200, models


def _listed_models(payload: Any) -> list[dict[str, Any]]:
    items = payload.get("data") if isinstance(payload, dict) else None
    rows = [_listed_model(item) for item in items] if isinstance(items, list) else []
    return [row for row in rows if row is not None]


def _httpx_post(url: str, headers: dict[str, str], body: dict[str, Any], timeout: float) -> int:
    return httpx.post(url, headers=headers, json=body, timeout=timeout).status_code


def _send(post: PostFn, url: str, headers: dict[str, str], body: dict[str, Any]) -> int | None:
    try:
        return post(url, headers, body, PROVIDER_CALL_TIMEOUT_SECONDS)
    except httpx.HTTPError as exc:
        logger.warning("provider call to %s failed: %s", url, type(exc).__name__)
        return None


def _probe_tool(api_id: str) -> dict[str, Any]:
    schema: dict[str, Any] = {"type": "object", "properties": {}}
    if api_id == "anthropic":
        return {"name": PROBE_TOOL_NAME, "description": "Probe.", "input_schema": schema}
    return {
        "type": "function",
        "function": {"name": PROBE_TOOL_NAME, "description": "Probe.", "parameters": schema},
    }


def _chat_body(
    api_id: str,
    model: str,
    max_tokens: int,
    *,
    system: bool = False,
    tools: bool = False,
    stream: bool = False,
) -> dict[str, Any]:
    messages: list[dict[str, Any]] = [{"role": "user", "content": PROBE_PROMPT}]
    body: dict[str, Any] = {"model": model, "max_tokens": max_tokens, "messages": messages}
    if system:
        if api_id == "anthropic":
            body["system"] = PROBE_SYSTEM
        else:
            messages.insert(0, {"role": "system", "content": PROBE_SYSTEM})
    if tools:
        body["tools"] = [_probe_tool(api_id)]
    if stream:
        body["stream"] = True
    return body


def _probe_result(
    post: PostFn, url: str, headers: dict[str, str], api_id: str, model: str
) -> dict[str, Any]:
    def accepted(**flags: bool) -> bool:
        body = _chat_body(api_id, model, PROBE_MAX_TOKENS, **flags)
        status = _send(post, url, headers, body)
        return status is not None and 200 <= status < 300

    tools = accepted(tools=True)
    return {
        "tools": tools,
        "streaming": accepted(stream=True),
        "system": accepted(system=True),
        "worker_warning": worker_warning({"tools": tools}),
    }


DEFAULT_ANTHROPIC_URL = "https://api.anthropic.com"


def _env_proposal(row: Mapping[str, Any], existing: set[str]) -> dict[str, Any]:
    name = str(row["name"]).lower().replace("_", "-")
    # an env block without ANTHROPIC_BASE_URL talks to the vendor's own endpoint
    fields = {
        "label": name,
        "api_type": "anthropic",
        "base_url": row["base_url"] or DEFAULT_ANTHROPIC_URL,
    }
    row = {"name": name, "has_key": bool(row["has_key"]), "exists": name in existing}
    try:
        _check_fields(fields, PROVIDER_CHECKS, PROVIDER_NON_NULL, PROVIDER_REQUIRED)
        _name(name, _PROVIDER_NAME_RE, "name")
    except ValueError as exc:
        return {**row, "error": str(exc)}
    return {**row, "fields": fields}


def _deps(request: Request) -> tuple[ProviderStore, ProviderSecrets]:
    store = cast(ProviderStore, _store_guard(request))
    secrets: ProviderSecrets = request.app.state.provider_secrets
    return store, secrets


def _post(request: Request) -> PostFn:
    return cast(PostFn, request.app.state.provider_post)


def _build_router() -> APIRouter:
    router = APIRouter(prefix="/api/board/providers", tags=["board providers"])

    @router.get("")
    def list_providers(request: Request) -> dict[str, Any]:
        store, secrets = _deps(request)
        return {"providers": [_with_key_state(v, secrets) for v in store.list_providers()]}

    @router.get("/import-from-env")
    def import_from_env(request: Request) -> dict[str, Any]:
        store, _ = _deps(request)
        existing = {view["name"] for view in store.list_providers()}
        profiles: dict[str, ProviderProfile] = request.app.state.provider_profiles
        rows = [env_provider_row(profile) for profile in profiles.values()]
        source = getattr(request.app.state, "provider_env_source", None)
        if not rows and source is not None:
            rows = source()
        proposals = [_env_proposal(row, existing) for row in rows]
        return {"dry_run": True, "proposals": proposals}

    @router.get("/templates")
    def list_templates(request: Request) -> dict[str, Any]:
        return {"templates": load_templates()}

    @router.get("/{name}")
    def get_provider(request: Request, name: str) -> dict[str, Any]:
        store, secrets = _deps(request)
        return {"provider": _with_key_state(_require(store, name), secrets)}

    @router.post("")
    def create_provider(request: Request, body: dict[str, Any]) -> dict[str, Any]:
        _require_dashboard_header(request)
        store, secrets = _deps(request)
        name = _name(body.get("name"), _PROVIDER_NAME_RE, "name")
        fields = {k: v for k, v in body.items() if k != "name"}
        _check_fields(fields, PROVIDER_CHECKS, PROVIDER_NON_NULL, PROVIDER_REQUIRED)
        store.create_provider(name, fields)
        return {"provider": _with_key_state(_require(store, name), secrets)}

    @router.put("/{name}")
    def update_provider(request: Request, name: str, body: dict[str, Any]) -> dict[str, Any]:
        _require_dashboard_header(request)
        store, secrets = _deps(request)
        current = _require(store, name)
        expected = _expected(body.get("expected_version"))
        dry_run = _flag_value("dry_run", body.get("dry_run", False))
        fields = {k: v for k, v in body.items() if k not in _CONTROL_KEYS}
        if not fields:
            raise ValueError("no fields to update")
        _check_fields(fields, PROVIDER_CHECKS, PROVIDER_NON_NULL)
        _check_expected(current, expected)
        diff = _diff(current, fields)
        if dry_run:
            return {"dry_run": True, "changed": bool(diff), "diff": diff}
        store.update_provider(name, fields, expected)
        return {"provider": _with_key_state(_require(store, name), secrets), "diff": diff}

    @router.delete("/{name}")
    def delete_provider(
        request: Request, name: str, expected_version: str = Query("")
    ) -> dict[str, Any]:
        _require_dashboard_header(request)
        store, secrets = _deps(request)
        _check_expected(_require(store, name), _expected(expected_version))
        users = _provider_users(store, name)
        if users:
            raise ConflictError(f"provider {name!r} is used by a role of: {', '.join(users)}")
        store.delete_provider(name, expected_version)
        secrets.delete_key(name)
        return {"deleted": name}

    @router.put("/{name}/key")
    def put_key(request: Request, name: str, body: dict[str, Any]) -> dict[str, Any]:
        _require_dashboard_header(request)
        store, secrets = _deps(request)
        _require(store, name)
        secrets.set_key(name, _key_value(body))
        return {"key_state": _key_state(name, secrets)}

    @router.delete("/{name}/key")
    def delete_key(request: Request, name: str) -> dict[str, Any]:
        _require_dashboard_header(request)
        store, secrets = _deps(request)
        _require(store, name)
        secrets.delete_key(name)
        return {"key_state": _key_state(name, secrets)}

    @router.post("/{name}/test")
    def test_provider(request: Request, name: str, body: dict[str, Any]) -> dict[str, Any]:
        _require_dashboard_header(request)
        store, secrets = _deps(request)
        provider = _require(store, name)
        row = _model_row(provider, body)
        api = _tested_api_type(provider, row)
        url, headers = _call_target(name, provider, api, secrets)
        body_sent = _chat_body(api["id"], row["model"], TEST_MAX_TOKENS)
        started = time.monotonic()
        status = _send(_post(request), url, headers, body_sent)
        latency_ms = round((time.monotonic() - started) * 1000)
        return {"status": status, "latency_ms": latency_ms}

    @router.post("/{name}/probe")
    def probe_provider(request: Request, name: str, body: dict[str, Any]) -> dict[str, Any]:
        _require_dashboard_header(request)
        store, secrets = _deps(request)
        provider = _require(store, name)
        row = _model_row(provider, body)
        api = _tested_api_type(provider, row)
        url, headers = _call_target(name, provider, api, secrets)
        result = _probe_result(_post(request), url, headers, api["id"], row["model"])
        store.set_model_probe(name, row["model"], result)
        return {"provider": _with_key_state(_require(store, name), secrets), "probe": result}

    @router.get("/{name}/available-models")
    def available_models(request: Request, name: str) -> dict[str, Any]:
        """The models the provider itself lists (GET <base_url>/models). A key is sent when one
        is stored; some providers list without one. Nothing is stored here."""
        store, secrets = _deps(request)
        provider = _require(store, name)
        api = get_api_type(provider["api_type"])
        key = secrets.get_key(name) or ""
        base = provider["base_url"].rstrip("/")
        url = base + MODELS_PATHS.get(api["id"], "/models")
        get = cast(GetFn, request.app.state.provider_get)
        try:
            status, models = _list_models(get, url, _listing_headers(key, api, base))
        except httpx.HTTPError as exc:
            logger.warning("model list of %s failed: %s", name, type(exc).__name__)
            return {"status": None, "models": []}
        return {"status": status, "models": models}

    @router.post("/{name}/models")
    def add_model(request: Request, name: str, body: dict[str, Any]) -> dict[str, Any]:
        _require_dashboard_header(request)
        store, secrets = _deps(request)
        model = _name(body.get("model"), _MODEL_NAME_RE, "model")
        fields = {k: v for k, v in body.items() if k != "model"}
        _check_fields(fields, MODEL_CHECKS, MODEL_NON_NULL)
        store.add_model(name, model, fields)
        return {"provider": _with_key_state(_require(store, name), secrets)}

    @router.put("/{name}/models/{model:path}")
    def update_model(
        request: Request, name: str, model: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        _require_dashboard_header(request)
        store, secrets = _deps(request)
        model_name = _name(model, _MODEL_NAME_RE, "model")
        expected = _expected(body.get("expected_version"))
        fields = {k: v for k, v in body.items() if k != "expected_version"}
        if not fields:
            raise ValueError("no fields to update")
        _check_fields(fields, MODEL_CHECKS, MODEL_NON_NULL)
        store.update_model(name, model_name, fields, expected)
        return {"provider": _with_key_state(_require(store, name), secrets)}

    @router.delete("/{name}/models/{model:path}")
    def remove_model(
        request: Request, name: str, model: str, expected_version: str = Query("")
    ) -> dict[str, Any]:
        _require_dashboard_header(request)
        store, secrets = _deps(request)
        model_name = _name(model, _MODEL_NAME_RE, "model")
        store.remove_model(name, model_name, _expected(expected_version))
        return {"provider": _with_key_state(_require(store, name), secrets)}

    return router


def add_provider_routes(
    app: FastAPI,
    secrets: ProviderSecrets,
    *,
    providers: Mapping[str, ProviderProfile] | None = None,
    post: PostFn = _httpx_post,
    get: GetFn = _httpx_get,
    env_source: Callable[[], list[dict[str, Any]]] | None = None,
) -> None:
    """Attach the provider routes; ``add_board_routes`` must already have set the error handlers."""
    app.state.provider_secrets = secrets
    app.state.provider_profiles = dict(providers or {})
    app.state.provider_post = post
    app.state.provider_get = get
    app.state.provider_env_source = env_source
    app.include_router(_build_router())
