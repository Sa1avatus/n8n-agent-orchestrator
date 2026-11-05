import importlib.util
import sys
from pathlib import Path

MODULE = Path(__file__).resolve().parent / "src" / "records_table.py"
MIN_ROWS = 3000


def main() -> int:
    spec = importlib.util.spec_from_file_location("records_table", MODULE)
    if spec is None or spec.loader is None:
        print(f"FAIL: cannot load {MODULE.name}")
        return 1
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if len(module.ROWS) < MIN_ROWS:
        print(f"FAIL: ROWS has {len(module.ROWS)} rows, expected at least {MIN_ROWS}")
        return 1
    if module.total_weight([]) != 0:
        print("FAIL: total_weight([]) is not 0")
        return 1
    if module.total_weight([(1, "a", 2), (2, "b", 3)]) != 5:
        print("FAIL: total_weight of two rows does not include the last row")
        return 1
    expected = sum(weight for _, _, weight in module.ROWS)
    if module.total_weight(module.ROWS) != expected:
        print(f"FAIL: total_weight(ROWS) is {module.total_weight(module.ROWS)}, expected {expected}")
        return 1
    print(f"PASS: total_weight sums all {len(module.ROWS)} rows")
    return 0


if __name__ == "__main__":
    sys.exit(main())
