import sys
from pathlib import Path

from latency_status import classify

HERE = Path(__file__).resolve().parent


def main() -> int:
    latency_ms = int((HERE / "latency.txt").read_text(encoding="utf-8").strip())
    with (HERE / "probe_runs.log").open("a", encoding="utf-8") as log:
        log.write(f"{latency_ms}\n")
    print(f"status: {classify(latency_ms)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
