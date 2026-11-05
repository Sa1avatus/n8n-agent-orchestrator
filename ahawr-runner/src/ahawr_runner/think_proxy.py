"""Think proxy: the thinking switch for an engine that ignores the client's thinking settings.

Claude Code sends no ``thinking`` field for a model name it does not know, so an
Anthropic-compatible engine thinks at its own default (Strata: high) whatever
MAX_THINKING_TOKENS says. With ``CLAUDE_RUNNER_THINK_PROXY`` on, the runner asks a provider's
endpoint what it is (``GET /health``, field ``service``): when it is one of ``ENGINE_SERVICES`` the
run sends its requests here instead. The proxy adds ``"thinking": {"type": "disabled"}`` while the
thinking flag file (think_switch.py) says ``off`` and forwards the request, with the client's own
credentials, to the provider's endpoint. The policy, the pulses and the loop guard of
think_switch.py decide the flag; only the requests change. The provider's name plays no part: any
provider that runs such an engine is covered.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.request
from collections.abc import AsyncIterator, Callable
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask

from . import call_net
from .config import ProviderProfile

log = logging.getLogger("ahawr_runner.think_proxy")

PROXY_PREFIX = "/think-proxy"
# values of the ``service`` field of GET /health for engines that ignore the client's thinking
ENGINE_SERVICES = frozenset({"strata"})
PROBE_TIMEOUT_SECONDS = 3.0
PROBE_CACHE_SECONDS = 60.0
LOOPBACK_HOSTS = ("127.0.0.1", "::1")
UPSTREAM_TIMEOUT = httpx.Timeout(connect=10.0, read=1800.0, write=60.0, pool=10.0)
# headers that belong to one hop: not copied to the upstream request or back to the client
HOP_HEADERS = frozenset({"host", "content-length", "connection", "transfer-encoding", "keep-alive"})
DISABLED_THINKING: dict[str, str] = {"type": "disabled"}
SAMPLING_KEYS = ("temperature", "top_p", "top_k", "min_p")


_probe_cache: dict[str, tuple[float, bool]] = {}


def ignores_client_thinking(base_url: str, now: float | None = None) -> bool:
    """Whether the endpoint reports one of ``ENGINE_SERVICES``; the answer is cached for a minute
    per URL. An endpoint that does not answer (a cloud API, a restarting server) counts as no."""
    moment = time.monotonic() if now is None else now
    cached = _probe_cache.get(base_url)
    if cached is not None and moment - cached[0] < PROBE_CACHE_SECONDS:
        return cached[1]
    found = False
    try:
        request = urllib.request.Request(base_url.rstrip("/") + "/health")
        with urllib.request.urlopen(request, timeout=PROBE_TIMEOUT_SECONDS) as reply:  # noqa: S310
            payload: Any = json.loads(reply.read())
        found = isinstance(payload, dict) and payload.get("service") in ENGINE_SERVICES
    except (OSError, ValueError) as exc:
        log.info("think proxy probe %s: %s", base_url, exc)
    _probe_cache[base_url] = (moment, found)
    return found


def child_base_url(proxy_url: str, provider_name: str) -> str:
    return f"{proxy_url.rstrip('/')}{PROXY_PREFIX}/{provider_name}"


def apply_think_flag(body: bytes, flag: str) -> bytes:
    """The request body with thinking disabled when the flag says ``off``; any other flag, or a
    body that is not a JSON object, comes back unchanged."""
    if flag != "off":
        return body
    try:
        payload: Any = json.loads(body)
    except ValueError:
        return body
    if not isinstance(payload, dict):
        return body
    payload["thinking"] = dict(DISABLED_THINKING)
    return json.dumps(payload).encode("utf-8")


def apply_sampling(body: bytes, params: dict[str, float]) -> bytes:
    """The request body with the sampling keys of ``params`` set (SAMPLING_KEYS only); a body that
    is not a JSON object, or no params, come back unchanged."""
    keys = {k: v for k, v in params.items() if k in SAMPLING_KEYS}
    if not keys:
        return body
    try:
        payload: Any = json.loads(body)
    except ValueError:
        return body
    if not isinstance(payload, dict):
        return body
    payload.update(keys)
    return json.dumps(payload).encode("utf-8")


def reply_net(body: bytes, feedback: bool) -> call_net.SseNet | None:
    """The net for the streamed reply to this request, or None when the request does not stream or
    declares no tool."""
    try:
        payload: Any = json.loads(body)
    except ValueError:
        return None
    if not isinstance(payload, dict) or payload.get("stream") is not True:
        return None
    schemas = call_net.schemas_of(payload)
    if not schemas:
        return None
    streak = call_net.feedback_streak(payload)
    return call_net.SseNet(schemas, feedback and streak < call_net.MAX_FEEDBACK_IN_ROW)


async def _through_net(upstream: httpx.Response, net: call_net.SseNet) -> AsyncIterator[bytes]:
    async for chunk in upstream.aiter_bytes():
        for piece in net.feed(chunk):
            yield piece
    for piece in net.finish():
        yield piece
    if net.rewrote:
        log.info(
            "think proxy: %d call(s) written as text were returned as tool_use", net.rewritten_calls
        )


def build_router(
    lookup: Callable[[str], ProviderProfile | None],
    allowed: Callable[[], tuple[str, ...]],
    flag: Callable[[], str | None],
    repair: bool = False,
    feedback: bool = False,
    sampling: Callable[[str], dict[str, float]] | None = None,
) -> APIRouter:
    """The proxy route: ``lookup`` gives a provider's profile, ``allowed`` the provider names that
    may use the proxy, ``flag`` the thinking mode now ('on' / 'off').  ``repair`` turns on the call
    net (call_net.py) for streamed replies, ``feedback`` lets it answer an unreadable call with a
    request to send it again, ``sampling`` gives the sampling keys to set for a provider."""
    router = APIRouter()

    @router.api_route(PROXY_PREFIX + "/{provider}/{path:path}", methods=["GET", "POST"])
    async def forward(provider: str, path: str, request: Request) -> StreamingResponse:
        if request.client is None or request.client.host not in LOOPBACK_HOSTS:
            raise HTTPException(status_code=403, detail="the think proxy serves the runner only")
        profile = lookup(provider) if provider in allowed() else None
        base_url = (profile.env.get("ANTHROPIC_BASE_URL", "") if profile else "").rstrip("/")
        if not base_url:
            raise HTTPException(status_code=404, detail=f"no think proxy for provider {provider!r}")
        body = await request.body()
        if path.rstrip("/").endswith("messages"):
            body = apply_think_flag(body, flag() or "")
            if sampling is not None:
                body = apply_sampling(body, sampling(provider))
        headers = {k: v for k, v in request.headers.items() if k.lower() not in HOP_HEADERS}
        client = httpx.AsyncClient(timeout=UPSTREAM_TIMEOUT)
        upstream_request = client.build_request(
            request.method,
            f"{base_url}/{path}",
            params=request.query_params,
            headers=headers,
            content=body,
        )
        try:
            upstream = await client.send(upstream_request, stream=True)
        except httpx.HTTPError as exc:
            await client.aclose()
            log.warning("think proxy: %s unreachable: %s", provider, exc)
            raise HTTPException(status_code=502, detail="the endpoint is unreachable") from exc

        async def close() -> None:
            await upstream.aclose()
            await client.aclose()

        net = (
            reply_net(body, feedback) if repair and path.rstrip("/").endswith("messages") else None
        )
        coded = "content-encoding" in upstream.headers
        netted = (
            net is not None
            and not coded
            and "event-stream" in upstream.headers.get("content-type", "")
        )
        return StreamingResponse(
            _through_net(upstream, net) if netted and net is not None else upstream.aiter_raw(),
            status_code=upstream.status_code,
            headers={k: v for k, v in upstream.headers.items() if k.lower() not in HOP_HEADERS},
            background=BackgroundTask(close),
        )

    return router
