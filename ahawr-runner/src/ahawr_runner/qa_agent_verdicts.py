"""Strict parser for the qa_agent's final JSON answer.

The agent ends its run with one JSON array, one element per contract item
(``{"item": "C1", "verdict": "pass|fail|blocked", "evidence": "...",
"reason": "..."}``). ``parse_agent_verdicts`` takes the last JSON array of the
final text, validates it against the contract's item ids, and returns one
verdict per item plus the gate: an item answered once with a valid verdict and
non-empty evidence (for pass and fail) is kept; a missing, duplicate or unknown
item, an invalid verdict, a pass or fail without evidence, and a malformed or
absent array give a FAIL with a clear reason. The gate is "pass" only when every
item is "pass"; a "fail" or "blocked" verdict fails it.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

# The verdict values the answer schema allows.
VERDICTS: tuple[str, ...] = ("pass", "fail", "blocked")
# A pass and a fail must both carry evidence; a blocked verdict keeps its reason.
EVIDENCE_REQUIRED: tuple[str, ...] = ("pass", "fail")


def parse_agent_verdicts(final_text: str, item_ids: Sequence[str]) -> dict[str, Any]:
    """Return one verdict per contract item, in the contract's order, plus the gate.

    When the answer is complete and valid, ``verdicts`` holds one entry per item and
    ``reason`` is empty. Otherwise ``verdicts`` is empty and ``reason`` names the
    defect. The gate is "pass" only when every item's verdict is "pass": a "fail"
    or "blocked" verdict, and any malformed answer, fail it.
    """
    expected = [item_id for item_id in item_ids]
    if not expected:
        return _failure("the contract has no items")
    array, problem = _last_json_array(final_text)
    if array is None:
        return _failure(problem or "no JSON array in the final text")
    answer_by_id: dict[str, dict[str, str]] = {}
    for index, entry in enumerate(array):
        entry_problem = _entry_fields(entry, index, answer_by_id)
        if entry_problem is not None:
            return _failure(entry_problem)
    missing = [item_id for item_id in expected if item_id not in answer_by_id]
    if missing:
        return _failure(f"the answer is missing items {', '.join(missing)}")
    unknown = [item_id for item_id in answer_by_id if item_id not in expected]
    if unknown:
        return _failure(f"the answer names unknown items {', '.join(unknown)}")
    verdicts = [
        {
            "item": item_id,
            "verdict": answer_by_id[item_id]["verdict"],
            "evidence": answer_by_id[item_id]["evidence"],
            "reason": answer_by_id[item_id]["reason"],
        }
        for item_id in expected
    ]
    gate = "pass" if all(entry["verdict"] == "pass" for entry in verdicts) else "fail"
    return {"verdicts": verdicts, "gate": gate, "reason": ""}


def _failure(reason: str) -> dict[str, Any]:
    """The failed result: no verdicts, a failed gate and the clear reason."""
    return {"verdicts": [], "gate": "fail", "reason": reason}


def _entry_fields(
    entry: object, index: int, answer_by_id: dict[str, dict[str, str]]
) -> str | None:
    """Store the entry's fields under its item id; a failure reason when the entry
    has no item id, repeats one, has an invalid verdict, or lacks evidence for a
    pass or fail."""
    if not isinstance(entry, dict):
        return f"answer entry {index} is not an object"
    item_id = entry.get("item")
    if not isinstance(item_id, str) or not item_id.strip():
        return f"answer entry {index} has no item id"
    item_id = item_id.strip()
    if item_id in answer_by_id:
        return f"item {item_id} is answered twice"
    verdict = entry.get("verdict")
    if not isinstance(verdict, str) or verdict not in VERDICTS:
        return f"item {item_id} has an invalid verdict {verdict!r}"
    evidence = entry.get("evidence")
    evidence = evidence.strip() if isinstance(evidence, str) else ""
    if verdict in EVIDENCE_REQUIRED and not evidence:
        return f"item {item_id} has no evidence for its {verdict} verdict"
    reason = entry.get("reason")
    answer_by_id[item_id] = {
        "verdict": verdict,
        "evidence": evidence,
        "reason": reason if isinstance(reason, str) else "",
    }
    return None


def _last_json_array(text: str) -> tuple[list[Any] | None, str | None]:
    """The last JSON array of the text, with a reason when the text has none.

    The last balanced top-level bracket region is the answer: nested arrays belong
    to the region that encloses them, so a region inside the answer is not a
    separate answer. The region is parsed as JSON; a region that is not a JSON
    array (prose brackets) or that has no balanced close fails, never falls back
    to an earlier region, and is reported with a clear reason.
    """
    depth = 0
    in_string = False
    escaped = False
    start = -1
    end = -1
    for index, char in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
        elif char == '"':
            in_string = True
        elif char == "[":
            if depth == 0:
                start = index
            depth += 1
        elif char == "]":
            depth -= 1
            if depth == 0:
                end = index
    if start < 0 or end < 0:
        return None, "no JSON array in the final text"
    return _parse_region(text, start, end)


def _parse_region(text: str, start: int, end: int) -> tuple[list[Any] | None, str | None]:
    """The JSON array of one balanced bracket region; a reason when it is not JSON."""
    try:
        value = json.loads(text[start : end + 1])
    except ValueError as error:
        return None, f"the final JSON array is not valid JSON: {error}"
    if not isinstance(value, list):
        return None, "the final JSON array is not an array"
    return value, None
