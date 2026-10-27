"""Seeds, bounds, host guard and crawl of the QA smoke; the detectors are in qa_smoke_detect.py."""

from __future__ import annotations

import json
import logging
import math
import time
import urllib.request
from collections.abc import Collection, Mapping
from dataclasses import dataclass, field, replace
from functools import partial
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from . import qa_stack
from .board_ops_redact import scrub
from .qa_runner import (
    ALLOWED_HTTP_STATUSES,
    PLAYWRIGHT_HINT,
    RUNS_DIR,
    SECONDS_PER_MINUTE,
    STEP_TIMEOUT_MS,
    ScenarioError,
    _attach,
    _attempt,
    _mapping,
    _new_run_dir,
    _reason,
    _Recorder,
    _strings,
    _text,
    build_verdict,
)
from .qa_smoke_detect import (
    ERROR,
    WARNING,
    ConsoleMessage,
    DomElement,
    FailedRequest,
    Finding,
    HttpRequest,
    HttpResponse,
    Where,
    detect_bad_text,
    detect_client_errors,
    detect_console_errors,
    detect_failed_assets,
    detect_missing_header,
    detect_no_effect,
    detect_null_ids,
    detect_pageerrors,
)

log = logging.getLogger("claude_runner.qa_smoke")
MAX_PAGES = 40
MISSION_PAGES = 3
MISSION_LIST_TIMEOUT_SECONDS = 5
MAX_CLICKS_PER_PAGE = 25
MAX_MINUTES = 10
SEEDS_PATH = qa_stack.CLAUDE_RUNNER_DIR / "qa" / "smoke_seeds.yaml"
LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost"})
QA_HOST_PREFIX = "qa-"
SEEDS_KEYS = frozenset({"start_paths", "mission_paths", "products", "destructive"})
MISSION_KEYS = frozenset({"source", "list_key", "id_field", "template"})
INTERACTIVE_SELECTOR = "button, a[href], [role=tab], select, input"
CLOSE_SELECTOR = "[aria-label=Close]"
SMOKE_SCENARIO = "smoke"


class SeedsError(ValueError):
    """The seeds file is invalid; the message names the field that is wrong."""


class HostRefusedError(ValueError):
    """The crawl target is not a local host; the message names the host."""


@dataclass(frozen=True)
class MissionPaths:
    source: str
    list_key: str
    id_field: str
    template: str


@dataclass(frozen=True)
class Seeds:
    start_paths: tuple[str, ...]
    mission_paths: MissionPaths
    products: Mapping[str, tuple[str, ...]]
    destructive: tuple[str, ...]


@dataclass(frozen=True)
class Bounds:
    max_pages: int = MAX_PAGES
    max_clicks_per_page: int = MAX_CLICKS_PER_PAGE
    deadline: float = math.inf  # time.monotonic() value at which the crawl must stop


@dataclass(frozen=True)
class _Control:
    label: str
    href: str
    visible: bool
    disabled: bool


@dataclass
class _Seen:
    console: int = 0
    responses: int = 0
    pageerrors: int = 0
    requests: int = 0
    failures: int = 0
    # validation answers whose console line has not been seen yet (it can land in a later step)
    answers: int = 0
    # urls of the writes seen so far: the answer can arrive in a later step than the request
    write_urls: set[str] = field(default_factory=set)


@dataclass
class Crawl:
    seeds: Seeds
    base_url: str
    run_dir: Path
    bounds: Bounds
    page: Any
    recorder: _Recorder = field(default_factory=_Recorder)
    pageerrors: list[str] = field(default_factory=list)
    requests: list[HttpRequest] = field(default_factory=list)
    failures: list[FailedRequest] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    reported: set[tuple[str, str, str]] = field(default_factory=set)
    screenshots: list[str] = field(default_factory=list)
    skipped: list[dict[str, str]] = field(default_factory=list)
    visited: set[str] = field(default_factory=set)
    seen: _Seen = field(default_factory=_Seen)
    pages: int = 0
    clicks: int = 0
    bound_reached: str | None = None
    blocked_reason: str | None = None


def load_seeds(path: Path = SEEDS_PATH) -> Seeds:
    return parse_seeds(path.read_text(encoding="utf-8"))


def parse_seeds(text: str) -> Seeds:
    import yaml  # the 'qa' extra, read the same way as qa_runner.parse_scenario

    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise SeedsError(f"invalid YAML: {exc}") from exc
    try:
        root = _mapping(data, "seeds", SEEDS_KEYS)
        return Seeds(
            start_paths=_paths(root.get("start_paths"), "start_paths"),
            mission_paths=_mission_paths(root.get("mission_paths")),
            products=_products(root.get("products")),
            destructive=_strings(root.get("destructive"), "destructive"),
        )
    except ScenarioError as exc:
        raise SeedsError(str(exc)) from exc


def _paths(value: object, field: str) -> tuple[str, ...]:
    paths = _strings(value, field)
    for index, path in enumerate(paths):
        if not path.startswith("/") or path.startswith("//"):
            raise ScenarioError(f"{field}[{index}]: {path!r} is not a path on this host")
    return paths


def _mission_paths(value: object) -> MissionPaths:
    fields = _mapping(value, "mission_paths", MISSION_KEYS)
    id_field = _text(fields.get("id_field"), "mission_paths.id_field")
    template = _text(fields.get("template"), "mission_paths.template")
    if "{" + id_field + "}" not in template:
        raise ScenarioError(f"mission_paths.template: must contain {{{id_field}}}")
    return MissionPaths(
        source=_text(fields.get("source"), "mission_paths.source"),
        list_key=_text(fields.get("list_key"), "mission_paths.list_key"),
        id_field=id_field,
        template=template,
    )


def _products(value: object) -> dict[str, tuple[str, ...]]:
    if not isinstance(value, dict) or not value:
        raise ScenarioError("products: expected a mapping of product name to paths")
    return {str(name): _paths(paths, f"products.{name}") for name, paths in value.items()}


def check_host(url: str, allow_hosts: Collection[str] = ()) -> None:
    host = urlsplit(url).hostname or ""
    if host in LOCAL_HOSTS or _is_qa_container(host):
        return
    if host in {name.lower() for name in allow_hosts}:
        return
    raise HostRefusedError(
        f"host {host!r} is not local: the crawl runs only against 127.0.0.1, localhost "
        "or a qa- container; name it with --allow-host to run against it"
    )


def _is_qa_container(host: str) -> bool:
    return host.startswith(QA_HOST_PREFIX) and "." not in host


def _set_language(page: Any, lang: str) -> None:
    """Open the board in the named language through the board's language switch
    (localStorage ``board.lang``, ``BoardI18n.setLanguage``)."""
    code = json.dumps(lang)
    page.evaluate(f"globalThis.BoardI18n.setLanguage({code})")
    page.reload(wait_until="networkidle")


def destructive_match(label: str, seeds: Seeds) -> str | None:
    folded = label.casefold()
    return next((word for word in seeds.destructive if word.casefold() in folded), None)


def run_smoke(
    seeds: Seeds,
    *,
    base_url: str,
    max_pages: int = MAX_PAGES,
    max_clicks_per_page: int = MAX_CLICKS_PER_PAGE,
    max_minutes: float = MAX_MINUTES,
    runs_dir: Path = RUNS_DIR,
    allow_hosts: Collection[str] = (),
    lang: str = "en",
) -> dict[str, Any]:
    started = time.monotonic()
    try:
        check_host(base_url, allow_hosts)
    except HostRefusedError as error:
        return _blocked_verdict(str(error), started)
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return _blocked_verdict(PLAYWRIGHT_HINT, started)
    run_dir = _new_run_dir(runs_dir)
    bounds = Bounds(max_pages, max_clicks_per_page, started + max_minutes * SECONDS_PER_MINUTE)
    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(headless=True)
        except Exception as error:  # Playwright raises its own error types; the crawl is blocked
            return _blocked_verdict(f"{PLAYWRIGHT_HINT} ({_reason(error)})", started)
        page = browser.new_page()
        try:
            _goto_settled(page, base_url + "/static/board.html#board")
            _set_language(page, lang)
            state = crawl(page, seeds, base_url, run_dir, bounds)
        finally:
            browser.close()
    verdict = build_smoke_verdict(state, started)
    (run_dir / "verdict.json").write_text(json.dumps(verdict, indent=2), encoding="utf-8")
    return verdict


def crawl(page: Any, seeds: Seeds, base_url: str, run_dir: Path, bounds: Bounds) -> Crawl:
    state = Crawl(seeds, base_url, run_dir, bounds, page)
    _attach_events(state, page)
    start_urls = [base_url + path for path in seeds.start_paths]
    for url in [*start_urls, *_mission_urls(seeds, base_url)]:
        bound = _page_bound(state)
        if bound is not None:
            state.bound_reached = bound
            break
        if state.blocked_reason is not None:
            break
        if url not in state.visited:
            state.visited.add(url)
            _visit(state, url)
    return state


def _mission_urls(seeds: Seeds, base_url: str) -> list[str]:
    """Pages of the first MISSION_PAGES missions, from the list the seeds name; none when the
    list cannot be read (the crawl then covers the start paths only)."""
    paths = seeds.mission_paths
    try:
        url = base_url + paths.source
        with urllib.request.urlopen(url, timeout=MISSION_LIST_TIMEOUT_SECONDS) as reply:
            rows = json.loads(reply.read()).get(paths.list_key, [])
    except (OSError, ValueError) as error:
        log.warning("smoke: mission list not read: %s", error)
        return []
    field = paths.id_field
    ids = [str(row[field]) for row in rows if isinstance(row, dict) and row.get(field)]
    template = paths.template
    return [
        base_url + template.replace("{" + paths.id_field + "}", mission)
        for mission in ids[:MISSION_PAGES]
    ]


def _attach_events(crawl: Crawl, page: Any) -> None:
    _attach(page, crawl.base_url, crawl.recorder)
    page.on("pageerror", partial(_record_pageerror, crawl))
    page.on("request", partial(_record_request, crawl))
    page.on("requestfailed", partial(_record_failure, crawl))


def _record_pageerror(crawl: Crawl, error: Any) -> None:
    crawl.pageerrors.append(str(error))


def _record_request(crawl: Crawl, request: Any) -> None:
    crawl.requests.append(HttpRequest(request.method, request.url, request.headers))


def _record_failure(crawl: Crawl, request: Any) -> None:
    crawl.failures.append(FailedRequest(request.url, request.resource_type))


def _visit(crawl: Crawl, url: str) -> None:
    reason = _attempt(partial(_goto_settled, crawl.page, url))
    if reason is not None:
        if crawl.pages == 0:
            crawl.blocked_reason = f"the first page did not load: {reason}"
        else:
            _record_findings(crawl, [_problem(crawl, "page-load", url, reason)])
        return
    crawl.pages += 1
    _check(crawl, "page")
    _click_controls(crawl, url, _controls(crawl.page))


def _click_controls(crawl: Crawl, url: str, controls: list[_Control]) -> None:
    clicks_here = 0
    for index, control in enumerate(controls):
        if not control.visible:
            continue
        matched = destructive_match(f"{control.label} {control.href}", crawl.seeds)
        if matched is not None:
            crawl.skipped.append({"label": control.label, "matched": matched})
            continue
        bound = _click_bound(crawl, clicks_here)
        if bound is not None:
            crawl.bound_reached = bound
            return
        clicks_here += 1
        reason = _click_once(crawl, url, index, control)
        if reason is not None:
            _record_findings(crawl, [_problem(crawl, "page-load", url, reason)])
            return


def _click_once(crawl: Crawl, url: str, index: int, control: _Control) -> str | None:
    before = _dom_state(crawl.page)
    crawl.clicks += 1
    reason = _attempt(partial(_click, crawl.page, index))
    if reason is not None:
        _record_findings(crawl, [_click_problem(crawl, control.label, reason)])
    else:
        _check(crawl, control.label)
        after = _dom_state(crawl.page)
        _record_findings(
            crawl,
            detect_no_effect(before, after, control.disabled, _where(crawl, control.label)),
        )
    return _attempt(partial(_restore, crawl.page, url, before))


def _click(page: Any, index: int) -> None:
    page.locator(INTERACTIVE_SELECTOR).nth(index).click(timeout=CLICK_TIMEOUT_MS)


def _restore(page: Any, url: str, baseline: list[DomElement]) -> None:
    page.keyboard.press("Escape")
    if _dom_state(page) == baseline:
        return
    close = page.locator(CLOSE_SELECTOR)
    if close.count() > 0:
        close.first.click(timeout=STEP_TIMEOUT_MS)
        if _dom_state(page) == baseline:
            return
    _goto_settled(page, url)


def _goto_settled(page: Any, url: str) -> None:
    page.goto(url)
    page.wait_for_load_state("networkidle", timeout=STEP_TIMEOUT_MS)


# One call reads every control, so a page that re-renders while the crawl reads it cannot leave
# a handle that points at a control which is gone (the old per-handle reads waited 30 s for it).
CONTROLS_SCRIPT = """elements => elements.map((element) => {
  const box = element.getBoundingClientRect();
  const style = getComputedStyle(element);
  return {
    label: element.getAttribute('aria-label') || element.innerText || '',
    href: element.getAttribute('href') || '',
    visible: box.width > 0 && box.height > 0 && style.visibility !== 'hidden',
    disabled: element.hasAttribute('disabled'),
  };
})"""


def _controls(page: Any) -> list[_Control]:
    rows = page.locator(INTERACTIVE_SELECTOR).evaluate_all(CONTROLS_SCRIPT)
    return [
        _Control(
            label=" ".join(str(row["label"]).split()),
            href=str(row["href"]),
            visible=bool(row["visible"]),
            disabled=bool(row["disabled"]),
        )
        for row in rows
    ]


def _dom_state(page: Any) -> list[DomElement]:
    # shortcut: bad-text and no-effect see control labels only, not all page text;
    # upgrade to body text once the labels-only misses are measured
    return [
        DomElement(selector=control.label, text=control.label, visible=control.visible)
        for control in _controls(page)
    ]


def _check(crawl: Crawl, element: str) -> None:
    _record_findings(crawl, _new_findings(crawl, _where(crawl, element)))


# A write the crawl provoked (an empty form submitted) is answered 400 or 422 by the validation
# of the server: that is the expected answer, not a defect.
VALIDATION_STATUSES = frozenset({400, 422})
LOAD_FAILED_TEXT = "Failed to load resource"
COVERED_CLICK_TEXT = "intercepts pointer events"
CLICK_TIMEOUT_MS = 3000


def _without_validation_answers(
    responses: list[tuple[str, int]], write_urls: set[str], console: list[str], owed: int = 0
) -> tuple[list[tuple[str, int]], list[str], int]:
    """Drop validation answers to writes and their console lines; unmatched ones are owed on."""
    kept = [
        (url, status)
        for url, status in responses
        if not (status in VALIDATION_STATUSES and url in write_urls)
    ]
    owed += len(responses) - len(kept)
    texts: list[str] = []
    for text in console:
        is_answer = LOAD_FAILED_TEXT in text and any(
            str(code) in text for code in VALIDATION_STATUSES
        )
        if is_answer and owed > 0:
            owed -= 1
            continue
        texts.append(text)
    return kept, texts, owed


def _new_findings(crawl: Crawl, where: Where) -> list[Finding]:
    recorder, seen = crawl.recorder, crawl.seen
    console = recorder.console_errors[seen.console :]
    responses = recorder.responses[seen.responses :]
    pageerrors = crawl.pageerrors[seen.pageerrors :]
    requests = crawl.requests[seen.requests :]
    failures = crawl.failures[seen.failures :]
    seen.console = len(recorder.console_errors)
    seen.responses = len(recorder.responses)
    seen.pageerrors = len(crawl.pageerrors)
    seen.requests = len(crawl.requests)
    seen.failures = len(crawl.failures)
    seen.write_urls |= {request.url for request in requests if request.method != "GET"}
    responses, console, seen.answers = _without_validation_answers(
        list(responses), seen.write_urls, list(console), seen.answers
    )
    return [
        *detect_console_errors([ConsoleMessage("error", text) for text in console], where),
        *detect_pageerrors(pageerrors, where),
        *detect_client_errors(
            [HttpResponse(url, status) for url, status in responses], ALLOWED_HTTP_STATUSES, where
        ),
        *detect_null_ids(requests, where),
        *detect_missing_header(requests, crawl.base_url, where),
        *detect_failed_assets(failures, where),
        *detect_bad_text(_dom_state(crawl.page), where),
    ]


def _record_findings(crawl: Crawl, found: list[Finding]) -> None:
    for finding in found:
        key = (finding.detector, finding.url, finding.reason)
        if key in crawl.reported:
            continue
        crawl.reported.add(key)
        name = f"finding-{len(crawl.findings) + 1:03d}.png"
        crawl.findings.append(replace(finding, screenshot=_screenshot(crawl, name)))


def _screenshot(crawl: Crawl, name: str) -> str:
    try:
        crawl.page.screenshot(path=str(crawl.run_dir / name))
    except Exception as error:  # the finding stays; only its picture is missing
        log.warning("screenshot %s was not saved: %s", name, _reason(error))
        return ""
    path = f"{crawl.run_dir.name}/{name}"
    crawl.screenshots.append(path)
    return path


def _where(crawl: Crawl, element: str) -> Where:
    return Where(url=crawl.page.url, element=element, step=crawl.clicks, screenshot="")


def _click_problem(crawl: Crawl, element: str, reason: str) -> Finding:
    """A click that timed out is a defect only when another element covers the control."""
    severity = ERROR if COVERED_CLICK_TEXT in reason else WARNING
    return Finding("click-failed", severity, crawl.page.url, element, crawl.clicks, "", reason)


def _problem(crawl: Crawl, detector: str, element: str, reason: str) -> Finding:
    return Finding(detector, ERROR, crawl.page.url, element, crawl.clicks, "", reason)


def _page_bound(crawl: Crawl) -> str | None:
    if crawl.pages >= crawl.bounds.max_pages:
        return "max_pages"
    return _time_bound(crawl)


def _click_bound(crawl: Crawl, clicks_here: int) -> str | None:
    if clicks_here >= crawl.bounds.max_clicks_per_page:
        return "max_clicks_per_page"
    return _time_bound(crawl)


def _time_bound(crawl: Crawl) -> str | None:
    return "max_minutes" if time.monotonic() > crawl.bounds.deadline else None


def build_smoke_verdict(state: Crawl, started: float) -> dict[str, Any]:
    verdict = build_verdict(
        scenario=SMOKE_SCENARIO,
        blocked_reason=state.blocked_reason,
        screenshots=state.screenshots,
        console_errors=state.recorder.console_errors,
        network_errors=state.recorder.network_errors,
        minutes=_minutes_since(started),
    )
    verdict["findings"] = [_finding_json(finding) for finding in state.findings]
    verdict["verdict"] = _outcome(state)
    return verdict


def _outcome(state: Crawl) -> str:
    if state.blocked_reason is not None:
        return "blocked"
    if any(finding.severity == ERROR for finding in state.findings):
        return "fail"
    return "pass"


def _blocked_verdict(reason: str, started: float) -> dict[str, Any]:
    verdict = build_verdict(
        scenario=SMOKE_SCENARIO, blocked_reason=reason, minutes=_minutes_since(started)
    )
    verdict["findings"] = []
    return verdict


def _finding_json(finding: Finding) -> dict[str, str]:
    return {
        "severity": finding.severity,
        "detector": finding.detector,
        "page": scrub(finding.url),
        "element": scrub(finding.element),
        "reason": scrub(finding.reason),
        "screenshot": scrub(finding.screenshot),
    }


def _minutes_since(started: float) -> float:
    return round((time.monotonic() - started) / SECONDS_PER_MINUTE, 2)
