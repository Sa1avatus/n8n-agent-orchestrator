import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent / "src"
sys.path.insert(0, str(SRC))

import ledger  # noqa: E402
import money  # noqa: E402

CASES = [
    ("1.10", 110),
    ("0.07", 7),
    ("12", 1200),
    (" 3.5 ", 350),
    ("19.99", 1999),
]
TOTALS = [
    (["19.99", "0.01"], 2000),
    (["0.1", "0.2"], 30),
]


def main() -> int:
    for text, expected in CASES:
        got = money.parse_amount(text)
        if type(got) is not int or got != expected:
            print(f"FAIL: parse_amount({text!r}) returned {got!r}, expected int {expected}")
            return 1
    for lines, expected in TOTALS:
        got = ledger.total_cents(lines)
        if type(got) is not int or got != expected:
            print(f"FAIL: total_cents({lines!r}) returned {got!r}, expected int {expected}")
            return 1
    print("PASS: parse_amount returns int cents and total_cents adds them directly")
    return 0


if __name__ == "__main__":
    sys.exit(main())
