from __future__ import annotations

import json
import re
import sys
import time
import urllib.request
from collections import defaultdict
from collections.abc import Callable
from functools import partial
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from claude_runner import qa_smoke
from claude_runner.qa_runner import PLAYWRIGHT_HINT
from claude_runner.qa_smoke import (
    INTERACTIVE_SELECTOR,
    MAX_CLICKS_PER_PAGE,
    MAX_MINUTES,
    MAX_PAGES,
    SEEDS_PATH,
    Bounds,
    HostRefusedError,
    MissionPaths,
    Seeds,
    SeedsError,
    build_smoke_verdict,
    check_host,
    crawl,
    destructive_match,
    load_seeds,
    parse_seeds,
    run_smoke,
)

BASE = "http://127.0.0.1:8701"

VALID_SEEDS = """
start_paths:
  - "/static/board.html#board"
mission_paths:
  source: "/api/board/missions"
  list_key: "missions"
  id_field: "mission_id"
  template: "/static/board.html#board/{mission_id}"
products:
  board:
    - "/static/board.html"
destructive:
  - "wipe"
  - "Purge All"
"""


def _seeds(text: str = VALID_SEEDS) -> Seeds:
    return parse_seeds(text)


def test_bounds_are_the_documented_defaults() -> None:
    assert (MAX_PAGES, MAX_CLICKS_PER_PAGE, MAX_MINUTES) == (40, 25, 10)


def test_shipped_seeds_file_has_the_board_start_paths_and_destructive_list() -> None:
    seeds = load_seeds(SEEDS_PATH)
    assert "/static/board.html#board" in seeds.start_paths
    for view in ("kan", "lane", "proj", "spr", "grid"):
        assert f"/static/board.html#board/view/{view}" in seeds.start_paths
    assert seeds.mission_paths.template == "/static/board.html#board/{mission_id}"
    assert set(seeds.products) == {"runner", "board"}
    assert len(seeds.destructive) == 6


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8701/static/board.html",
        "http://localhost:8701/static/board.html",
        "http://qa-board-1:8080/static/board.html",
    ],
)
def test_host_guard_accepts_local_and_qa_container_hosts(url: str) -> None:
    check_host(url)


@pytest.mark.parametrize(
    "url",
    [
        "https://board.example-prod.net/static/board.html",
        "http://qa-board.example-prod.net/static/board.html",
        "http://127.0.0.1.example-prod.net/static/board.html",
    ],
)
def test_host_guard_refuses_non_local_hosts_with_reason(url: str) -> None:
    with pytest.raises(HostRefusedError, match="is not local") as info:
        check_host(url)
    assert "example-prod.net" in str(info.value)


def test_allow_hosts_lets_the_named_host_through_only() -> None:
    url = "https://board.example-prod.net/static/board.html"
    check_host(url, allow_hosts=("Board.Example-Prod.net",))
    with pytest.raises(HostRefusedError):
        check_host("https://other.example-prod.net/", allow_hosts=("board.example-prod.net",))


def test_destructive_matcher_reads_the_list_from_the_seeds_data() -> None:
    seeds = _seeds()
    assert destructive_match("WIPE the board", seeds) == "wipe"
    assert destructive_match("purge all tasks", seeds) == "Purge All"
    assert destructive_match("Open kanban", seeds) is None


def test_destructive_matcher_uses_the_shipped_list() -> None:
    seeds = load_seeds(SEEDS_PATH)
    for word in seeds.destructive:
        assert destructive_match(word.upper(), seeds) == word


def test_destructive_matcher_matches_nothing_for_an_empty_label() -> None:
    assert destructive_match("", _seeds()) is None


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("- just a list\n", "seeds: expected a mapping"),
        (VALID_SEEDS + "extra: 1\n", "unknown field(s) extra"),
        (VALID_SEEDS.replace('"/static/board.html#board"', '"https://evil.example/"'),
         "start_paths[0]"),
        (VALID_SEEDS.replace("{mission_id}", "{id}"), "mission_paths.template"),
        (VALID_SEEDS.replace('"wipe"', '""'), "destructive[0]"),
        (VALID_SEEDS.replace("products:\n  board:\n    - \"/static/board.html\"", "products: []"),
         "products:"),
        ("start_paths: [", "invalid YAML"),
    ],
)
def test_seeds_validation_names_the_bad_field(text: str, reason: str) -> None:
    with pytest.raises(SeedsError, match=re.escape(reason)):
        parse_seeds(text)


def test_load_seeds_reads_a_file(tmp_path: Path) -> None:
    path = tmp_path / "smoke_seeds.yaml"
    path.write_text(VALID_SEEDS, encoding="utf-8")
    assert load_seeds(path).destructive == ("wipe", "Purge All")


class FakeElement:
    def __init__(
        self,
        label: str,
        *,
        href: str = "",
        visible: bool = True,
        on_click: Callable[[], None] | None = None,
    ) -> None:
        self.label = label
        self.href = href
        self.visible = visible
        self.on_click = on_click
        self.clicks = 0

    def get_attribute(self, name: str) -> str | None:
        return self.href or None if name == "href" else None

    def inner_text(self) -> str:
        return self.label

    def is_visible(self) -> bool:
        return self.visible

    def click(self, timeout: float = 0) -> None:
        self.clicks += 1
        if self.on_click is not None:
            self.on_click()


class FakeLocator:
    def __init__(self, elements: list[FakeElement]) -> None:
        self._elements = elements

    def all(self) -> list[FakeElement]:
        return list(self._elements)

    def evaluate_all(self, script: str) -> list[dict[str, object]]:
        return [
            {"label": e.label, "href": e.href, "visible": e.visible, "disabled": False}
            for e in self._elements
        ]

    def nth(self, index: int) -> FakeElement:
        return self._elements[index]

    def count(self) -> int:
        return len(self._elements)

    @property
    def first(self) -> FakeElement:
        return self._elements[0]


class FakeKeyboard:
    def __init__(self) -> None:
        self.keys: list[str] = []

    def press(self, key: str) -> None:
        self.keys.append(key)


class FakePage:
    def __init__(self, elements: list[FakeElement]) -> None:
        self.elements = elements
        self.url = ""
        self.gotos: list[str] = []
        self.screenshots: list[str] = []
        self.keyboard = FakeKeyboard()
        self.handlers: defaultdict[str, list[Callable[[Any], None]]] = defaultdict(list)

    def on(self, event: str, handler: Callable[[Any], None]) -> None:
        self.handlers[event].append(handler)

    def emit(self, event: str, payload: Any) -> None:
        for handler in self.handlers[event]:
            handler(payload)

    def goto(self, url: str) -> None:
        self.url = url
        self.gotos.append(url)

    def wait_for_load_state(self, state: str, timeout: float) -> None:
        return None

    def screenshot(self, path: str) -> None:
        self.screenshots.append(path)

    def locator(self, selector: str) -> FakeLocator:
        return FakeLocator(self.elements if selector == INTERACTIVE_SELECTOR else [])


def _seeds_with(
    start_paths: tuple[str, ...],
    destructive: tuple[str, ...] = ("wipe", "restart", "Purge All"),
) -> Seeds:
    return Seeds(
        start_paths=start_paths,
        mission_paths=MissionPaths(
            source="/api/board/missions",
            list_key="missions",
            id_field="mission_id",
            template="/static/board.html#board/{mission_id}",
        ),
        products={"board": ("/static/board.html",)},
        destructive=destructive,
    )


def test_max_pages_stops_the_crawl(tmp_path: Path) -> None:
    page = FakePage([FakeElement("Open kanban")])
    state = crawl(page, _seeds_with(("/a", "/b")), BASE, tmp_path, Bounds(max_pages=1))
    assert state.pages == 1
    assert state.bound_reached == "max_pages"
    assert page.gotos == [BASE + "/a"]


def test_max_clicks_per_page_stops_the_page(tmp_path: Path) -> None:
    elements = [FakeElement(f"Open {n}") for n in range(3)]
    state = crawl(
        FakePage(elements), _seeds_with(("/a",)), BASE, tmp_path, Bounds(max_clicks_per_page=2)
    )
    assert [element.clicks for element in elements] == [1, 1, 0]
    assert state.bound_reached == "max_clicks_per_page"


def test_max_minutes_stops_before_the_first_page(tmp_path: Path) -> None:
    page = FakePage([FakeElement("Open kanban")])
    bounds = Bounds(deadline=time.monotonic() - 1)
    state = crawl(page, _seeds_with(("/a",)), BASE, tmp_path, bounds)
    assert (state.pages, page.gotos, state.bound_reached) == (0, [], "max_minutes")


def test_destructive_elements_are_never_clicked(tmp_path: Path) -> None:
    wipe = FakeElement("Wipe board")
    restart = FakeElement("Open", href="/api/board/restart")
    purge = FakeElement("Purge All tasks")
    safe = FakeElement("Open kanban")
    page = FakePage([wipe, restart, purge, safe])
    state = crawl(page, _seeds_with(("/a",)), BASE, tmp_path, Bounds())
    assert (wipe.clicks, restart.clicks, purge.clicks, safe.clicks) == (0, 0, 0, 1)
    assert state.skipped == [
        {"label": "Wipe board", "matched": "wipe"},
        {"label": "Open", "matched": "restart"},
        {"label": "Purge All tasks", "matched": "Purge All"},
    ]


def test_playwright_missing_is_blocked_with_the_install_hint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(sys.modules, "playwright.sync_api", None)
    verdict = run_smoke(_seeds(), base_url=BASE, runs_dir=tmp_path)
    assert verdict["verdict"] == "blocked"
    assert verdict["blocked_reason"] == PLAYWRIGHT_HINT
    assert verdict["findings"] == []


def test_refused_host_is_blocked_before_the_browser_starts(tmp_path: Path) -> None:
    verdict = run_smoke(
        _seeds(), base_url="https://board.example-prod.net", runs_dir=tmp_path
    )
    assert verdict["verdict"] == "blocked"
    assert "is not local" in verdict["blocked_reason"]


def test_error_finding_fails_the_verdict_and_keeps_a_named_screenshot(tmp_path: Path) -> None:
    page = FakePage([])
    boom = FakeElement(
        "Open kanban", on_click=partial(page.emit, "pageerror", RuntimeError("TypeError: x"))
    )
    page.elements.append(boom)
    state = crawl(page, _seeds_with(("/a",)), BASE, tmp_path, Bounds())
    verdict = build_smoke_verdict(state, time.monotonic())
    assert verdict["verdict"] == "fail"
    assert ("pageerror", "error") in [(f["detector"], f["severity"]) for f in verdict["findings"]]
    assert re.fullmatch(r"[\w.-]+/finding-001\.png", verdict["screenshots"][0])


def test_warning_only_findings_pass(tmp_path: Path) -> None:
    page = FakePage([FakeElement("Open kanban")])
    state = crawl(page, _seeds_with(("/a",)), BASE, tmp_path, Bounds())
    verdict = build_smoke_verdict(state, time.monotonic())
    assert verdict["verdict"] == "pass"
    assert [(f["detector"], f["severity"]) for f in verdict["findings"]] == [
        ("no-effect", "warning")
    ]


@pytest.fixture(autouse=True)
def _no_mission_list(monkeypatch: pytest.MonkeyPatch) -> None:
    """The crawl tests use a fake page: they must not read a mission list over HTTP."""
    monkeypatch.setattr(qa_smoke, "_mission_urls", lambda seeds, base_url: [])


def test_mission_urls_come_from_the_list_the_seeds_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Reply:
        def __enter__(self) -> Reply:
            return self

        def __exit__(self, *exc: object) -> None:
            return None

        def read(self) -> bytes:
            rows = [{"mission_id": f"m{n}"} for n in range(1, 6)] + [{"other": 1}]
            return json.dumps({"missions": rows}).encode()

    monkeypatch.undo()
    monkeypatch.setattr(urllib.request, "urlopen", lambda url, timeout: Reply())
    seeds = _seeds()
    urls = qa_smoke._mission_urls(seeds, "http://x")
    assert len(urls) == qa_smoke.MISSION_PAGES
    assert urls[0].startswith("http://x/static/board.html#board/m1")


def test_mission_urls_are_empty_when_the_list_cannot_be_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def refuse(url: str, timeout: float) -> None:
        raise OSError("down")

    monkeypatch.undo()
    monkeypatch.setattr(urllib.request, "urlopen", refuse)
    assert qa_smoke._mission_urls(_seeds(), "http://x") == []


def test_validation_answer_to_a_provoked_write_is_not_a_finding() -> None:
    base = "http://127.0.0.1:8701"
    write = qa_smoke.HttpRequest("POST", f"{base}/api/board/missions/create", {})
    read = qa_smoke.HttpRequest("GET", f"{base}/api/board/queue", {})
    responses = [(write.url, 400), (read.url, 400)]
    load_failed = "Failed to load resource: the server responded with a status of 400"
    console = [load_failed] * 2

    kept, texts, owed = qa_smoke._without_validation_answers(responses, {write.url}, console)

    assert kept == [(read.url, 400)]
    assert len(texts) == 1
    assert owed == 0


def test_a_server_error_to_a_write_stays_a_finding() -> None:
    write = qa_smoke.HttpRequest("POST", "http://127.0.0.1:8701/api/board/queue/move", {})

    kept, texts, _owed = qa_smoke._without_validation_answers(
        [(write.url, 500)], {write.url}, ["Failed to load resource: 500"]
    )

    assert kept == [(write.url, 500)]
    assert texts == ["Failed to load resource: 500"]


def test_a_console_line_that_lands_in_a_later_step_is_still_matched() -> None:
    write = qa_smoke.HttpRequest("POST", "http://127.0.0.1:8701/api/board/missions/create", {})
    load_failed = "Failed to load resource: the server responded with a status of 400"

    _kept, first_texts, owed = qa_smoke._without_validation_answers(
        [(write.url, 400)], {write.url}, []
    )
    _kept, later_texts, owed = qa_smoke._without_validation_answers([], set(), [load_failed], owed)

    assert first_texts == [] and later_texts == [] and owed == 0


class _PageAt:
    url = "http://127.0.0.1:8701/static/board.html#board/m01"


def test_a_click_timeout_is_a_warning_unless_another_element_covers_the_control() -> None:
    crawl = SimpleNamespace(page=_PageAt(), clicks=3)

    hidden = qa_smoke._click_problem(crawl, "toggle", "Timeout 15000ms exceeded: not visible")
    covered = qa_smoke._click_problem(
        crawl, "save", "<div class='modal'> intercepts pointer events"
    )

    assert (hidden.detector, hidden.severity) == ("click-failed", "warning")
    assert (covered.detector, covered.severity) == ("click-failed", "error")
