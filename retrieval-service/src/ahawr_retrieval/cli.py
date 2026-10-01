"""CLI: HTTP API server, JSON ``exec``, index, ad-hoc retrieval, log export, usage report."""

from __future__ import annotations

import argparse
import json
import sys
from contextlib import suppress
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

    usage = sub.add_parser(
        "usage",
        help="per-profile retrieval-usage metrics (selected/opened files, missed files, "
        "ahawr-search calls) from the log and the claude-runner API",
    )
    usage.add_argument("--runner-url", required=True, help="claude-runner base URL")
    usage.add_argument(
        "--since",
        nargs="?",
        type=_parse_since,
        const=0.0,
        default=None,
        help="unix timestamp or ISO/relative time (e.g. 1700000000, "
        "2026-01-01T00:00:00Z, -24h, -7d); default: all logged requests. "
        "A bare --since (no value) means the beginning of time (0).",
    )
    usage.add_argument(
        "--db",
        help="retrieval log sqlite path (default: <RETRIEVAL_DATA_DIR>/retrieval_logs.sqlite)",
    )
    usage.add_argument("--profile", help="print metrics for one profile only")
    usage.add_argument(
        "--top", type=int, default=10, help="show at most N missed files (default 10)"
    )
    usage.add_argument("--json", action="store_true", help="print the full report as JSON")

    argv = _normalize_since_argv(sys.argv[1:] if argv is None else argv)
    args = parser.parse_args(argv)
    settings = Settings.from_env()

    if args.command == "usage":
        import asyncio

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is None:
            return asyncio.run(_usage_async(args, settings))
        # A running loop is present (tests, interactive shells). We can't nest
        # ``asyncio.run``; schedule the coroutine and wait via a blocking shim.
        import threading

        result: list[int] = []
        errors: list[Exception] = []

        def runner() -> None:
            new_loop = asyncio.new_event_loop()
            try:
                asyncio.set_event_loop(new_loop)
                result.append(new_loop.run_until_complete(_usage_async(args, settings)))
            except Exception as exc:
                errors.append(exc)
            finally:
                new_loop.close()

        thread = threading.Thread(target=runner)
        thread.start()
        thread.join()
        if errors:
            raise errors[0]
        return result[0]

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
            index_response = service.index(
                IndexRequest(
                    corpus_id=args.corpus_id,
                    root=args.root,
                    mode="full" if args.full else "incremental",
                    force=args.force,
                )
            )
            print(index_response.model_dump_json(indent=2))
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


async def _usage_async(args: argparse.Namespace, settings: Settings) -> int:
    """Run the usage subcommand: read the log + runner API, print the report.

    ``await``s the report so it works both under a fresh ``asyncio.run`` (normal CLI
    use) and from inside an already-running event loop (tests / interactive shells).
    """
    from .logstore import RetrievalLog
    from .usage import usage_report

    db = Path(args.db) if args.db else settings.log_path
    if not db.exists():
        print(f"error: retrieval log not found: {db}", file=sys.stderr)
        return 1

    log = RetrievalLog(db)
    try:
        report = await usage_report(log, args.runner_url, args.since, args.profile)
    except Exception as exc:
        print(f"error: claude-runner not reachable at {args.runner_url}: {exc}", file=sys.stderr)
        return 1
    finally:
        with suppress(Exception):
            log.close()

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    profiles = list(report.keys())
    if not profiles:
        print("no usage data (empty log or unknown --profile)")
        return 0

    header = (
        f"{'profile':<10} {'selected':>8} {'opened':>8} {'precision':>9} "
        f"{'recall':>7} {'token_share':>11} {'searches':>8}"
    )
    print(f"runner: {args.runner_url}  since: {args.since if args.since is not None else 'all'}")
    print(header)
    for profile in profiles:
        prof = report[profile]
        print(
            f"{profile:<10} {prof['selected_files']:>8} {prof['opened_files']:>8} "
            f"{prof['precision']:>9.3f} {prof['recall']:>7.3f} "
            f"{prof['token_share']:>11.3f} {prof['ahawr_search_calls']:>8}"
        )

    for profile in profiles:
        missed = report[profile]["missed_files"]
        if missed:
            print(f"\nmissed files ({profile}):")
            for item in missed[: args.top]:
                print(f"  {item['requests']:>3}  {item['path']}")
            if len(missed) > args.top:
                print(f"  … {len(missed) - args.top} more")
    return 0


def _normalize_since_argv(argv: list[str] | None) -> list[str] | None:
    """Rewrite ``--since -VALUE`` as ``--since=-VALUE``.

    argparse treats a lone leading dash as a potential option, so the relative forms
    (``-24h``, ``-7d``) must be glued to ``--since`` with ``=`` to parse as a value.
    Only a token that looks like a relative ``--since`` value (``-N`` followed by
    ``h``/``d``/``w``) is rewritten; other arguments are left untouched.
    """
    if argv is None:
        return None
    out: list[str] = []
    i = 0
    while i < len(argv):
        if argv[i] == "--since" and i + 1 < len(argv) and _is_relative_since(argv[i + 1]):
            out.append(f"--since={argv[i + 1]}")
            i += 2
        else:
            out.append(argv[i])
            i += 1
    return out


def _is_relative_since(token: str) -> bool:
    """True for relative ``--since`` values: ``-N`` digits plus ``h``/``d``/``w``."""
    if len(token) < 2 or token[0] != "-":
        return False
    digits = token[1:-1]
    return digits.isdigit() and token[-1] in "hdw"


def _parse_since(value: str) -> float:
    """Parse ``--since``: unix timestamp, ISO-8601, or a relative offset like ``-24h``.

    The value is passed through argparse: ``nargs="?"`` with ``const=0.0`` means a bare
    ``--since`` (no value) yields 0.0; a supplied string is parsed below. Returns
    ``None`` when the caller did not pass ``--since`` at all (argparse default).

    A relative offset is resolved against the current time: ``-24h`` / ``-7d`` / ``-1w``
    yield ``time.time() - offset`` (a positive unix timestamp a few days in the past), not a
    negative absolute timestamp. A bare ``+7d`` yields ``time.time() + offset``.
    """
    if value is None:
        return None
    text = value.strip()
    if text.lower().endswith(("h", "d", "w")):
        import time

        sign = -1.0 if text[0] == "-" else 1.0
        digits, unit = text[1:-1], text[-1]
        try:
            amount = float(digits)
        except ValueError as exc:
            raise ValueError(f"invalid relative --since: {value!r}") from exc
        factor = {"h": 3600.0, "d": 86400.0, "w": 604800.0}[unit]
        return time.time() + sign * amount * factor
    try:
        return float(text)
    except ValueError:
        pass
    try:
        from datetime import UTC, datetime

        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.timestamp()
    except ValueError as exc:
        raise ValueError(f"cannot parse --since: {value!r}") from exc


if __name__ == "__main__":
    raise SystemExit(main())
