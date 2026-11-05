"""Scrubbing and truncation for board ops results.

Command results are truncated and scrubbed of anything that looks like a
credential before they land in ``result_json`` or are shown in the UI
(see ``docs/board-ops.md``, "Shared rules"): ``scrub`` replaces key-like
substrings with ``***``, ``truncate`` caps characters and lines, and
``sanitize_result`` applies both recursively to dicts and lists.
``presence`` reports a secret field as 'задан' or 'не задан' without ever
returning the value itself.
"""

from __future__ import annotations

import json
import re
from typing import Any

__all__ = ["MAX_CHARS", "MAX_LINES", "REDACTED", "scrub", "truncate", "sanitize_result", "presence"]

REDACTED = "***"

# Maximum size for a sanitized result: characters and lines.
MAX_CHARS = 20000
MAX_LINES = 500

# The marker a truncated value ends with.
_TRUNCATED_MARKER = "…(truncated)"

# Telegram bot token: <digits>:<token> (bot id : secret part).
_TG_TOKEN = re.compile(r"\b\d{6,12}:[A-Za-z0-9_-]{30,}\b")

# sk-/key-style API keys (OpenAI, Anthropic, LiteLLM style).
_API_KEY = re.compile(r"\bsk-[A-Za-z0-9]{16,}\b")

# Bearer tokens: 'Bearer <token>' with a token of at least 16 chars.
_BEARER_PREFIX = re.compile(r"\bBearer\s+[A-Za-z0-9._-]{16,}\b", re.IGNORECASE)

# DSNs/URLs with credentials: scheme://user:pass@host.
_DSN = re.compile(r"\b[a-zA-Z][a-zA-Z0-9+.\-]*://[^/@\s]+:[^@\s]+@[^\s]+", re.IGNORECASE)

# KEY= / TOKEN= / PASSWORD= / SECRET= / CHAT_ID= assignments (the label
# may carry a prefix, e.g. TELEGRAM_BOT_TOKEN, DB_PASSWORD).
_ASSIGNMENT = re.compile(
    r"(?<![A-Za-z0-9])([A-Za-z0-9_]*(?:KEY|TOKEN|PASSWORD|SECRET|CHAT_ID))\s*=\s*(?:\"[^\"]*\"|'[^']*'|[^\s,;&]+)",
    re.IGNORECASE,
)

# Long hex or base64 blobs.
_HEX = re.compile(r"\b[0-9a-fA-F]{32,}\b")
_BASE64 = re.compile(r"\b[A-Za-z0-9+=]{30,}\b")


def scrub(text: str) -> str:
    """Replace key-like substrings with ``***``.

    Patterns are applied in a fixed order (DSNs, bearer tokens, sk-/key
    API keys, Telegram bot tokens, KEY=/TOKEN=/PASSWORD=/SECRET/CHAT_ID=
    assignments, then long hex/base64 blobs); an assignment keeps its label
    (e.g. ``TELEGRAM_BOT_TOKEN``) and redacts only the value.  Normal log
    text that contains no key-like pattern is returned unchanged.
    """
    if not text:
        return text
    result = text
    # DSNs / URLs with credentials (scheme://user:pass@host).
    result = _DSN.sub(REDACTED, result)
    # Bearer tokens (long token right after the 'Bearer' prefix).
    result = _BEARER_PREFIX.sub(REDACTED, result)
    result = _API_KEY.sub(REDACTED, result)
    result = _TG_TOKEN.sub(REDACTED, result)
    # Key-style assignments (KEY=..., TELEGRAM_BOT_TOKEN=..., etc.):
    # keep the label, redact the value.
    result = _ASSIGNMENT.sub(lambda m: m.group(1) + "=" + REDACTED, result)
    result = _HEX.sub(REDACTED, result)
    result = _BASE64.sub(REDACTED, result)
    return result


def truncate(text: str, max_chars: int = MAX_CHARS, max_lines: int = MAX_LINES) -> str:
    """Cap a string to ``max_chars`` characters and ``max_lines`` lines.

    When either cap is exceeded the text is cut at the first cap and the
    ``…(truncated)`` marker is appended (the marker itself counts toward
    ``max_chars``).
    """
    if not text:
        return text

    # Line cap first (a line longer than max_chars is still a single line).
    lines = text.split("\n")
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        text = "\n".join(lines)

    if len(text) > max_chars:
        marker = _TRUNCATED_MARKER
        if max_chars < len(marker):
            # max_chars too small to fit the marker: hard-cut, no marker.
            return text[:max_chars]
        cut = text[: max_chars - len(marker)]
        return cut + marker
    return text


def sanitize_result(obj: Any, max_chars: int = MAX_CHARS, max_lines: int = MAX_LINES) -> Any:
    """Apply scrubbing and truncation recursively to dicts and lists.

    Every string in the structure is scrubbed; the total serialized size of
    the result is capped by ``max_chars`` (characters) and ``max_lines``
    (lines).  Non-string scalars are kept as-is; dicts and lists are recursed
    into.  The returned object is a new structure (the input is not mutated).
    """
    if obj is None or isinstance(obj, (bool, int, float)):
        return obj

    if isinstance(obj, str):
        return truncate(scrub(obj), max_chars, max_lines)

    if isinstance(obj, dict):
        scrubbed = {key: sanitize_result(value, max_chars, max_lines) for key, value in obj.items()}
        return _cap_total(scrubbed, max_chars, max_lines)

    if isinstance(obj, list):
        scrubbed = [sanitize_result(item, max_chars, max_lines) for item in obj]
        return _cap_total(scrubbed, max_chars, max_lines)

    # Any other value: stringify and scrub.
    return truncate(scrub(str(obj)), max_chars, max_lines)


def _total_lines(text: str) -> int:
    return text.count("\n") + 1


def _cap_total(obj: Any, max_chars: int, max_lines: int) -> Any:
    """Cap the serialized total of a scrubbed dict/list.

    The whole structure is serialized to JSON (so the cap applies to the
    total, not per element).  If the serialized form exceeds ``max_chars``
    or ``max_lines``, the structure is collapsed to a single string that
    carries the caps and the ``…(truncated)`` marker.
    """
    text = json.dumps(obj, ensure_ascii=False, default=str)
    if len(text) > max_chars or _total_lines(text) > max_lines:
        return truncate(text, max_chars, max_lines)
    return obj


def presence(value: Any) -> str:
    """Report a secret field as 'задан' or 'не задан'.

    The value itself is never returned — only whether it is present."""
    if value is None or value == "":
        return "не задан"
    return "задан"
