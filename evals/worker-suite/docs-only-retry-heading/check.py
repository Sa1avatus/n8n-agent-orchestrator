import hashlib
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent / "src"
DOC = ROOT / "docs" / "retries.md"
CODE = ROOT / "retry.py"
CODE_SHA256 = "287dc125eb160c5bf6a2277551bb5b224a4387289eeef77e050a14c8a6d10f58"
HEADING = "## Retry budget"
BUDGET = re.compile(r"\b3 attempts\b")


def section_body(lines: list[str]) -> list[str]:
    start = lines.index(HEADING) + 1
    end = next((i for i in range(start, len(lines)) if lines[i].startswith("## ")), len(lines))
    return lines[start:end]


def main() -> int:
    code_sha = hashlib.sha256(CODE.read_bytes()).hexdigest()
    if code_sha != CODE_SHA256:
        print("FAIL: src/retry.py was changed, this is a docs-only task")
        return 1
    lines = DOC.read_text(encoding="utf-8").splitlines()
    if lines.count(HEADING) != 1:
        print(f"FAIL: the heading {HEADING!r} must appear exactly once")
        return 1
    if "## Backoff" not in lines or "starting at 2 seconds" not in DOC.read_text(encoding="utf-8"):
        print("FAIL: the existing Backoff section was changed")
        return 1
    if not BUDGET.search("\n".join(section_body(lines))):
        print("FAIL: the Retry budget section does not state 3 attempts")
        return 1
    print(f"PASS: {HEADING} states the budget and retry.py is unchanged")
    return 0


if __name__ == "__main__":
    sys.exit(main())
