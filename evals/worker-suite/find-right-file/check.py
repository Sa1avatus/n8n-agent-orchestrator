import re
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent / "src"
DECOYS = [
    SRC / "services" / "profile_loader_old.py",
    SRC / "archive" / "2024" / "profile_loader.py",
    SRC / "tests" / "fixtures" / "profile_loader_sample.py",
]
LIMIT_RE = re.compile(r"^RETRY_LIMIT\s*=\s*(\d+)", re.MULTILINE)


def retry_limit(path: Path) -> str | None:
    match = LIMIT_RE.search(path.read_text(encoding="utf-8"))
    return match.group(1) if match else None


def main() -> int:
    definers = [
        path
        for path in SRC.rglob("*.py")
        if re.search(r"^def load_profile\(", path.read_text(encoding="utf-8"), re.MULTILINE)
    ]
    if len(definers) != 1:
        print(f"FAIL: expected one file defining load_profile(), found {len(definers)}")
        return 1
    live = definers[0]
    if retry_limit(live) != "5":
        print(f"FAIL: RETRY_LIMIT in {live.relative_to(SRC)} is {retry_limit(live)}, expected 5")
        return 1
    for decoy in DECOYS:
        if retry_limit(decoy) != "3":
            print(f"FAIL: decoy {decoy.relative_to(SRC)} was changed (RETRY_LIMIT={retry_limit(decoy)})")
            return 1
    print("PASS: live RETRY_LIMIT is 5 and the decoys are unchanged")
    return 0


if __name__ == "__main__":
    sys.exit(main())
