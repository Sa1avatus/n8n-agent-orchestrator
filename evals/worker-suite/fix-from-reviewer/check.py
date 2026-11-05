import importlib.util
import sys
from pathlib import Path

MODULE = Path(__file__).resolve().parent / "src" / "textutil.py"
CASES = [
    ("Hello World", "hello-world"),
    ("  Hello   World  ", "hello-world"),
    ("a--b", "a-b"),
    ("--x--", "x"),
]


def main() -> int:
    spec = importlib.util.spec_from_file_location("textutil", MODULE)
    if spec is None or spec.loader is None:
        print(f"FAIL: cannot load {MODULE.name}")
        return 1
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for text, expected in CASES:
        got = module.slugify(text)
        if got != expected:
            print(f"FAIL: slugify({text!r}) returned {got!r}, expected {expected!r}")
            return 1
    print("PASS: slugify meets all four Reviewer examples")
    return 0


if __name__ == "__main__":
    sys.exit(main())
