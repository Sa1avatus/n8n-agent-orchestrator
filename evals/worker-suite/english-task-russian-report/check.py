import importlib.util
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPORT = ROOT / "report" / "worker_report.md"
CYRILLIC = re.compile(r"[А-Яа-яЁё]")
MIN_CYRILLIC_LETTERS = 40


def check_inventory() -> str | None:
    spec = importlib.util.spec_from_file_location("inventory", ROOT / "src" / "inventory.py")
    if spec is None or spec.loader is None:
        return "cannot load src/inventory.py"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    stock = {"bolt": 3}
    try:
        module.remove_item(stock, "bolt", 5)
    except ValueError:
        pass
    else:
        return "remove_item(5 of 3) did not raise ValueError"
    if stock != {"bolt": 3}:
        return f"stock changed after a refused removal: {stock}"
    try:
        module.remove_item(stock, "nut", 1)
    except ValueError:
        pass
    else:
        return "remove_item on a missing item did not raise ValueError"
    module.remove_item(stock, "bolt", 3)
    if "bolt" in stock:
        return f"zero quantity was stored instead of deleted: {stock}"
    return None


def check_report() -> str | None:
    if not REPORT.exists():
        return "report/worker_report.md is missing"
    text = REPORT.read_text(encoding="utf-8")
    if not text.startswith("# Worker report"):
        return "report does not start with '# Worker report'"
    if "## Summary" not in text:
        return "report has no '## Summary' section"
    match = re.search(r"^## Итоги\s*$(.*?)(?=^## |\Z)", text, re.MULTILINE | re.DOTALL)
    if match is None:
        return "report has no '## Итоги' section"
    section = match.group(1)
    letters = len(CYRILLIC.findall(section))
    if letters < MIN_CYRILLIC_LETTERS:
        return f"'## Итоги' has {letters} Cyrillic letters, expected at least {MIN_CYRILLIC_LETTERS}"
    if "remove_item" not in section:
        return "'## Итоги' does not name remove_item"
    return None


def main() -> int:
    problem = check_inventory() or check_report()
    if problem:
        print(f"FAIL: {problem}")
        return 1
    print("PASS: remove_item is fixed and the report has an English Summary and a Russian Итоги section")
    return 0


if __name__ == "__main__":
    sys.exit(main())
