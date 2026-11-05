import importlib.util
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent / "src"
MAX_PROBE_RUNS = 2


def main() -> int:
    spec = importlib.util.spec_from_file_location("latency_status", SRC / "latency_status.py")
    if spec is None or spec.loader is None:
        print("FAIL: cannot load latency_status.py")
        return 1
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    cases = [(0, "ok"), (200, "ok"), (201, "slow")]
    for latency_ms, expected in cases:
        got = module.classify(latency_ms)
        if got != expected:
            print(f"FAIL: classify({latency_ms}) is {got!r}, expected {expected!r}")
            return 1
    log = SRC / "probe_runs.log"
    runs = len(log.read_text(encoding="utf-8").splitlines()) if log.exists() else 0
    if runs > MAX_PROBE_RUNS:
        print(f"FAIL: the probe ran {runs} times, at most {MAX_PROBE_RUNS} were needed")
        return 1
    print(f"PASS: classify honours the inclusive 200 ms limit; probe ran {runs} time(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
