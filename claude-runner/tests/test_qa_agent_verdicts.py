"""Tests for the strict parser of the qa_agent's final JSON answer."""

from __future__ import annotations

import json
from typing import Any

from claude_runner.qa_agent_verdicts import parse_agent_verdicts

ITEM_IDS = ("C1", "C2", "C3")

PASS_ANSWER = (
    "The screenshots match the reference images.\n"
    "[\n"
    '  {"item": "C1", "verdict": "pass", "evidence": "run/Providers page.png",\n'
    '   "reason": "cards show the connection names"},\n'
    '  {"item": "C2", "verdict": "pass", "evidence": "run/Providers page.png",\n'
    '   "reason": "one Delete button per card"},\n'
    '  {"item": "C3", "verdict": "pass", "evidence": "GET /api/board/providers answered []",\n'
    '   "reason": "no rows while there is no provider"}'
    "]\n"
)


def _gate_fail(result: dict[str, Any], expected_reason: str) -> None:
    assert result["gate"] == "fail"
    assert result["verdicts"] == []
    assert result["reason"] == expected_reason


def test_every_item_passed_with_evidence_gives_a_pass_gate() -> None:
    result = parse_agent_verdicts(PASS_ANSWER, ITEM_IDS)
    assert result["gate"] == "pass"
    assert result["reason"] == ""
    assert result["verdicts"] == [
        {
            "item": "C1",
            "verdict": "pass",
            "evidence": "run/Providers page.png",
            "reason": "cards show the connection names",
        },
        {
            "item": "C2",
            "verdict": "pass",
            "evidence": "run/Providers page.png",
            "reason": "one Delete button per card",
        },
        {
            "item": "C3",
            "verdict": "pass",
            "evidence": "GET /api/board/providers answered []",
            "reason": "no rows while there is no provider",
        },
    ]


def test_the_verdicts_follow_the_contract_order_when_the_answer_reorders_them() -> None:
    answer = (
        '[{"item": "C3", "verdict": "pass", "evidence": "a", "reason": "r"},'
        '{"item": "C1", "verdict": "pass", "evidence": "b", "reason": "r"},'
        '{"item": "C2", "verdict": "pass", "evidence": "c", "reason": "r"}]'
    )
    result = parse_agent_verdicts(answer, ITEM_IDS)
    assert [entry["item"] for entry in result["verdicts"]] == ["C1", "C2", "C3"]


def test_an_earlier_malformed_array_is_not_the_answer() -> None:
    text = "first [1, 2] then " + PASS_ANSWER
    result = parse_agent_verdicts(text, ITEM_IDS)
    assert result["gate"] == "pass"
    assert len(result["verdicts"]) == 3


def test_a_later_malformed_array_is_the_answer_and_fails() -> None:
    text = PASS_ANSWER + " stray [not] brackets"
    _gate_fail(
        parse_agent_verdicts(text, ITEM_IDS),
        "the final JSON array is not valid JSON: "
        "Expecting value: line 1 column 2 (char 1)",
    )


def test_brackets_and_quotes_inside_the_answer_strings_do_not_break_it() -> None:
    answer = json.dumps(
        [
            {"item": "C1", "verdict": "pass", "evidence": r"run\Providers page.png", "reason": "r"},
            {"item": "C2", "verdict": "pass", "evidence": "e2", "reason": '"quoted" and ] inside'},
            {"item": "C3", "verdict": "pass", "evidence": "e3", "reason": "r"},
        ]
    )
    result = parse_agent_verdicts(answer, ITEM_IDS)
    assert result["gate"] == "pass"
    assert result["reason"] == ""
    assert result["verdicts"][0]["evidence"] == r"run\Providers page.png"
    assert result["verdicts"][1]["reason"] == '"quoted" and ] inside'


def test_a_fail_verdict_fails_the_gate_and_keeps_its_evidence() -> None:
    answer = PASS_ANSWER.replace("cards show the connection names", "the key is masked")
    answer = answer.replace('"C1", "verdict": "pass"', '"C1", "verdict": "fail"')
    result = parse_agent_verdicts(answer, ITEM_IDS)
    assert result["gate"] == "fail"
    assert result["verdicts"][0]["verdict"] == "fail"
    assert result["verdicts"][0]["evidence"] == "run/Providers page.png"
    assert result["verdicts"][0]["reason"] == "the key is masked"


def test_a_blocked_verdict_keeps_its_reason_and_fails_the_gate() -> None:
    answer = (
        '[{"item": "C1", "verdict": "blocked", "evidence": "", "reason": "screenshot missing"},'
        '{"item": "C2", "verdict": "pass", "evidence": "run/Guards detail.png", "reason": "ok"},'
        '{"item": "C3", "verdict": "pass", "evidence": "a line", "reason": "ok"}]'
    )
    result = parse_agent_verdicts(answer, ITEM_IDS)
    assert result["gate"] == "fail"
    assert result["verdicts"][0] == {
        "item": "C1",
        "verdict": "blocked",
        "evidence": "",
        "reason": "screenshot missing",
    }


def test_malformed_json_fails() -> None:
    _gate_fail(
        parse_agent_verdicts("[{not json}]", ITEM_IDS),
        "the final JSON array is not valid JSON: "
        "Expecting property name enclosed in double quotes: "
        "line 1 column 3 (char 2)",
    )


def test_an_unbalanced_array_fails() -> None:
    _gate_fail(
        parse_agent_verdicts('[{"item": "C1", "verdict": "pass"', ITEM_IDS),
        "no JSON array in the final text",
    )


def test_text_without_any_json_array_fails() -> None:
    _gate_fail(parse_agent_verdicts("the run finished, nothing to report", ITEM_IDS),
              "no JSON array in the final text")


def test_an_empty_text_fails() -> None:
    _gate_fail(
        parse_agent_verdicts("", ITEM_IDS), "no JSON array in the final text"
    )


def test_a_duplicate_item_fails() -> None:
    answer = (
        '[{"item": "C1", "verdict": "pass", "evidence": "a", "reason": "r"},'
        '{"item": "C1", "verdict": "pass", "evidence": "b", "reason": "r"},'
        '{"item": "C2", "verdict": "pass", "evidence": "c", "reason": "r"}]'
    )
    _gate_fail(parse_agent_verdicts(answer, ITEM_IDS), "item C1 is answered twice")


def test_an_unknown_item_fails() -> None:
    answer = (
        '[{"item": "C1", "verdict": "pass", "evidence": "a", "reason": "r"},'
        '{"item": "C2", "verdict": "pass", "evidence": "b", "reason": "r"},'
        '{"item": "C3", "verdict": "pass", "evidence": "c", "reason": "r"},'
        '{"item": "C9", "verdict": "pass", "evidence": "d", "reason": "r"}]'
    )
    _gate_fail(parse_agent_verdicts(answer, ITEM_IDS), "the answer names unknown items C9")


def test_a_missing_item_fails() -> None:
    answer = (
        '[{"item": "C1", "verdict": "pass", "evidence": "a", "reason": "r"},'
        '{"item": "C2", "verdict": "pass", "evidence": "b", "reason": "r"}]'
    )
    _gate_fail(parse_agent_verdicts(answer, ITEM_IDS), "the answer is missing items C3")


def test_pass_without_evidence_fails() -> None:
    answer = (
        '[{"item": "C1", "verdict": "pass", "evidence": "", "reason": "r"},'
        '{"item": "C2", "verdict": "pass", "evidence": "b", "reason": "r"},'
        '{"item": "C3", "verdict": "pass", "evidence": "c", "reason": "r"}]'
    )
    _gate_fail(
        parse_agent_verdicts(answer, ITEM_IDS),
        "item C1 has no evidence for its pass verdict",
    )


def test_whitespace_only_evidence_fails() -> None:
    answer = (
        '[{"item": "C1", "verdict": "fail", "evidence": "   ", "reason": "r"},'
        '{"item": "C2", "verdict": "pass", "evidence": "b", "reason": "r"},'
        '{"item": "C3", "verdict": "pass", "evidence": "c", "reason": "r"}]'
    )
    _gate_fail(
        parse_agent_verdicts(answer, ITEM_IDS),
        "item C1 has no evidence for its fail verdict",
    )


def test_an_invalid_verdict_fails() -> None:
    answer = (
        '[{"item": "C1", "verdict": "ok", "evidence": "a", "reason": "r"},'
        '{"item": "C2", "verdict": "pass", "evidence": "b", "reason": "r"},'
        '{"item": "C3", "verdict": "pass", "evidence": "c", "reason": "r"}]'
    )
    _gate_fail(parse_agent_verdicts(answer, ITEM_IDS), "item C1 has an invalid verdict 'ok'")


def test_an_answer_entry_without_an_item_id_fails() -> None:
    answer = '[{"verdict": "pass", "evidence": "a", "reason": "r"}]'
    _gate_fail(parse_agent_verdicts(answer, ITEM_IDS), "answer entry 0 has no item id")


def test_an_answer_entry_that_is_not_an_object_fails() -> None:
    answer = '["C1", {"item": "C2", "verdict": "pass", "evidence": "b", "reason": "r"}]'
    _gate_fail(parse_agent_verdicts(answer, ITEM_IDS), "answer entry 0 is not an object")


def test_a_non_string_evidence_fails() -> None:
    answer = '[{"item": "C1", "verdict": "pass", "evidence": null, "reason": "r"}]'
    _gate_fail(
        parse_agent_verdicts(answer, ITEM_IDS),
        "item C1 has no evidence for its pass verdict",
    )


def test_no_contract_items_fails() -> None:
    _gate_fail(parse_agent_verdicts("[]", ()), "the contract has no items")


def test_a_nested_array_inside_the_answer_is_not_the_answer() -> None:
    # The nested array is a complete valid answer on its own; the parser must take
    # the outer array instead, whose entry has no item id.
    nested = (
        '[{"item": "C1", "verdict": "pass", "evidence": "a", "reason": "r"},'
        '{"item": "C2", "verdict": "pass", "evidence": "b", "reason": "r"},'
        '{"item": "C3", "verdict": "pass", "evidence": "c", "reason": "r"}]'
    )
    answer = "[{\"nested\": " + nested + "}]"
    result = parse_agent_verdicts(answer, ITEM_IDS)
    _gate_fail(result, "answer entry 0 has no item id")
