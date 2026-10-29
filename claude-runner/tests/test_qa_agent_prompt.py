"""Tests for the deterministic qa_agent prompt builder: required content, the
named size limit, and the missing-screen marker."""

from __future__ import annotations

from claude_runner.qa_agent_prompt import (
    PROMPT_CUT_MARKER,
    PROMPT_MAX_CHARS,
    build_qa_agent_prompt,
)

CONTRACT_TEXT = """\
# Board: providers

## Providers page
- Open: route /providers, viewport 1280x800
- Layout: cards in a row
- Reference image: providers-reference.png
- C1: every provider card shows its connection name
- C2: the header has exactly one Delete provider button per card
- C3: the list shows no rows while there is no provider in the database
"""

SCHEMA = (
    "[{\"item\": \"C1\", \"verdict\": \"pass|fail|blocked\", "
    "\"evidence\": \"screenshot file name or API line\", \"reason\": \"one sentence\"}]"
)

MANIFEST = [
    {
        "screen": "Providers page",
        "png": "run/Providers page.png",
        "dom": "run/Providers page.dom.txt",
        "error": "",
    },
    {
        "screen": "Guards detail",
        "png": "run/Guards detail.png",
        "dom": "run/Guards detail.dom.txt",
        "error": "route /guards/1 failed to load",
    },
]

REFERENCE_PATHS = [
    "contracts/ahawr-providers-ui/providers-reference.png",
    "contracts/ahawr-guards-detail/guards-reference.png",
]


def test_the_prompt_carries_every_item_id_screenshot_reference_and_schema() -> None:
    prompt = build_qa_agent_prompt(CONTRACT_TEXT, MANIFEST, REFERENCE_PATHS, SCHEMA)
    for item_id in ("C1", "C2", "C3"):
        assert item_id in prompt
    for entry in MANIFEST:
        assert entry["png"] in prompt
        assert entry["dom"] in prompt
    for path in REFERENCE_PATHS:
        assert path in prompt
    assert SCHEMA in prompt


def test_a_failed_screen_is_marked_as_missing() -> None:
    prompt = build_qa_agent_prompt(CONTRACT_TEXT, MANIFEST, REFERENCE_PATHS, SCHEMA)
    assert "Guards detail" in prompt
    assert "Guards detail.png" in prompt
    assert "Screenshot missing: route /guards/1 failed to load" in prompt
    # the clean screen is listed before the failing one and carries no missing marker
    clean_part = prompt.split("## Capture manifest")[1].split("Guards detail")[0]
    assert "Screenshot missing:" not in clean_part


def test_the_same_input_gives_the_same_prompt() -> None:
    first = build_qa_agent_prompt(CONTRACT_TEXT, MANIFEST, REFERENCE_PATHS, SCHEMA)
    second = build_qa_agent_prompt(CONTRACT_TEXT, MANIFEST, REFERENCE_PATHS, SCHEMA)
    assert first == second


def test_the_prompt_stays_within_the_named_size_limit() -> None:
    manifest = [
        {"screen": "Screen", "png": "run/Screen.png", "dom": "run/Screen.dom.txt", "error": ""}
    ]
    huge_contract = "# Board\n" + "\n".join(f"- C{i}: item {i}" for i in range(1, 4000))
    prompt = build_qa_agent_prompt(huge_contract, manifest, REFERENCE_PATHS, SCHEMA)
    assert len(prompt) <= PROMPT_MAX_CHARS
    assert PROMPT_CUT_MARKER in prompt
