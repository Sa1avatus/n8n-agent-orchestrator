"""Build the deterministic prompt of the qa_agent run: the contract text, the capture
manifest (screen, screenshot and DOM paths, a missing marker for a failed screen),
the reference images and the answer schema. The agent only reads PNG files, writes no
file and runs no command, and ends with one JSON array of item verdicts."""

from __future__ import annotations

from collections.abc import Sequence

# The bound on the prompt: the contract text is cut when it alone would exceed it.
PROMPT_MAX_CHARS = 60_000
PROMPT_CUT_MARKER = "[qa agent prompt cut at the size limit]"


def build_qa_agent_prompt(
    contract_text: str,
    manifest: list[dict[str, str]],
    reference_image_paths: Sequence[str],
    answer_schema: str,
) -> str:
    """Build the qa_agent prompt; the same input always gives the same prompt."""
    fixed = "\n\n".join(
        [
            _header(),
            "## Capture manifest\n" + _manifest_lines(manifest),
            "## Reference images\n" + _reference_lines(reference_image_paths),
            "## Answer schema\n" + answer_schema,
            "## Output\n" + _rules(),
        ]
    )
    # The contract is the only variable bulk; when it would push the prompt past the
    # limit it is cut to fit, keeping every other section whole.
    header = "## Design contract\n"
    budget = PROMPT_MAX_CHARS - len(fixed) - len(header) - 4
    if len(contract_text) <= max(0, budget):
        return header + contract_text + "\n\n" + fixed
    # shortcut: a contract over the limit is cut whole, which can drop item lines;
    # upgrade to cut only non-item lines when contracts grow that large
    marker = "\n" + PROMPT_CUT_MARKER + "\n\n"
    kept = contract_text[: max(0, budget - len(marker))]
    return header + kept + marker + fixed


def _header() -> str:
    return (
        "You are the qa_agent. Check the mission's UI against its design contract and "
        "answer every check item."
    )


def _manifest_lines(manifest: list[dict[str, str]]) -> str:
    lines: list[str] = []
    for entry in manifest:
        lines.append(f"- Screen: {entry.get('screen', '')}")
        lines.append(f"  Screenshot: {entry.get('png', '')}")
        if entry.get("error", ""):
            lines.append(f"  Screenshot missing: {entry['error']}")
        lines.append(f"  DOM text: {entry.get('dom', '')}")
    return "\n".join(lines)


def _reference_lines(reference_image_paths: Sequence[str]) -> str:
    return "\n".join(f"- {path}" for path in reference_image_paths)


def _rules() -> str:
    return (
        "Read the PNG files with your Read tool. Write no files and run no commands.\n"
        "End with exactly ONE JSON array, one element per check item, in the items' order. "
        "Each element has the keys:\n"
        '  "item": the item id (for example "C1")\n'
        '  "verdict": "pass", "fail" or "blocked"\n'
        '  "evidence": the screenshot file name or the API line that proves the verdict\n'
        '  "reason": one sentence'
    )
