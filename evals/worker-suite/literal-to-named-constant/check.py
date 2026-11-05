import ast
import importlib.util
import sys
from pathlib import Path

MODULE = Path(__file__).resolve().parent / "src" / "client.py"
CONSTANT = "REQUEST_TIMEOUT_SECONDS"
TIMEOUT = 30


def main() -> int:
    source = MODULE.read_text(encoding="utf-8")
    tree = ast.parse(source)
    function = next(
        (node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "request_options"),
        None,
    )
    if function is None:
        print("FAIL: request_options is missing from client.py")
        return 1
    literals = [
        node.value
        for node in ast.walk(function)
        if isinstance(node, ast.Constant) and isinstance(node.value, int) and not isinstance(node.value, bool)
    ]
    if TIMEOUT in literals:
        print(f"FAIL: request_options still contains the literal {TIMEOUT}")
        return 1
    names = [node.id for node in ast.walk(function) if isinstance(node, ast.Name)]
    if CONSTANT not in names:
        print(f"FAIL: request_options does not use {CONSTANT}")
        return 1
    spec = importlib.util.spec_from_file_location("client", MODULE)
    if spec is None or spec.loader is None:
        print("FAIL: cannot load client.py")
        return 1
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    value = getattr(module, CONSTANT, None)
    if type(value) is not int or value != TIMEOUT:
        print(f"FAIL: {CONSTANT} must be the int {TIMEOUT}, found {value!r}")
        return 1
    got = module.request_options(2)
    if got != {"timeout": TIMEOUT, "retries": 2}:
        print(f"FAIL: request_options(2) returned {got!r}")
        return 1
    print(f"PASS: request_options uses {CONSTANT} and keeps the behaviour")
    return 0


if __name__ == "__main__":
    sys.exit(main())
