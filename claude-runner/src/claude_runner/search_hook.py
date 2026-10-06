"""PreToolUse hook that steers a run to ``ahawr-search`` before it explores with grep/rg/find.

A prompt rule ("search with ahawr-search first") is followed once at best by a small local model,
which then falls back to the habit of ``grep``. This hook makes the rule hold: until the session
has called ``ahawr-search`` it refuses exploratory searches (rg, find, recursive grep, git grep,
the Grep and Glob tools) with a message that shows the syntax, and after that it asks again every
few exploratory searches. Checks of a known file (``grep -n text path/file.py``) are never touched.

Claude Code starts it for every Bash, Grep and Glob call (see ``claude_cli.build_command``) and
passes the call as JSON on stdin. Exit code 2 blocks the call and feeds stderr back to the model;
any other exit lets it run. It must never break a run, so every error means "allow".
"""

from __future__ import annotations

import json
import os
import re
import shlex
import sys
import time
from pathlib import Path
from typing import Any

SEARCH_COMMAND = "ahawr-search"
# until the first ahawr-search call: refuse this many exploratory searches, then let them through
MAX_FIRST_DENIALS = 2
DEFAULT_EVERY = 6

FIRST_MESSAGE = (
    "Search with ahawr-search before you explore with grep, rg, find, Grep or Glob. It ranks the "
    "whole project and returns fragments as path:lines.\n"
    'Example: ahawr-search "where is the retry delay applied" --k 8 '
    "(add --budget 3000 for more text)\n"
    "Then open the returned file around the given lines with Read. If it returns nothing usable, "
    "run your search again and it will be allowed. Checking a file you already know "
    "(grep -n text path/to/file) is allowed at any time."
)
AGAIN_MESSAGE = (
    "You have explored with grep, rg or find {n} times since your last ahawr-search. Try "
    'ahawr-search "what you are looking for, in words" --k 8 for this question first; if it is '
    "not useful, run the same command again and it will be allowed."
)

_SPLIT = re.compile(r"\s*(?:&&|\|\||;|\||\n)\s*")
_GREP = {"grep", "egrep", "fgrep"}
_LONG_VALUE_FLAGS = {"--include", "--exclude", "--exclude-dir", "--max-count", "--regexp", "--file"}


def _words(segment: str) -> list[str]:
    try:
        return shlex.split(segment)
    except ValueError:
        return segment.split()


def _split_command(words: list[str]) -> tuple[str, list[str]]:
    """``(program, arguments)`` of one command, skipping ``VAR=x`` prefixes and time/nice/sudo."""
    for i, word in enumerate(words):
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", word) or word in {"time", "nice", "sudo"}:
            continue
        return os.path.basename(word), words[i + 1 :]
    return "", []


def _segments(command: str) -> list[list[str]]:
    return [w for w in (_words(s) for s in _SPLIT.split(command) if s.strip()) if w]


def calls_search(command: str) -> bool:
    """True when some part of the command runs ``ahawr-search`` (``command -v`` does not count)."""
    return any(_split_command(words)[0] == SEARCH_COMMAND for words in _segments(command))


def _grep_is_exploratory(args: list[str], cwd: str) -> bool:
    """A recursive grep, or one whose paths include a directory or a glob."""
    pattern_flag = False
    recursive = False
    operands: list[str] = []
    skip = False
    for arg in args:
        if skip:
            skip = False
            continue
        if arg.startswith("--"):
            recursive = recursive or arg in {"--recursive", "--dereference-recursive"}
            pattern_flag = pattern_flag or arg.startswith(("--regexp", "--file"))
            skip = arg in _LONG_VALUE_FLAGS
        elif arg.startswith("-") and len(arg) > 1:
            letters = arg[1:]
            recursive = recursive or "r" in letters or "R" in letters
            if letters[-1] in "efmABCdD":  # the last letter of the cluster takes the next word
                pattern_flag = pattern_flag or letters[-1] in "ef"
                skip = True
        else:
            operands.append(arg)
    if recursive:
        return True
    paths = operands if pattern_flag else operands[1:]
    # no paths: it filters a pipe (``... | grep text``)
    return any(
        path in {".", ".."}
        or path.endswith("/")
        or any(c in path for c in "*?")
        or os.path.isdir(os.path.join(cwd, path))
        for path in paths
    )


def is_exploratory(command: str, cwd: str = ".") -> bool:
    for words in _segments(command):
        name, rest = _split_command(words)
        if name in {"rg", "ag", "ack", "find"}:
            return True
        if name == "git" and rest[:1] == ["grep"]:
            return True
        if name in _GREP and _grep_is_exploratory(rest, cwd):
            return True
    return False


def state_dir() -> Path:
    raw = os.environ.get("CLAUDE_CONFIG_DIR", "").strip()
    return (Path(raw) if raw else Path.home() / ".claude") / "search-first"


def _load(path: Path) -> dict[str, Any]:
    try:
        return dict(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return {}


def _save(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state), encoding="utf-8")
    os.replace(tmp, path)


def _log(directory: Path, session: str, event: str, detail: str) -> None:
    line = {"ts": round(time.time(), 1), "session": session, "event": event, "detail": detail[:160]}
    try:
        with (directory / "decisions.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(line) + "\n")
    except OSError:
        pass


def decide(payload: dict[str, Any], directory: Path, every: int = DEFAULT_EVERY) -> str | None:
    """None to allow the call, otherwise the message to hand back to the model."""
    tool = str(payload.get("tool_name") or "")
    tool_input = payload.get("tool_input") or {}
    session = str(payload.get("session_id") or "unknown")
    path = directory / f"{session}.json"
    state = _load(path)
    command = str(tool_input.get("command") or "") if tool == "Bash" else ""
    if tool == "Bash":
        if calls_search(command):
            state.update(searched=True, since=0)
            _save(path, state)
            _log(directory, session, "search", command)
            return None
        explore = is_exploratory(command, str(payload.get("cwd") or "."))
    else:
        explore = tool in {"Grep", "Glob"}
    if not explore:
        return None
    if not state.get("searched"):
        if int(state.get("denied", 0)) >= MAX_FIRST_DENIALS:
            return None
        state["denied"] = int(state.get("denied", 0)) + 1
        _save(path, state)
        _log(directory, session, "deny-first", command or tool)
        return FIRST_MESSAGE
    state["since"] = int(state.get("since", 0)) + 1
    if every > 0 and state["since"] > every:
        n = state["since"]
        state["since"] = 0
        _save(path, state)
        _log(directory, session, "deny-again", command or tool)
        return AGAIN_MESSAGE.format(n=n)
    _save(path, state)
    return None


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        every = int(os.environ.get("AHAWR_SEARCH_FIRST_EVERY", DEFAULT_EVERY))
        message = decide(payload, state_dir(), every)
    except Exception:  # noqa: BLE001 - a hook must never break a run
        return 0
    if message:
        print(message, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
