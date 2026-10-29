"""Deterministic screen capture of a design contract: one png and a visible-DOM text
dump per screen, written into the QA run folder and returned as a manifest. No model.
The page is an injectable dependency so tests drive it with a fake.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

from .qa_contract import Contract, Screen
from .qa_runner import RUNS_DIR, _new_run_dir, _reason

log = logging.getLogger("claude_runner.qa_capture")

# The settle wait after a screen's route opens; a named bound for every wait.
SCREEN_TIMEOUT_MS = 15_000
# A screen's route is written as "route /x"; the word is stripped before joining.
ROUTE_PREFIX = "route "
# More steps than this are refused; the first MAX_STEPS are still run.
MAX_STEPS = 10
DOM_SELECTOR = "body"
# A page that fills itself from a request after a click needs a moment before it is photographed.
SETTLE_MS = 1_500


class CaptureError(ValueError):
    """The contract has no screens to capture."""


def capture(
    contract: Contract,
    page: Any,
    base_url: str,
    runs_dir: Path = RUNS_DIR,
) -> list[dict[str, str]]:
    """Capture every screen of the contract, in contract order, into one QA run folder.

    Each screen sets its viewport, opens its route under ``base_url``, runs its steps
    (at most ``MAX_STEPS``), and saves ``<screen>.png`` plus ``<screen>.dom.txt`` (the
    visible text of the DOM). A screen that fails to load or runs a failing step is
    recorded with its error and the capture continues with the remaining screens.
    Returns the manifest: one entry per screen with its name, png path, dom path and
    the error (empty when the screen was captured cleanly).
    """
    if not contract.screens:
        raise CaptureError(f"contract {contract.title!r} has no screens to capture")
    run_dir = _new_run_dir(runs_dir)
    manifest: list[dict[str, str]] = []
    for screen in contract.screens:
        manifest.append(_capture_screen(page, screen, base_url, run_dir))
    return manifest


def _capture_screen(page: Any, screen: Screen, base_url: str, run_dir: Path) -> dict[str, str]:
    name = screen.name
    png = run_dir / f"{name}.png"
    dom = run_dir / f"{name}.dom.txt"
    error = ""
    try:
        viewport = _viewport_dimensions(screen.viewport)
        if viewport is not None:
            page.set_viewport_size({"width": viewport[0], "height": viewport[1]})
        url, steps = _route_and_steps(base_url, screen.route_or_steps)
        page.goto(url)
        page.wait_for_load_state("networkidle", timeout=SCREEN_TIMEOUT_MS)
        error = _run_steps(page, name, steps)
        page.wait_for_timeout(SETTLE_MS)
    except Exception as load_error:  # the screen failed to load; record and move on
        error = _reason(load_error)
        log.warning("capture %s did not load: %s", name, error)
    error = _save_png(page, png, name, error)
    error = _save_dom(page, dom, name, error)
    return {"screen": name, "png": str(png), "dom": str(dom), "error": error}


def _run_steps(page: Any, name: str, steps: list[str]) -> str:
    """Run the screen's steps in order; a step failure or an overlong list is the error."""
    error = ""
    if len(steps) > MAX_STEPS:
        error = f"screen {name} lists {len(steps)} steps, more than MAX_STEPS ({MAX_STEPS})"
        log.warning("%s", error)
        steps = steps[:MAX_STEPS]
    for step in steps:
        selector = _step_selector(step)
        try:
            page.click(selector, timeout=SCREEN_TIMEOUT_MS)
            page.wait_for_load_state("networkidle", timeout=SCREEN_TIMEOUT_MS)
        except Exception as step_error:  # the step did not run; record and stop this screen
            if not error:
                error = _reason(step_error)
            log.warning("screen %s step %r did not run: %s", name, step, _reason(step_error))
            break
    return error


def _step_selector(step: str) -> str:
    """The selector of a step: "click text=Connection" clicks text=Connection."""
    step = step.strip()
    if step.startswith("click "):
        return step[len("click "):]
    return step


def _route_and_steps(base_url: str, route_or_steps: str) -> tuple[str, list[str]]:
    """Split the Open text into the url under the base and the steps after the route."""
    route = route_or_steps
    if route.startswith(ROUTE_PREFIX):
        route = route[len(ROUTE_PREFIX) :]
    parts = [part.strip() for part in route.split(",")]
    steps = [part for part in parts[1:] if part]
    return urljoin(base_url, parts[0] if parts else ""), steps


def _save_png(page: Any, png: Path, screen: str, error: str) -> str:
    try:
        page.screenshot(path=str(png))
    except Exception as screenshot_error:  # the picture is missing; keep the error
        log.warning("screenshot %s was not saved: %s", screen, _reason(screenshot_error))
        return error or _reason(screenshot_error)
    return error


def _save_dom(page: Any, dom: Path, screen: str, error: str) -> str:
    try:
        # the dashboard shell shows the board in a frame: its text belongs to the screen too
        parts = [page.inner_text(DOM_SELECTOR)]
        parts += [frame.inner_text(DOM_SELECTOR) for frame in page.frames[1:]]
        dom.write_text("\n".join(parts), encoding="utf-8")
    except Exception as dom_error:  # the dump is missing; keep the error
        log.warning("dom text %s was not saved: %s", screen, _reason(dom_error))
        return error or _reason(dom_error)
    return error


def _viewport_dimensions(viewport: str) -> tuple[int, int] | None:
    """Parse "1280x800" into (width, height); None when the value is not a dimension."""
    match = re.fullmatch(r"(\d+)x(\d+)", viewport.strip())
    if match is None:
        return None
    return int(match.group(1)), int(match.group(2))
