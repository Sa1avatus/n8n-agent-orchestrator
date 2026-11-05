"""Failure classes and text helpers shared by the engine event mappers (hermes_events.py)."""

from __future__ import annotations

import json
from typing import Any

from .events import _clip

TRANSIENT = "transient"
PERMANENT = "permanent"
CONTEXT_OVERFLOW = "context_overflow"
OTHER = "other"


def _text_of(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return _clip(value)
    return _clip(json.dumps(value, ensure_ascii=False))


def _count(value: Any) -> int:
    return value if isinstance(value, int) else 0
