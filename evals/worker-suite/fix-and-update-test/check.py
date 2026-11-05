import importlib.util
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MODULE = ROOT / "src" / "durations.py"
TEST_FILE = ROOT / "tests" / "test_durations.py"
STALE_VALUES = ('"60m"', '"61m"')
CASES = [
    (0, "0s"),
    (59, "59s"),
    (60, "1m"),
    (90, "1m 30s"),
    (3600, "1h 0m"),
    (3661, "1h 1m"),
]


def main() -> int:
    spec = importlib.util.spec_from_file_location("durations", MODULE)
    if spec is None or spec.loader is None:
        print("FAIL: cannot load durations.py")
        return 1
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for seconds, expected in CASES:
        got = module.humanize(seconds)
        if got != expected:
            print(f"FAIL: humanize({seconds}) returned {got!r}, expected {expected!r}")
            return 1
    test_text = TEST_FILE.read_text(encoding="utf-8")
    for stale in STALE_VALUES:
        if stale in test_text:
            print(f"FAIL: tests/test_durations.py still asserts the old value {stale}")
            return 1
    run = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", str(TEST_FILE.parent)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if run.returncode != 0:
        print("FAIL: unit tests in tests/ do not pass")
        print(run.stderr[-500:])
        return 1
    print("PASS: humanize shows hours and tests/ match the new spec")
    return 0


if __name__ == "__main__":
    sys.exit(main())
