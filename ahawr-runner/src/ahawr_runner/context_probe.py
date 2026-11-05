"""The local model's real context window, read from llama-server before each run.

llama-server in router mode lists every model with the arguments it is (or will be) started
with, loaded or not: ``GET /v1/models`` → ``data[].status.args`` has ``--ctx-size N``. The
runner reads it for the run's model and gives Claude Code that window and the matching
autocompact trigger, so a server restarted with a larger or smaller ``ctx-size`` (another
quant, another build) needs no runner change. Any failure returns ``None`` and the provider's
static settings apply.
"""

from __future__ import annotations

import json
import logging
import urllib.request
from typing import Any

from .config import (
    AUTO_COMPACT_PRECOMPUTE_BUFFER,
    AUTO_COMPACT_TOOL_RESERVE,
    AUTO_COMPACT_WINDOW,
    DEFAULT_MAX_OUTPUT_TOKENS,
    ProviderProfile,
)

log = logging.getLogger(__name__)

PROBE_TIMEOUT_SECONDS = 5.0
# a window whose trigger would leave less than this much room for the conversation is
# not worth running Claude Code in: keep the static settings and log it
MIN_TRIGGER_TOKENS = 8_000


def _arg(args: list[str], *names: str) -> str | None:
    for i, a in enumerate(args):
        if a in names and i + 1 < len(args):
            return args[i + 1]
        for n in names:
            if n.startswith("--") and a.startswith(n + "="):
                return a.split("=", 1)[1]
    return None


def ctx_from_models(payload: dict[str, Any], model: str) -> int | None:
    """Per-slot context of ``model`` from a llama-server ``/v1/models`` payload."""
    for item in payload.get("data") or []:
        if item.get("id") != model and model not in (item.get("aliases") or []):
            continue
        args = [str(a) for a in ((item.get("status") or {}).get("args") or [])]
        raw = _arg(args, "--ctx-size", "-c")
        if raw is None or not raw.strip().lstrip("-").isdigit():
            return None
        ctx = int(raw)
        if ctx <= 0:  # 0 = the model's training context, not known from the arguments
            return None
        parallel = _arg(args, "--parallel", "-np")
        n_par = int(parallel) if parallel and parallel.isdigit() and int(parallel) > 0 else 1
        # without a unified KV cache each of the parallel slots gets an equal share
        if n_par > 1 and "--kv-unified" not in args and "-kvu" not in args:
            ctx //= n_par
        return ctx
    return None


def probe_context(
    url: str, key: str, model: str, timeout: float = PROBE_TIMEOUT_SECONDS
) -> int | None:
    req = urllib.request.Request(url.rstrip("/") + "/v1/models")
    if key:
        req.add_header("Authorization", "Bearer " + key)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            payload = json.loads(r.read())
    except Exception as exc:  # the server may be restarting: fall back to static settings
        log.warning("context probe %s failed: %s", url, exc)
        return None
    ctx = ctx_from_models(payload, model)
    if ctx is None:
        log.warning("context probe %s: no --ctx-size for model %r", url, model)
    return ctx


def max_trigger(window: int, provider: ProviderProfile) -> int:
    """The latest safe trigger: room for one reply and one large tool output stays free
    (Claude Code's own trigger for ``CLAUDE_CODE_AUTO_COMPACT_WINDOW=window``)."""
    max_output = provider.max_output_tokens or DEFAULT_MAX_OUTPUT_TOKENS
    return window - min(max_output, AUTO_COMPACT_TOOL_RESERVE) - AUTO_COMPACT_PRECOMPUTE_BUFFER


def compact_pct(provider: ProviderProfile) -> float:
    """The trigger as a percentage of the window: ``COMPACT_PCT``, else the block's
    ``CLAUDE_AUTOCOMPACT_PCT_OVERRIDE``, else the static trigger's share of the static
    65536 window (44344 → 67.66%), so a 64K server keeps today's behaviour."""
    if provider.compact_pct is not None:
        return provider.compact_pct
    raw = provider.env.get("CLAUDE_AUTOCOMPACT_PCT_OVERRIDE", "").strip()
    if raw:
        return float(raw)
    static = provider.autocompact_threshold or max_trigger(AUTO_COMPACT_WINDOW, provider)
    return static * 100 / AUTO_COMPACT_WINDOW


def autocompact_trigger(window: int, provider: ProviderProfile) -> int:
    """The trigger for a ``window``-token context: the percentage, capped by ``max_trigger``."""
    return min(int(window * compact_pct(provider) / 100), max_trigger(window, provider))


def resume_compact_threshold(window: int, provider: ProviderProfile, base: int) -> int:
    """The runner's pre-resume /compact threshold for a ``window``-token context.

    ``COMPACT_MIN_PCT`` of the window, else ``base`` (COMPACT_MIN_TOKENS) scaled from the static
    65536 window, so a larger window does not compact a session that still has room. Never
    above the autocompact trigger: a resumed session must start below it."""
    pct = provider.compact_min_pct
    scaled = int(window * pct / 100) if pct is not None else base * window // AUTO_COMPACT_WINDOW
    return min(scaled, autocompact_trigger(window, provider))


def window_env(window: int, provider: ProviderProfile) -> dict[str, str] | None:
    """Claude Code settings for a ``window``-token context, or None if it is too small."""
    trigger = autocompact_trigger(window, provider)
    if trigger < MIN_TRIGGER_TOKENS:
        log.warning("context window %d too small for Claude Code; keeping static settings", window)
        return None
    return {
        "CLAUDE_CODE_MAX_CONTEXT_TOKENS": str(window),
        "CLAUDE_CODE_AUTO_COMPACT_WINDOW": str(window),
        # Claude Code takes the trigger as a percentage of the window; two decimals keep it
        # within a token or so of ``trigger``
        "CLAUDE_AUTOCOMPACT_PCT_OVERRIDE": f"{trigger * 100 / window:.2f}",
    }
