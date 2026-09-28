from __future__ import annotations

import argparse


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="claude-runner")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8700)
    args = parser.parse_args(argv)

    import uvicorn

    from .api import create_app

    uvicorn.run(create_app(), host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
