"""Labels for runs, from the AHAWR prompt that started them."""

from __future__ import annotations

import re

TITLE_MAX_CHARS = 160
ROLE_HEAD_CHARS = 600  # the start of a prompt that names the role
_TASK_RE = re.compile(r"^TASK\s+\S+.*$", re.MULTILINE)
_REVIEW_RE = re.compile(r"^CURRENT TASK:\s*\n\s*(\S.*)$", re.MULTILINE)
_MISSION_RE = re.compile(r"^MISSION:\s*(\S.*)$", re.MULTILINE)


def title_of(text: str) -> str:
    """A short label for a run from its AHAWR prompt."""
    if match := _TASK_RE.search(text):
        return match.group(0).strip()[:TITLE_MAX_CHARS]
    if match := _REVIEW_RE.search(text):
        return ("Review: " + match.group(1).strip())[:TITLE_MAX_CHARS]
    if match := _MISSION_RE.search(text):
        return ("Plan: " + match.group(1).strip())[:TITLE_MAX_CHARS]
    first = next((line.strip() for line in text.splitlines() if line.strip()), "")
    return first[:TITLE_MAX_CHARS]


def role_of(text: str) -> str:
    """AHAWR role from the role prompt that opens a Hermes session (agent_prompts)."""
    head = text[:ROLE_HEAD_CHARS].lower()
    if "reviewer" in head:
        return "reviewer"
    if "architect" in head or "planner" in head:
        return "architect"
    if "worker" in head:
        return "worker"
    return "hermes"
