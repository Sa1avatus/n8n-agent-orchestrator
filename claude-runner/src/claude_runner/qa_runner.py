"""QA runner: scenario parsing, verdict building and the Playwright executor.

``parse_scenario`` and ``build_verdict`` are pure. ``run_browser_scenario`` imports Playwright
(the ``qa`` extra) only when it runs, so the module imports and the pure parts work without it.
"""

from __future__ import annotations

import json
import logging
import re
import secrets
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from . import qa_stack
from .board_ops_redact import scrub

TAGS = frozenset({"ui", "queue", "settings", "i18n", "nightly"})
ACTIONS = frozenset(
    {"open", "click", "drag", "type", "press", "wait_for_text", "switch_language", "api_post"}
)
EXPECTATIONS = frozenset(
    {
        "text_visible",
        "order",
        "placeholder_visible",
        "request_2xx",
        "no_console_error",
        "no_mutation_without_header",
    }
)
LANGUAGES = frozenset({"en", "ru"})
TOP_LEVEL_KEYS = frozenset(
    {
        "name", "tags", "preconditions", "steps", "reload_persistence",
        "allow_http_status", "freeze_refresh",
    }
)
# a scenario that provokes a conflict expects the browser to log the 409
ALLOWED_HTTP_STATUSES = frozenset({409})
FIXTURE_NAME = re.compile(r"[a-z0-9][a-z0-9_-]*")
DEFAULT_MAX_MINUTES = 20.0  # shortcut: roles max_minutes is not read by the DB-free CLI
SECONDS_PER_MINUTE = 60
STEP_TIMEOUT_MS = 15_000
FREEZE_TIMERS_SCRIPT = "window.setInterval = () => 0;"
VISIBLE_ONLY = "visible=true"  # a text also sits in hidden copies of a panel: skip those
MS_PER_SECOND = 1000
RESPONSE_POLL_MS = 100
REASON_MAX_CHARS = 300
RUN_ID_BYTES = 4
SERVER_ERROR_MIN = 500
DASHBOARD_HEADER = "X-AHAWR-Dashboard"
DASHBOARD_HEADER_VALUE = "1"
MUTATING_METHODS = frozenset({"POST", "PUT", "DELETE"})
API_PREFIX = "/api/"
CARD_ANCESTOR = "xpath=ancestor::div[contains(@class,'kcard')][1]"
BELOW_DROP_RATIO = 0.85
SCENARIO_DIR = qa_stack.CLAUDE_RUNNER_DIR / "qa" / "scenarios"
RUNS_DIR = qa_stack.QA_RUNS_DIR
RUN_DIR_PREFIX = "qa-"
PLAYWRIGHT_HINT = (
    "Playwright is not available: install the qa extra with pip install -e '.[qa]', "
    "then run python -m playwright install chromium"
)
log = logging.getLogger("claude_runner.qa_runner")


class ScenarioError(ValueError):
    """A scenario is invalid; the message names the field that is wrong."""


@dataclass(frozen=True)
class Expect:
    text_visible: tuple[str, ...] = ()
    order: tuple[str, ...] = ()
    placeholder_visible: tuple[str, ...] = ()
    request_2xx: tuple[str, ...] = ()
    no_console_error: bool = False
    no_mutation_without_header: bool = False


@dataclass(frozen=True)
class Step:
    action: str
    # A str for most actions; (from, onto) for drag and (into, text) for type.
    value: str | tuple[str, str]
    expect: Expect
    below: bool = False  # drag only: drop in the lower part of the target card
    replace: bool = False  # type only: clear + retype (otherwise press_sequentially appends)
    body: dict[str, Any] = field(default_factory=dict)  # api_post only


@dataclass(frozen=True)
class Scenario:
    name: str
    tags: tuple[str, ...]
    fixture: str
    steps: tuple[Step, ...]
    reload_persistence: bool = False
    allow_http_status: tuple[int, ...] = ()
    freeze_refresh: bool = False  # no page timers: the board cannot refresh its versions on its own


def parse_scenario(text: str) -> Scenario:
    import yaml  # the 'qa' extra; kept out of module import so the pure parts work without it

    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ScenarioError(f"invalid YAML: {exc}") from exc
    root = _mapping(data, "scenario", TOP_LEVEL_KEYS)
    name = _text(root.get("name"), "name")
    tags = _tags(root.get("tags"))
    fixture = _fixture(root.get("preconditions"))
    steps = _steps(root.get("steps"))
    reload_persistence = _bool(root.get("reload_persistence", False), "reload_persistence")
    allow_http_status = _statuses(root.get("allow_http_status", []))
    freeze_refresh = _bool(root.get("freeze_refresh", False), "freeze_refresh")
    return Scenario(
        name, tags, fixture, steps, reload_persistence, allow_http_status, freeze_refresh
    )


def _statuses(value: object) -> tuple[int, ...]:
    if not isinstance(value, list) or not all(
        isinstance(code, int) and code in ALLOWED_HTTP_STATUSES for code in value
    ):
        allowed = ", ".join(str(code) for code in sorted(ALLOWED_HTTP_STATUSES))
        raise ScenarioError(f"allow_http_status must be a list of: {allowed}")
    return tuple(value)


def load_scenario(path: Path) -> Scenario:
    try:
        return parse_scenario(path.read_text(encoding="utf-8"))
    except ScenarioError as exc:
        raise ScenarioError(f"{path.name}: {exc}") from exc


def build_verdict(
    *,
    scenario: str,
    blocked_reason: str | None = None,
    failed_step: int | None = None,
    failed_reason: str = "",
    screenshots: Sequence[str] = (),
    console_errors: Sequence[str] = (),
    network_errors: Sequence[str] = (),
    minutes: float = 0.0,
    cost_usd: float = 0.0,
) -> dict[str, Any]:
    if blocked_reason is not None:
        verdict = "blocked"
    elif failed_step is not None or console_errors or network_errors:
        verdict = "fail"
    else:
        verdict = "pass"
    return {
        "scenario": scrub(scenario),
        "verdict": verdict,
        "blocked_reason": None if blocked_reason is None else scrub(blocked_reason),
        "failed_step": failed_step,
        "failed_reason": scrub(failed_reason),
        "screenshots": [scrub(path) for path in screenshots],
        "console_errors": [scrub(line) for line in console_errors],
        "network_errors": [scrub(line) for line in network_errors],
        "minutes": minutes,
        "cost_usd": cost_usd,
    }


def _mapping(value: object, field: str, allowed: frozenset[str]) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise ScenarioError(f"{field}: expected a mapping, got {type(value).__name__}")
    unknown = sorted(str(key) for key in value if key not in allowed)
    if unknown:
        raise ScenarioError(f"{field}: unknown field(s) {', '.join(unknown)}")
    return value


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ScenarioError(f"{field}: required non-empty string")
    return value


def _bool(value: object, field: str) -> bool:
    if not isinstance(value, bool):
        raise ScenarioError(f"{field}: expected true or false")
    return value


def _strings(value: object, field: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise ScenarioError(f"{field}: expected a non-empty list of strings")
    return tuple(_text(item, f"{field}[{index}]") for index, item in enumerate(value))


def _tags(value: object) -> tuple[str, ...]:
    tags = _strings(value, "tags")
    for index, tag in enumerate(tags):
        if tag not in TAGS:
            raise ScenarioError(f"tags[{index}]: unknown tag {tag!r}")
    return tags


def _fixture(value: object) -> str:
    preconditions = _mapping(value, "preconditions", frozenset({"fixture"}))
    fixture = _text(preconditions.get("fixture"), "preconditions.fixture")
    if not FIXTURE_NAME.fullmatch(fixture):
        raise ScenarioError(f"preconditions.fixture: {fixture!r} is not a fixture name")
    return fixture


def _steps(value: object) -> tuple[Step, ...]:
    if not isinstance(value, list) or not value:
        raise ScenarioError("steps: expected a non-empty list")
    return tuple(_step(item, f"steps[{index}]") for index, item in enumerate(value))


def _step(value: object, field: str) -> Step:
    body = _mapping(value, field, ACTIONS | {"expect"})
    actions = [key for key in body if key != "expect"]
    if len(actions) != 1:
        raise ScenarioError(f"{field}: exactly one action is required, got {len(actions)}")
    action = actions[0]
    expect = _expect(body.get("expect"), f"{field}.expect")
    detail = f"{field}.{action}"
    if action == "drag":
        return _drag_step(body[action], detail, expect)
    if action == "api_post":
        return _api_post_step(body[action], detail, expect)
    if action == "type":
        return _type_step(body[action], detail, expect)
    return Step(action, _action_value(action, body[action], detail), expect)


def _drag_step(value: object, field: str, expect: Expect) -> Step:
    body = _mapping(value, field, frozenset({"from", "onto", "below"}))
    pair = (_text(body.get("from"), f"{field}.from"), _text(body.get("onto"), f"{field}.onto"))
    below = _bool(body.get("below", False), f"{field}.below")
    return Step("drag", pair, expect, below=below)


def _api_post_step(value: object, field: str, expect: Expect) -> Step:
    body = _mapping(value, field, frozenset({"path", "body"}))
    path = _text(body.get("path"), f"{field}.path")
    if not path.startswith(API_PREFIX) or "://" in path:
        raise ScenarioError(f"{field}.path: {path!r} must be a path under {API_PREFIX}")
    payload = body.get("body", {})
    if not isinstance(payload, dict):
        raise ScenarioError(f"{field}.body: expected a mapping, got {type(payload).__name__}")
    return Step("api_post", path, expect, body=payload)


def _action_value(action: str, value: object, field: str) -> str | tuple[str, str]:
    if action == "type":
        body = _mapping(value, field, frozenset({"into", "text"}))
        return (_text(body.get("into"), f"{field}.into"), _text(body.get("text"), f"{field}.text"))
    if action == "switch_language":
        language = _text(value, field)
        if language not in LANGUAGES:
            raise ScenarioError(f"{field}: unknown language {language!r}")
        return language
    return _text(value, field)


def _type_step(value: object, field: str, expect: Expect) -> Step:
    body = _mapping(value, field, frozenset({"into", "text", "replace"}))
    into = _text(body.get("into"), f"{field}.into")
    text = _text(body.get("text"), f"{field}.text")
    replace = _bool(body.get("replace", False), f"{field}.replace")
    return Step("type", (into, text), expect, replace=replace)


def _expect(value: object, field: str) -> Expect:
    if value is None:
        return Expect()
    body = _mapping(value, field, EXPECTATIONS)
    return Expect(
        text_visible=_optional_strings(body, "text_visible", field),
        order=_optional_strings(body, "order", field),
        placeholder_visible=_placeholders(body, field),
        request_2xx=_optional_strings(body, "request_2xx", field),
        no_console_error=_bool(body.get("no_console_error", False), f"{field}.no_console_error"),
        no_mutation_without_header=_bool(
            body.get("no_mutation_without_header", False), f"{field}.no_mutation_without_header"
        ),
    )


def _optional_strings(body: Mapping[str, Any], key: str, field: str) -> tuple[str, ...]:
    if key not in body:
        return ()
    return _strings(body[key], f"{field}.{key}")


def _placeholders(body: Mapping[str, Any], field: str) -> tuple[str, ...]:
    values = _optional_strings(body, "placeholder_visible", field)
    for value in values:
        if '"' in value:
            raise ScenarioError(f"{field}.placeholder_visible: {value!r} has a double quote")
    return values


class StepFailed(RuntimeError):
    """An action or expectation of one step did not hold."""


@dataclass
class _Recorder:
    console_errors: list[str] = field(default_factory=list)
    network_errors: list[str] = field(default_factory=list)
    responses: list[tuple[str, int]] = field(default_factory=list)
    allowed_statuses: tuple[int, ...] = ()


@dataclass(frozen=True)
class _Mark:
    console: int
    network: int
    responses: int


@dataclass
class _Progress:
    failed_step: int | None = None
    failed_reason: str = ""
    blocked_reason: str | None = None
    screenshots: list[str] = field(default_factory=list)


def load_scenarios(directory: Path = SCENARIO_DIR) -> list[Scenario]:
    paths = sorted(directory.glob("*.yaml")) if directory.is_dir() else []
    if not paths:
        raise ValueError("no scenario files (*.yaml) in qa/scenarios/")
    return [load_scenario(path) for path in paths]


def select_scenarios(
    scenarios: Sequence[Scenario], *, tag: str | None = None, name: str | None = None
) -> list[Scenario]:
    selected = [
        scenario
        for scenario in scenarios
        if (tag is None or tag in scenario.tags) and (name is None or scenario.name == name)
    ]
    if not selected:
        raise ValueError("no scenario matches the --tag and --scenario filters")
    return selected


def run_browser_scenario(
    scenario: Scenario,
    *,
    max_minutes: float = DEFAULT_MAX_MINUTES,
    runs_dir: Path = RUNS_DIR,
    stack_mode: str = "docker",
) -> dict[str, Any]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return build_verdict(scenario=scenario.name, blocked_reason=PLAYWRIGHT_HINT)
    started = time.monotonic()
    run_dir = _new_run_dir(runs_dir)
    try:
        stack = qa_stack.stack_up(scenario.fixture, mode=stack_mode)
    except (qa_stack.QaStackError, ValueError) as error:
        progress = _Progress(blocked_reason=f"QA stack did not start: {_reason(error)}")
        return _settle(scenario, run_dir, started, progress, _Recorder())
    print(f"stack id: {stack.run_id}", file=sys.stderr, flush=True)
    deadline = started + max_minutes * SECONDS_PER_MINUTE
    try:
        progress, recorder = _drive(scenario, stack, sync_playwright, run_dir, deadline)
    finally:
        _tear_down(stack)
    return _settle(scenario, run_dir, started, progress, recorder)


def _new_run_dir(runs_dir: Path) -> Path:
    run_dir = runs_dir / f"{RUN_DIR_PREFIX}{secrets.token_hex(RUN_ID_BYTES)}"
    run_dir.mkdir(parents=True)
    return run_dir


def _tear_down(stack: qa_stack.QaStack) -> None:
    try:
        qa_stack.stack_down(stack)
    except qa_stack.QaStackError as error:
        log.error("QA stack %s teardown incomplete: %s", stack.run_id, error)


def _drive(
    scenario: Scenario,
    stack: qa_stack.QaStack,
    sync_playwright: Callable[[], Any],
    run_dir: Path,
    deadline: float,
) -> tuple[_Progress, _Recorder]:
    recorder = _Recorder(allowed_statuses=scenario.allow_http_status)
    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(headless=True)
        except Exception as error:
            return _Progress(blocked_reason=f"{PLAYWRIGHT_HINT} ({_reason(error)})"), recorder
        try:
            page = browser.new_page()
            if scenario.freeze_refresh:
                page.add_init_script(FREEZE_TIMERS_SCRIPT)
            _attach(page, stack.dashboard_url, recorder)
            progress = _execute(scenario, page, recorder, stack.dashboard_url, run_dir, deadline)
        finally:
            browser.close()
    return progress, recorder


def _execute(
    scenario: Scenario,
    page: Any,
    recorder: _Recorder,
    base_url: str,
    run_dir: Path,
    deadline: float,
) -> _Progress:
    progress = _Progress()
    for index, step in enumerate(scenario.steps):
        if time.monotonic() > deadline:
            progress.blocked_reason = f"max_minutes exceeded before step {index}"
            return progress
        reason = _run_step(page, step, recorder, base_url)
        if reason is not None:
            progress.failed_step, progress.failed_reason = index, reason
            _keep_screenshot(page, run_dir, f"step-{index}.png", progress)
            break
    else:
        if scenario.reload_persistence:
            reason = _recheck_after_reload(page, scenario, recorder)
            if reason is not None:
                progress.failed_step, progress.failed_reason = len(scenario.steps), reason
    _keep_screenshot(page, run_dir, "final.png", progress)
    return progress


def _run_step(page: Any, step: Step, recorder: _Recorder, base_url: str) -> str | None:
    mark = _mark(recorder)
    return _attempt(partial(_perform, page, step, base_url)) or _attempt(
        partial(_check, page, step.expect, recorder, mark)
    )


def _recheck_after_reload(page: Any, scenario: Scenario, recorder: _Recorder) -> str | None:
    reason = _attempt(partial(_reload, page))
    if reason is not None:
        return reason
    final = next(
        (
            step.expect
            for step in reversed(scenario.steps)
            if step.expect.text_visible or step.expect.order
        ),
        None,
    )
    if final is None:
        return None
    expect = Expect(text_visible=final.text_visible, order=final.order)
    return _attempt(partial(_check, page, expect, recorder, _mark(recorder)))


def _reload(page: Any) -> None:
    page.reload()
    page.wait_for_load_state("load", timeout=STEP_TIMEOUT_MS)


def _attempt(action: Callable[[], None]) -> str | None:
    try:
        action()
    except Exception as error:  # Playwright raises its own error types; they fail the step
        return _reason(error)
    return None


def _reason(error: BaseException) -> str:
    return " ".join(str(error).split())[:REASON_MAX_CHARS] or type(error).__name__


def _mark(recorder: _Recorder) -> _Mark:
    return _Mark(
        len(recorder.console_errors), len(recorder.network_errors), len(recorder.responses)
    )


def _attach(page: Any, base_url: str, recorder: _Recorder) -> None:
    page.on("dialog", _accept_dialog)
    page.on("console", partial(_record_console, recorder))
    page.on("request", partial(_check_header, base_url, recorder))
    page.on("response", partial(_record_response, base_url, recorder))


def _accept_dialog(dialog: Any) -> None:
    dialog.accept()


def _record_console(recorder: _Recorder, message: Any) -> None:
    if message.type != "error":
        return
    if any(f"status of {code}" in message.text for code in recorder.allowed_statuses):
        return
    recorder.console_errors.append(message.text)


def _check_header(base_url: str, recorder: _Recorder, request: Any) -> None:
    if request.method not in MUTATING_METHODS or not request.url.startswith(base_url):
        return
    if request.headers.get(DASHBOARD_HEADER.lower()) != DASHBOARD_HEADER_VALUE:
        path = urlsplit(request.url).path
        recorder.network_errors.append(f"{request.method} {path} without {DASHBOARD_HEADER} header")


def _record_response(base_url: str, recorder: _Recorder, response: Any) -> None:
    recorder.responses.append((response.url, response.status))
    if response.url.startswith(base_url) and response.status >= SERVER_ERROR_MIN:
        recorder.network_errors.append(f"{response.status} {urlsplit(response.url).path}")


def _perform(page: Any, step: Step, base_url: str) -> None:
    if step.action == "open":
        page.goto(base_url + _as_text(step.value))
    elif step.action == "click":
        _locate(page, _as_text(step.value)).click()
    elif step.action == "drag":
        source, target = _as_pair(step.value)
        _drag(page, source, target, step.below)
    elif step.action == "api_post":
        _post_api(page, base_url, step)
    elif step.action == "type":
        field_name, text = _as_pair(step.value)
        by_name = page.locator(f'[name="{field_name}"]')
        field = page.get_by_role("textbox", name=field_name).or_(by_name)
        field = field.locator(VISIBLE_ONLY).first
        if step.replace:
            field.fill("")
            field.press_sequentially(text)
        else:
            field.press_sequentially(text)
    elif step.action == "press":
        page.keyboard.press(_as_text(step.value))
    elif step.action == "wait_for_text":
        _locate(page, _as_text(step.value)).wait_for(state="visible", timeout=STEP_TIMEOUT_MS)
    else:
        code = _as_text(step.value)
        page.locator(f'select:has(option[value="{code}"])').first.select_option(code)


def _drag(page: Any, source: str, target: str, below: bool) -> None:
    if not below:
        _locate(page, source).drag_to(_locate(page, target))
        return
    card = _locate(page, target).locator(CARD_ANCESTOR)
    box = card.bounding_box()
    if box is None:
        raise StepFailed(f"{target!r} has no position on the page")
    position = {"x": float(box["width"]) / 2, "y": float(box["height"]) * BELOW_DROP_RATIO}
    _locate(page, source).drag_to(card, target_position=position)


def _post_api(page: Any, base_url: str, step: Step) -> None:
    path = _as_text(step.value)
    response = page.request.post(
        base_url + path,
        headers={DASHBOARD_HEADER: DASHBOARD_HEADER_VALUE, "Content-Type": "application/json"},
        data=json.dumps(step.body),
    )
    if not response.ok:
        raise StepFailed(f"{path} answered {response.status}")


def _as_text(value: str | tuple[str, str]) -> str:
    if not isinstance(value, str):
        raise StepFailed("step value is not text")
    return value


def _as_pair(value: str | tuple[str, str]) -> tuple[str, str]:
    if not isinstance(value, tuple):
        raise StepFailed("step value is not a pair")
    return value


def _locate(page: Any, text: str) -> Any:
    return page.get_by_text(text, exact=True).locator(VISIBLE_ONLY).first


def _check(page: Any, expect: Expect, recorder: _Recorder, mark: _Mark) -> None:
    for text in expect.text_visible:
        page.get_by_text(text).locator(VISIBLE_ONLY).first.wait_for(
            state="visible", timeout=STEP_TIMEOUT_MS
        )
    if expect.order:
        _check_order(page, expect.order)
    for value in expect.placeholder_visible:
        _check_placeholder(page, value)
    for path in expect.request_2xx:
        _check_request_2xx(page, path, recorder, mark)
    if expect.no_console_error and len(recorder.console_errors) > mark.console:
        raise StepFailed(f"console error: {recorder.console_errors[mark.console]}")
    if expect.no_mutation_without_header and len(recorder.network_errors) > mark.network:
        raise StepFailed(recorder.network_errors[mark.network])


def _check_order(page: Any, texts: tuple[str, ...]) -> None:
    """The board redraws after a drag or a reload, so the order is polled until the step timeout."""
    deadline = time.monotonic() + STEP_TIMEOUT_MS / MS_PER_SECOND
    while True:
        try:
            _check_order_once(page, texts)
            return
        except StepFailed:
            if time.monotonic() >= deadline:
                raise
            page.wait_for_timeout(RESPONSE_POLL_MS)


def _check_order_once(page: Any, texts: tuple[str, ...]) -> None:
    tops: list[float] = []
    for text in texts:
        box = _locate(page, text).bounding_box()
        if box is None:
            raise StepFailed(f"{text!r} has no position on the page")
        tops.append(float(box["y"]))
    if any(upper >= lower for upper, lower in zip(tops, tops[1:], strict=False)):
        raise StepFailed(f"order is not {' then '.join(texts)}")


def _check_placeholder(page: Any, value: str) -> None:
    if page.locator(_placeholder_selector(value)).count() == 0:
        raise StepFailed(f"no input or textarea with placeholder {value!r}")


def _placeholder_selector(value: str) -> str:
    return f'input[placeholder="{value}"], textarea[placeholder="{value}"]'


def _check_request_2xx(page: Any, path: str, recorder: _Recorder, mark: _Mark) -> None:
    if any(_is_2xx(url, status, path) for url, status in recorder.responses[mark.responses :]):
        return
    deadline = time.monotonic() + STEP_TIMEOUT_MS / MS_PER_SECOND
    while time.monotonic() < deadline:
        page.wait_for_timeout(RESPONSE_POLL_MS)
        if any(_is_2xx(url, status, path) for url, status in recorder.responses[mark.responses :]):
            return
    raise StepFailed(f"no 2xx response for {path} within {STEP_TIMEOUT_MS / MS_PER_SECOND:g} s")


def _is_2xx(url: str, status: int, path: str) -> bool:
    return path in url and 200 <= status < 300


def _keep_screenshot(page: Any, run_dir: Path, name: str, progress: _Progress) -> None:
    try:
        page.screenshot(path=str(run_dir / name))
    except Exception as error:
        log.warning("screenshot %s was not saved: %s", name, _reason(error))
        return
    progress.screenshots.append(f"{run_dir.name}/{name}")


def _settle(
    scenario: Scenario,
    run_dir: Path,
    started: float,
    progress: _Progress,
    recorder: _Recorder,
) -> dict[str, Any]:
    verdict = build_verdict(
        scenario=scenario.name,
        blocked_reason=progress.blocked_reason,
        failed_step=progress.failed_step,
        failed_reason=progress.failed_reason,
        screenshots=progress.screenshots,
        console_errors=recorder.console_errors,
        network_errors=recorder.network_errors,
        minutes=round((time.monotonic() - started) / SECONDS_PER_MINUTE, 2),
    )
    (run_dir / "verdict.json").write_text(json.dumps(verdict, indent=2), encoding="utf-8")
    return verdict
