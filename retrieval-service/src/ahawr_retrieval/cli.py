"""CLI: HTTP API server, JSON ``exec``, index, ad-hoc retrieval, log export."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .config import Settings
from .logstore import RetrievalLog
from .models import IndexRequest, RetrieveRequest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ahawr-retrieval")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("exec", help="run one action from a JSON/base64 payload, print JSON")
    run.add_argument("payload", help="base64 JSON, raw JSON, or '-' to read stdin")

    serve = sub.add_parser("serve", help="run the HTTP API (needs the [server] extra)")
    serve.add_argument("--host", default="0.0.0.0")
    serve.add_argument("--port", type=int, default=8500)

    index = sub.add_parser("index", help="index or incrementally sync a workspace corpus")
    index.add_argument("corpus_id")
    index.add_argument("--root")
    index.add_argument("--full", action="store_true")
    index.add_argument("--force", action="store_true")

    retrieve = sub.add_parser("retrieve", help="run one retrieval and print the JSON response")
    retrieve.add_argument("--corpus", action="append", required=True)
    retrieve.add_argument("--profile", default="worker")
    retrieve.add_argument("--query", required=True)

    export = sub.add_parser("export-logs", help="export candidate feature rows as JSONL")
    export.add_argument("--log-db", help="defaults to <RETRIEVAL_DATA_DIR>/retrieval_logs.sqlite")
    export.add_argument("--since", type=float, default=0.0, help="unix timestamp")
    export.add_argument("--out", default="-")

    args = parser.parse_args(argv)
    settings = Settings.from_env()

    if args.command == "exec":
        from .embedded import decode_payload, execute

        try:
            token = sys.stdin.read() if args.payload == "-" else args.payload
            outcome = execute(decode_payload(token), settings)
        except ValueError as exc:
            outcome = {"ok": False, "action": None, "error": str(exc), "status_code": 400}
        sys.stdout.write(json.dumps(outcome, ensure_ascii=False) + "\n")
        return 0

    if args.command == "serve":
        import uvicorn

        from .api import create_app

        uvicorn.run(create_app(settings), host=args.host, port=args.port)
        return 0

    if args.command == "export-logs":
        log = RetrievalLog(args.log_db or settings.log_path)
        out = sys.stdout if args.out == "-" else Path(args.out).open("w", encoding="utf-8")  # noqa: SIM115
        count = 0
        try:
            for row in log.iter_feature_rows(args.since):
                out.write(json.dumps(row, ensure_ascii=False) + "\n")
                count += 1
        finally:
            if out is not sys.stdout:
                out.close()
        print(f"exported {count} rows", file=sys.stderr)
        return 0

    from .service import RetrievalService

    service = RetrievalService(settings)
    try:
        if args.command == "index":
            result = service.index(
                IndexRequest(
                    corpus_id=args.corpus_id,
                    root=args.root,
                    mode="full" if args.full else "incremental",
                    force=args.force,
                )
            )
            print(result.model_dump_json(indent=2))
        else:
            response = service.retrieve(
                RetrieveRequest(
                    profile=args.profile,
                    corpora=args.corpus,
                    query=args.query,
                )
            )
            print(response.model_dump_json(indent=2, exclude={"context"}))
    finally:
        service.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
