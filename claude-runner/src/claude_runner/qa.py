"""``python -m claude_runner.qa``: command line for the throwaway QA stack.

``stack up`` prints the dashboard URL and the stack id only: the DSN carries the password
and is never printed. ``stack down <stack id>`` removes what ``stack up`` created; the
object names are derived from the stack id, so no state file is needed.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import urllib.error
import urllib.request
from collections.abc import Callable, Collection, Mapping
from pathlib import Path
from typing import Any

from . import board_pg, board_ready, qa_api, qa_regression, qa_runner, qa_smoke, qa_stack, qa_worker
from .board import BoardStore, InMemoryBoardStore
from .qa_runner import TAGS
from .qa_stack import QA_FIXTURES, QaStackError, stack_from_id
from .roles_config import resolve

log = logging.getLogger("claude_runner.qa")

NIGHTLY_VERDICT_PATH = qa_stack.CLAUDE_RUNNER_DIR / "qa" / "nightly-verdict.json"
PROFILES_PATH = qa_stack.CLAUDE_RUNNER_DIR / "qa" / "projects.yaml"
API_ROUTES_PATH = qa_stack.CLAUDE_RUNNER_DIR / "qa" / "api_routes.yaml"
REGRESSION_RUNS_DIR = qa_stack.QA_RUNS_DIR
STACK_MODE_ENV = "QA_STACK_MODE"
DEFAULT_STACK_MODE = "docker"
MAX_NIGHTLY_PROFILES = 20
SMOKE_LANGUAGES = ("en", "ru")


class RegressionStopped(Exception):
    def __init__(self, reason: str, *, timed_out: bool = False) -> None:
        super().__init__(reason)
        self.reason = reason
        self.timed_out = timed_out


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
    _add_stack_option(run)
    run.set_defaults(handler=_run)
    api = commands.add_parser(
        "api", help="run the api layer on a fresh stack and print its verdicts"
    )
    _add_stack_option(api)
    api.set_defaults(handler=_api)
    nightly = commands.add_parser(
        "nightly", help="run every scenario and write the nightly verdict file"
    )
    nightly.set_defaults(handler=_nightly)
    regression = commands.add_parser(
        "regression", help="run the regression commands of a project and print the verdict"
    )
    regression.add_argument(
        "--workdir", required=True, help="project folder, host or container path"
    )
    regression.set_defaults(handler=_regression)
    baseline = commands.add_parser(
        "baseline", help="run the regression commands of a project and write its baseline file"
    )
    baseline.add_argument("project", help="project name in qa/projects.yaml")
    baseline.set_defaults(handler=_baseline)
    smoke = commands.add_parser("smoke", help="crawl the board in a browser and print the verdict")
    smoke.add_argument("--url", help="base URL to crawl; without it a throwaway stack is started")
    smoke.add_argument(
        "--fixture", choices=QA_FIXTURES, help="seed data set of the throwaway stack (default)"
    )
    smoke.add_argument(
        "--lang", choices=SMOKE_LANGUAGES, help="interface language of the crawl (default both)"
    )
    smoke.add_argument("--allow-host", metavar="NAME", help="host name the crawl may run against")
    smoke.add_argument(
        "--max-pages", type=int, default=qa_smoke.MAX_PAGES, help="pages to visit in one crawl"
    )
    smoke.add_argument(
        "--max-clicks-per-page",
        type=int,
        default=qa_smoke.MAX_CLICKS_PER_PAGE,
        help="controls clicked on one page",
    )
    smoke.add_argument(
        "--max-minutes",
        type=float,
        default=qa_smoke.MAX_MINUTES,
        help="wall-clock minutes for one crawl",
    )
    smoke.set_defaults(handler=_smoke)
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
    qa_stack.stack_down(stack_from_id(args.stack_id))
    print(f"stack {args.stack_id} removed")
    return 0


def _run(args: argparse.Namespace) -> int:
    stack_mode = _stack_mode(args.stack)
    scenarios = qa_runner.select_scenarios(
        qa_runner.load_scenarios(), tag=args.tag, name=args.scenario
    )
    verdicts = _run_all(scenarios, stack_mode)
    print(json.dumps(verdicts, indent=2))
    return 0 if all(verdict["verdict"] == "pass" for verdict in verdicts) else 1


def _add_stack_option(command: argparse.ArgumentParser) -> None:
    command.add_argument(
        "--stack",
        choices=qa_stack.STACK_MODES,
        help=f"stack mode (default {DEFAULT_STACK_MODE}, or ${STACK_MODE_ENV} when set)",
    )


def _api(args: argparse.Namespace) -> int:
    verdicts = _api_verdicts(_stack_mode(args.stack))
    print(json.dumps(verdicts, indent=2))
    return 0 if all(verdict["verdict"] == "pass" for verdict in verdicts) else 1


class _HttpApiClient:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url

    def request(
        self, method: str, path: str, *, headers: Mapping[str, str], timeout_seconds: float
    ) -> qa_api.ApiResponse:
        request = urllib.request.Request(self.base_url + path, method=method, headers=dict(headers))
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                return _api_response(response.status, response.read())
        except urllib.error.HTTPError as error:
            return _api_response(error.code, error.read())


def _api_response(status: int, body: bytes) -> qa_api.ApiResponse:
    return qa_api.ApiResponse(status_code=status, body=body.decode("utf-8", errors="replace"))


def _stack_mode(option: str | None) -> str:
    mode = option or os.environ.get(STACK_MODE_ENV) or DEFAULT_STACK_MODE
    if mode not in qa_stack.STACK_MODES:
        allowed = ", ".join(qa_stack.STACK_MODES)
        raise ValueError(f"{STACK_MODE_ENV} {mode!r} is not one of {allowed}")
    return mode


def _nightly(args: argparse.Namespace) -> int:
    stack_mode = _stack_mode(None)
    scenarios = qa_runner.load_scenarios()
    scenario_verdicts = _run_all(scenarios, stack_mode)
    layer_verdicts: list[dict[str, Any]] = []
    for profile in qa_regression.load_profiles(PROFILES_PATH)[:MAX_NIGHTLY_PROFILES]:
        layer_verdicts.extend(
            qa_worker.run_layers(
                ["regression", "api", "ui"], _nightly_runners(profile, stack_mode)
            )
        )
    verdicts = scenario_verdicts + layer_verdicts
    payload: dict[str, Any] = {"scenarios": scenario_verdicts, "layers": layer_verdicts}
    options = _qa_options(_store_for_options())
    if qa_worker.smoke_enabled(options):
        smoke_verdict = _smoke_crawl(
            qa_smoke.load_seeds(),
            SMOKE_LANGUAGES,
            (),
            {},
            fixture="default",
            stack_mode=stack_mode,
        )
        payload["smoke"] = smoke_verdict
        verdicts.append(smoke_verdict)
    NIGHTLY_VERDICT_PATH.parent.mkdir(parents=True, exist_ok=True)
    NIGHTLY_VERDICT_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"nightly verdict: {NIGHTLY_VERDICT_PATH}")
    passed = all(verdict["verdict"] == "pass" for verdict in verdicts)
    return 0 if passed else 1


def _store_for_options() -> BoardStore:
    dsn = os.environ.get("CLAUDE_RUNNER_BOARD_DSN", "").strip()
    if dsn:
        try:
            return board_pg.get_board_store(dsn)
        except Exception as error:  # the config is unreadable; smoke stays off
            log.warning("hermes_config store unavailable: %s", error)
            return InMemoryBoardStore()
    return InMemoryBoardStore()


def _qa_options(store: BoardStore) -> dict[str, Any]:
    """The qa role options from the hermes_config roles_json; ``{}`` when the config
    is missing or unreadable, so smoke stays off in that case."""
    try:
        roles = board_ready.effective_roles(store, "")
        return dict(resolve(roles, "qa", 1, {}).options)
    except ValueError as error:
        log.warning("qa options not read: %s", error)
        return {}


def _smoke(args: argparse.Namespace) -> int:
    if args.url is not None and args.fixture is not None:
        raise ValueError("--fixture seeds the throwaway stack and cannot be used with --url")
    allow_hosts: Collection[str] = ()
    if args.allow_host is not None:
        allow_hosts = (args.allow_host,)
    seeds = qa_smoke.load_seeds()
    limits: dict[str, Any] = {
        "max_pages": args.max_pages,
        "max_clicks_per_page": args.max_clicks_per_page,
        "max_minutes": args.max_minutes,
    }
    languages: tuple[str, ...] = SMOKE_LANGUAGES if args.lang is None else (args.lang,)
    verdict = _smoke_crawl(
        seeds,
        languages,
        allow_hosts,
        limits,
        base_url=args.url,
        fixture=args.fixture or "default",
        stack_mode=_stack_mode(None),
    )
    print(json.dumps(verdict, indent=2))
    return 0 if verdict["verdict"] == "pass" else 1


def _smoke_crawl(
    seeds: qa_smoke.Seeds,
    languages: Collection[str],
    allow_hosts: Collection[str],
    limits: dict[str, Any],
    *,
    base_url: str | None = None,
    fixture: str = "default",
    stack_mode: str = DEFAULT_STACK_MODE,
) -> dict[str, Any]:
    """Crawl the board in the given languages and merge them into one verdict.
    With a base url the crawl runs against it; otherwise it runs on a throwaway
    stack, one stack for all the languages, which is stopped when the crawl
    finishes."""
    if base_url is not None:
        runs = _crawl_languages(seeds, base_url, languages, allow_hosts, limits)
        return _merged_smoke_verdict(runs)
    stack = qa_stack.stack_up(fixture, mode=stack_mode)
    try:
        return _merged_smoke_verdict(
            _crawl_languages(seeds, stack.dashboard_url, languages, allow_hosts, limits)
        )
    finally:
        qa_stack.stack_down(stack)


def _crawl_languages(
    seeds: qa_smoke.Seeds,
    base_url: str,
    languages: Collection[str],
    allow_hosts: Collection[str],
    limits: dict[str, Any],
) -> list[tuple[str, dict[str, Any]]]:
    return [
        (
            code,
            qa_smoke.run_smoke(
                seeds, base_url=base_url, lang=code, allow_hosts=allow_hosts, **limits
            ),
        )
        for code in languages
    ]


def _merged_smoke_verdict(runs: list[tuple[str, dict[str, Any]]]) -> dict[str, Any]:
    """One verdict for a multi-language crawl: blocked if any run is blocked,
    fail if any run fails, pass only when all pass. The findings keep the
    findings of every run, each tagged with its ``lang``."""
    verdicts = [verdict for _, verdict in runs]
    outcomes = [verdict["verdict"] for verdict in verdicts]
    if "blocked" in outcomes:
        merged = verdicts[outcomes.index("blocked")]
    elif "fail" in outcomes:
        merged = verdicts[outcomes.index("fail")]
    else:
        merged = verdicts[0]
    findings: list[dict[str, str]] = []
    for lang, verdict in runs:
        findings.extend({**finding, "lang": lang} for finding in verdict.get("findings", []))
    merged["findings"] = findings
    return merged


def _nightly_runners(
    profile: qa_regression.Profile, stack_mode: str
) -> dict[str, qa_worker.LayerRunner]:
    return {
        "regression": lambda _timeout_seconds: [_profile_verdict(profile)],
        "api": lambda _timeout_seconds: _api_verdicts(stack_mode),
        "ui": lambda _timeout_seconds: _run_all(
            qa_runner.load_scenarios(), stack_mode
        ),
    }


def _api_verdicts(stack_mode: str) -> list[dict[str, Any]]:
    routes = qa_api.load_routes(API_ROUTES_PATH)
    stack = qa_stack.stack_up(mode=stack_mode)
    try:
        return qa_api.check_routes(_HttpApiClient(stack.dashboard_url), routes)
    finally:
        qa_stack.stack_down(stack)


def _run_all(scenarios: list[qa_runner.Scenario], stack_mode: str) -> list[dict[str, Any]]:
    return [
        qa_runner.run_browser_scenario(
            scenario, max_minutes=qa_runner.DEFAULT_MAX_MINUTES, stack_mode=stack_mode
        )
        for scenario in scenarios
    ]


def _regression(args: argparse.Namespace) -> int:
    profile = qa_regression.find_profile(qa_regression.load_profiles(PROFILES_PATH), args.workdir)
    if profile is None:
        verdict = qa_regression.regression_verdict(has_profile=False, found={}, baseline={})
    else:
        verdict = _profile_verdict(profile)
    print(json.dumps(verdict, indent=2))
    return 0 if verdict["verdict"] == "pass" else 1


def _profile_verdict(profile: qa_regression.Profile) -> dict[str, Any]:
    working_directory = qa_worker.container_path(profile.working_directory)
    try:
        found = _run_commands(profile, working_directory)
    except RegressionStopped as stopped:
        return qa_regression.regression_verdict(
            has_profile=True,
            found={},
            baseline={},
            blocked_reason=None if stopped.timed_out else stopped.reason,
            timed_out=stopped.timed_out,
        )
    baseline = qa_regression.read_baseline(Path(working_directory) / profile.baseline)
    return qa_regression.regression_verdict(has_profile=True, found=found, baseline=baseline)


def _baseline(args: argparse.Namespace) -> int:
    profile = _named_profile(args.project)
    working_directory = qa_worker.container_path(profile.working_directory)
    try:
        found = _run_commands(profile, working_directory)
    except RegressionStopped as stopped:
        raise ValueError(f"baseline not written: {stopped.reason}") from stopped
    path = Path(working_directory) / profile.baseline
    qa_regression.write_baseline(path, found)
    for tool, keys in found.items():
        print(f"{tool}: {len(keys)} failures")
    print(f"baseline: {path}")
    return 0


def _named_profile(name: str) -> qa_regression.Profile:
    for profile in qa_regression.load_profiles(PROFILES_PATH):
        if profile.name == name:
            return profile
    raise ValueError(f"no QA profile named {name!r} in {PROFILES_PATH.name}")


def _run_commands(
    profile: qa_regression.Profile, working_directory: str
) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for command in profile.regression:
        outcome = qa_worker.run_command(
            command.command,
            working_directory,
            timeout_seconds=qa_worker.RUN_TIMEOUT_SECONDS,
            runs_dir=REGRESSION_RUNS_DIR,
        )
        if outcome.missing_command:
            raise RegressionStopped(f"regression command not found: {command.name}")
        if outcome.timed_out:
            raise RegressionStopped(
                f"regression command timed out: {command.name}", timed_out=True
            )
        found[command.name] = _failures(command.name, outcome.output, working_directory)
    return found


def _failures(tool: str, output: str, project_root: str) -> list[str]:
    if tool == "node":
        return qa_regression.parse_node(output, project_root)
    parsers = {
        "pytest": qa_regression.parse_pytest,
        "ruff": qa_regression.parse_ruff,
        "mypy": qa_regression.parse_mypy,
    }
    if tool not in parsers:
        raise ValueError(f"no failure parser for regression tool {tool!r}")
    return parsers[tool](output)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
