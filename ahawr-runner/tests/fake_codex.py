#!/usr/bin/env python3
"""Stand-in for the Codex CLI in tests: same `exec` argv, same `exec --json` JSONL shapes.

The fixture comes from FAKE_CODEX_FIXTURE (a name under fixtures/codex/, or a path ending in
.jsonl), else from a [[fixture:NAME]] directive in the prompt, else `success`. Lines starting
with `#` are the fixture header and are not printed; `# exit_code: N` sets the exit code.
Field shapes are those of docs/codex-executor.md; fields the header marks HYPOTHESIS are not
confirmed by a real run.
"""

import json
import os
import re
import sys
from pathlib import Path
from typing import Any

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "codex"
DEFAULT_FIXTURE = "success"
VALUE_OPTIONS = {
    "-o",
    "--output-last-message",
    "-s",
    "--sandbox",
    "-C",
    "--cd",
    "-c",
    "--config",
    "-m",
    "--model",
    "-p",
    "--profile",
    "-i",
    "--image",
    "--add-dir",
    "--color",
    "--enable",
    "--disable",
    "--output-schema",
    "--local-provider",
    "--thread-source",
}
ALIASES = {"--output-last-message": "-o", "--sandbox": "-s", "--config": "-c", "--model": "-m"}
EXIT_CODE_RE = re.compile(r"^# exit_code: (\d+)$", re.MULTILINE)
DIRECTIVE_RE = re.compile(r"\[\[fixture:([A-Za-z0-9_]+)\]\]")


def parse_argv(args: list[str]) -> tuple[dict[str, str], list[str]]:
    opts: dict[str, str] = {}
    positional: list[str] = []
    i = 0
    while i < len(args):
        arg = args[i]
        if arg in VALUE_OPTIONS and i + 1 < len(args):
            opts[ALIASES.get(arg, arg)] = args[i + 1]
            i += 2
        elif arg.startswith("-") and arg != "-":
            opts[ALIASES.get(arg, arg)] = ""
            i += 1
        else:
            positional.append(arg)
            i += 1
    return opts, positional


def select_fixture(prompt: str) -> Path:
    name = os.environ.get("FAKE_CODEX_FIXTURE")
    if not name:
        directive = DIRECTIVE_RE.search(prompt)
        name = directive.group(1) if directive else DEFAULT_FIXTURE
    if name.endswith(".jsonl"):
        return Path(name)
    return FIXTURES / f"{name}.jsonl"


def load_fixture(path: Path) -> tuple[int, list[dict[str, Any]]]:
    text = path.read_text(encoding="utf-8")
    exit_match = EXIT_CODE_RE.search(text)
    exit_code = int(exit_match.group(1)) if exit_match else 0
    events = [
        json.loads(line) for line in text.splitlines() if line.strip() and not line.startswith("#")
    ]
    return exit_code, events


def last_agent_message(events: list[dict[str, Any]]) -> str | None:
    texts = [
        event["item"]["text"]
        for event in events
        if event.get("type") == "item.completed"
        and event.get("item", {}).get("type") == "agent_message"
    ]
    return texts[-1] if texts else None


def main() -> int:
    args = sys.argv[1:]
    if args == ["--version"]:
        print("codex-cli 0.159.0 (fake)")
        return 0
    if not args or args[0] != "exec":
        print(f"fake codex: unsupported command {args[:1]}", file=sys.stderr)
        return 2
    opts, positional = parse_argv(args[1:])
    session_id: str | None = None
    if positional[:1] == ["resume"]:
        session_id = positional[1] if len(positional) > 1 else "last"
        positional = positional[2:]
    prompt = positional[0] if positional and positional[0] != "-" else sys.stdin.read()

    exit_code, events = load_fixture(select_fixture(prompt))
    for event in events:
        if event.get("type") == "thread.started":
            fresh_id = os.environ.get("FAKE_CODEX_THREAD_ID")
            event["thread_id"] = session_id or fresh_id or event.get("thread_id")
        print(json.dumps(event), flush=True)

    output = opts.get("-o")
    message = last_agent_message(events)
    if output and message is not None:
        Path(output).write_text(message + "\n", encoding="utf-8")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
