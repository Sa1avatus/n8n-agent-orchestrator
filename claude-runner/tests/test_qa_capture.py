"""Tests for the deterministic contract screen capture: manifest order, per-screen error
recording that does not stop the run, and the file layout inside the QA run folder."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

from claude_runner.qa_capture import (
    MAX_STEPS,
    SCREEN_TIMEOUT_MS,
    SETTLE_MS,
    CaptureError,
    _route_and_steps,
    _step_selector,
    _viewport_dimensions,
    capture,
)
from claude_runner.qa_contract import Contract, Screen

BASE = "http://127.0.0.1:8701"

CONTRACT = Contract(
    title="Board: providers",
    screens=(
        Screen(
            name="Providers page",
            route_or_steps="route /providers",
            viewport="1280x800",
            layout="cards in a row",
            reference_image="providers-reference.png",
        ),
        Screen(
            name="Guards detail",
            route_or_steps="route /guards/1",
            viewport="1024x768",
            layout="detail pane",
            reference_image="guards-reference.png",
        ),
    ),
    items=(),
)

STEPPED_SCREEN = Screen(
    name="Kanban page",
    route_or_steps="route /kanban, click text=Connection, click text=Free only",
    viewport="1280x800",
    layout="kanban board",
    reference_image="kanban-reference.png",
)

STEPPED = Contract(
    title="Board: kanban",
    screens=(STEPPED_SCREEN,),
    items=(),
)


class FakePage:
    """A Playwright page double: it records viewports, routes, timeouts and clicks and
    can fail on a named route, a named operation or a named step selector."""

    def __init__(
        self,
        dom_text: str = "",
        failing_routes: tuple[str, ...] = (),
        failing: tuple[str, ...] = (),
        failing_steps: tuple[str, ...] = (),
    ) -> None:
        self.viewports: list[dict[str, int]] = []
        self.gotos: list[str] = []
        self.timeouts: list[float] = []
        self.clicks: list[str] = []
        self.dom_text = dom_text
        self.failing_routes = failing_routes
        self.failing = failing
        self.failing_steps = failing_steps
        self.screenshots: list[str] = []
        self.frames: list[Any] = [self]
        self.settles: list[int] = []

    def set_viewport_size(self, viewport: dict[str, int]) -> None:
        self.viewports.append(viewport)

    def goto(self, url: str) -> None:
        if url in self.failing_routes:
            raise RuntimeError(f"navigation failed for {url}")
        self.gotos.append(url)

    def wait_for_load_state(self, state: str, timeout: float) -> None:
        self.timeouts.append(timeout)

    def click(self, selector: str, timeout: float) -> None:
        if selector in self.failing_steps:
            raise RuntimeError(f"click failed for {selector}")
        self.clicks.append(selector)
        self.timeouts.append(timeout)

    def wait_for_timeout(self, milliseconds: int) -> None:
        self.settles.append(milliseconds)

    def screenshot(self, path: str) -> None:
        if "screenshot" in self.failing:
            raise RuntimeError("screenshot failed")
        self.screenshots.append(path)

    def inner_text(self, selector: str) -> str:
        if "inner_text" in self.failing:
            raise RuntimeError("inner_text failed")
        return self.dom_text


def _run_dir(tmp_path: Path) -> Path:
    return next(iter(tmp_path.iterdir()))


def test_manifest_lists_every_screen_in_order_with_files_in_the_run_folder(
    tmp_path: Path,
) -> None:
    page = FakePage(dom_text="providers")
    manifest = capture(CONTRACT, page, BASE, runs_dir=tmp_path)

    run_dir = _run_dir(tmp_path)
    assert [entry["screen"] for entry in manifest] == ["Providers page", "Guards detail"]
    assert all(entry["error"] == "" for entry in manifest)
    for entry in manifest:
        png = Path(entry["png"])
        dom = Path(entry["dom"])
        assert png.parent == run_dir
        assert dom.parent == run_dir
        assert png.name == f"{entry['screen']}.png"
        assert dom.name == f"{entry['screen']}.dom.txt"


def test_screen_files_are_written_with_the_visible_dom_text(tmp_path: Path) -> None:
    page = FakePage(dom_text="visible text")
    capture(CONTRACT, page, BASE, runs_dir=tmp_path)

    run_dir = _run_dir(tmp_path)
    assert page.screenshots == [
        str(run_dir / "Providers page.png"),
        str(run_dir / "Guards detail.png"),
    ]
    assert (run_dir / "Providers page.dom.txt").read_text(encoding="utf-8") == "visible text"
    assert (run_dir / "Guards detail.dom.txt").read_text(encoding="utf-8") == "visible text"
    # every route is opened under the base url with the "route " prefix stripped
    assert page.gotos == [f"{BASE}/providers", f"{BASE}/guards/1"]
    assert page.viewports == [{"width": 1280, "height": 800}, {"width": 1024, "height": 768}]


def test_a_screen_that_fails_to_load_is_recorded_and_the_capture_continues(
    tmp_path: Path,
) -> None:
    page = FakePage(failing_routes=(f"{BASE}/guards/1",))
    manifest = capture(CONTRACT, page, BASE, runs_dir=tmp_path)

    assert [entry["screen"] for entry in manifest] == ["Providers page", "Guards detail"]
    assert manifest[0]["error"] == ""
    assert manifest[1]["error"] != ""
    # the failing screen is still listed with its (absent) file paths inside the run folder
    run_dir = _run_dir(tmp_path)
    assert Path(manifest[1]["png"]).parent == run_dir


def test_a_later_failing_screen_does_not_stop_the_earlier_ones(tmp_path: Path) -> None:
    page = FakePage(failing_routes=(f"{BASE}/providers",))
    manifest = capture(CONTRACT, page, BASE, runs_dir=tmp_path)

    assert manifest[0]["error"] != ""
    assert manifest[1]["error"] == ""
    # the first route was attempted; the second one still opened afterwards
    assert page.gotos == [f"{BASE}/guards/1"]


def test_every_wait_uses_the_named_timeout_constant(tmp_path: Path) -> None:
    page = FakePage()
    capture(CONTRACT, page, BASE, runs_dir=tmp_path)
    # one settle wait per screen, no steps in this contract
    assert page.timeouts == [SCREEN_TIMEOUT_MS] * len(CONTRACT.screens)


def test_steps_run_in_order_after_the_route(tmp_path: Path) -> None:
    page = FakePage()
    manifest = capture(STEPPED, page, BASE, runs_dir=tmp_path)

    assert manifest[0]["error"] == ""
    assert page.clicks == ["text=Connection", "text=Free only"]
    assert page.gotos == [f"{BASE}/kanban"]
    # one settle wait, then a click bound and a settle wait per step, all the named constant
    assert page.timeouts == [SCREEN_TIMEOUT_MS] * (1 + 2 * len(page.clicks))


def test_a_failing_step_is_recorded_and_the_other_screens_still_capture(
    tmp_path: Path,
) -> None:
    page = FakePage(failing_steps=("text=Connection",))
    contract = Contract(
        title="Two screens", screens=(STEPPED_SCREEN, CONTRACT.screens[1]), items=()
    )
    manifest = capture(contract, page, BASE, runs_dir=tmp_path)

    assert manifest[0]["screen"] == "Kanban page"
    assert manifest[0]["error"] != ""
    # the step after the failing one is not run, but the screen is still saved
    assert page.clicks == []
    assert manifest[1]["error"] == ""
    assert page.screenshots != []


def test_steps_beyond_max_steps_are_refused_and_recorded(tmp_path: Path) -> None:
    too_many = Screen(
        name="Too many steps",
        route_or_steps="route /many, " + ", ".join(f"click text={i}" for i in range(MAX_STEPS + 1)),
        viewport="1280x800",
        layout="list",
        reference_image="many-reference.png",
    )
    page = FakePage()
    manifest = capture(
        Contract(title="Too many", screens=(too_many,), items=()), page, BASE, runs_dir=tmp_path
    )

    assert manifest[0]["error"] != ""
    assert "MAX_STEPS" in manifest[0]["error"]
    assert len(page.clicks) == MAX_STEPS


def test_a_screenshot_failure_is_recorded_in_the_manifest(tmp_path: Path) -> None:
    page = FakePage(failing=("screenshot",))
    manifest = capture(CONTRACT, page, BASE, runs_dir=tmp_path)

    assert manifest[0]["error"] != ""
    assert manifest[1]["error"] != ""


def test_a_contract_without_screens_is_refused(tmp_path: Path) -> None:
    empty = Contract(title="Empty", screens=(), items=())
    with pytest.raises(CaptureError, match="has no screens"):
        capture(empty, FakePage(), BASE, runs_dir=tmp_path)


def test_route_and_steps_are_split_in_order() -> None:
    assert _route_and_steps(BASE, "route /providers") == (f"{BASE}/providers", [])
    assert _route_and_steps(BASE, "/guards/1") == (f"{BASE}/guards/1", [])
    # a relative path is joined onto the base url, matching the smoke crawl's base_url + path
    assert _route_and_steps(BASE, "kanban") == (f"{BASE}/kanban", [])
    opened = "route /kanban, click text=Connection, click text=Free only"
    url, steps = _route_and_steps(BASE, opened)
    assert url == f"{BASE}/kanban"
    assert steps == ["click text=Connection", "click text=Free only"]


def test_step_selectors_strip_the_click_word() -> None:
    assert _step_selector("click text=Connection") == "text=Connection"
    assert _step_selector("text=Free only") == "text=Free only"


def test_viewport_dimensions_are_parsed_or_left_unset() -> None:
    assert _viewport_dimensions("1280x800") == (1280, 800)
    assert _viewport_dimensions(" 1024 x 768 ") is None
    assert _viewport_dimensions("wide") is None
    assert _viewport_dimensions("") is None


def test_route_names_match_the_screen_name_shape(tmp_path: Path) -> None:
    """The manifest names are the exact screen names: <screen>.png and <screen>.dom.txt."""
    page = FakePage()
    manifest = capture(CONTRACT, page, BASE, runs_dir=tmp_path)
    for entry in manifest:
        assert re.fullmatch(re.escape(entry["screen"]) + r"\.png", Path(entry["png"]).name)
        assert re.fullmatch(re.escape(entry["screen"]) + r"\.dom\.txt", Path(entry["dom"]).name)


def test_the_text_of_a_frame_is_part_of_the_dom_dump_and_the_page_settles(
    tmp_path: Path,
) -> None:
    page = FakePage(dom_text="shell")
    page.frames.append(FakePage(dom_text="board frame"))
    manifest = capture(STEPPED, page, BASE, runs_dir=tmp_path)

    assert Path(manifest[0]["dom"]).read_text(encoding="utf-8") == "shell\nboard frame"
    assert page.settles == [SETTLE_MS]
