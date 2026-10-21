"""``python -m claude_runner.qa``: command line for the throwaway QA stack.

``stack up`` prints the dashboard URL and the stack id only: the DSN carries the password
and is never printed. ``stack down <stack id>`` removes what ``stack up`` created; the
object names are derived from the stack id, so no state file is needed.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Callable
from typing import Any

from . import qa_runner, qa_stack
from .qa_runner import TAGS
from .qa_stack import QA_DATABASE_PREFIX, QA_FIXTURES, QA_PREFIX, QaStack, QaStackError

_STACK_ID_PATTERN = re.compile(r"[0-9a-f]{8}")
NIGHTLY_VERDICT_PATH = qa_stack.CLAUDE_RUNNER_DIR / "qa" / "nightly-verdict.json"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m claude_runner.qa")
    commands = parser.add_subparsers(dest="command", required=True, metavar="command")
    stack = commands.add_parser("stack", help="start or stop the throwaway QA stack")
    stack_actions = stack.add_subparsers(dest="action", required=True, metavar="action")
    up = stack_actions.add_parser("up", help="start a fresh Postgres and dashboard")
    up.add_argument("--fixture", choices=QA_FIXTURES, default="default", help="seed data set")
    up.set_defaults(handler=_stack_up)
    down = stack_actions.add_parser("down", help="remove a stack by the id that up printed")
    down.add_argument("stack_id", help="stack id printed by stack up")
    down.set_defaults(handler=_stack_down)
    run = commands.add_parser("run", help="run browser scenarios and print their verdicts")
    run.add_argument("--tag", choices=sorted(TAGS), help="only scenarios with this tag")
    run.add_argument("--scenario", metavar="NAME", help="only the scenario with this name")
    run.set_defaults(handler=_run)
    nightly = commands.add_parser(
        "nightly", help="run every scenario and write the nightly verdict file"
    )
    nightly.set_defaults(handler=_nightly)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    handler: Callable[[argparse.Namespace], int] = args.handler
    try:
        return handler(args)
    except (QaStackError, ValueError) as error:
        print(f"qa: {error}", file=sys.stderr)
        return 1


def _stack_up(args: argparse.Namespace) -> int:
    stack = qa_stack.stack_up(args.fixture)
    print(f"dashboard: {stack.dashboard_url}")
    print(f"stack id: {stack.run_id}")
    return 0


def _stack_down(args: argparse.Namespace) -> int:
    qa_stack.stack_down(_stack_from_id(args.stack_id))
    print(f"stack {args.stack_id} removed")
    return 0


def _run(args: argparse.Namespace) -> int:
    scenarios = qa_runner.select_scenarios(
        qa_runner.load_scenarios(), tag=args.tag, name=args.scenario
    )
    verdicts = _run_all(scenarios)
    print(json.dumps(verdicts, indent=2))
    return 0 if all(verdict["verdict"] == "pass" for verdict in verdicts) else 1


def _nightly(args: argparse.Namespace) -> int:
    verdicts = _run_all(qa_runner.load_scenarios())
    NIGHTLY_VERDICT_PATH.parent.mkdir(parents=True, exist_ok=True)
    NIGHTLY_VERDICT_PATH.write_text(json.dumps(verdicts, indent=2), encoding="utf-8")
    print(f"nightly verdict: {NIGHTLY_VERDICT_PATH}")
    return 0 if all(verdict["verdict"] == "pass" for verdict in verdicts) else 1


def _run_all(scenarios: list[qa_runner.Scenario]) -> list[dict[str, Any]]:
    return [
        qa_runner.run_browser_scenario(scenario, max_minutes=qa_runner.DEFAULT_MAX_MINUTES)
        for scenario in scenarios
    ]


def _stack_from_id(stack_id: str) -> QaStack:
    if not _STACK_ID_PATTERN.fullmatch(stack_id):
        raise ValueError(f"stack id {stack_id!r} is not 8 lowercase hex digits")
    host = f"{QA_PREFIX}{stack_id}"
    return QaStack(
        run_id=stack_id,
        host=host,
        database=f"{QA_DATABASE_PREFIX}{stack_id}",
        dsn="",
        containers=[host, f"{host}-dashboard"],
        volumes=[f"{host}-data"],
        networks=[f"{host}-net"],
        images=[f"{QA_PREFIX}dashboard:{stack_id}"],
    )


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
