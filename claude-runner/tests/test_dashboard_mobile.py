from __future__ import annotations

import json
import re
from pathlib import Path

import httpx
import pytest
from starlette.testclient import TestClient

from claude_runner.dashboard import DashboardSettings, create_dashboard_app

SOURCE = Path(__file__).resolve().parents[1] / "src" / "claude_runner"
DASHBOARD = SOURCE / "dashboard.html"
LOGIN = SOURCE / "dashboard_login.html"

Rule = tuple[str, str, str]  # (media query or "", selector list, declarations)
MOBILE_RULES_MIN = 20
MOBILE_WIDTH_MAX_PX = 430
WIDTH_PX = re.compile(r"(?<![-\w])width:\s*(\d+)px")


@pytest.fixture
def dashboard_html() -> str:
    return DASHBOARD.read_text(encoding="utf-8")


@pytest.fixture
def login_html() -> str:
    return LOGIN.read_text(encoding="utf-8")


def _style(html: str) -> str:
    start = html.index("<style>") + len("<style>")
    css = html[start : html.index("</style>")]
    return re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)


def _early_script(html: str) -> str:
    return html[: html.index("<style>")]


def _close_brace(css: str, open_at: int) -> int:
    depth = 0
    for i in range(open_at, len(css)):
        if css[i] == "{":
            depth += 1
        elif css[i] == "}":
            depth -= 1
            if depth == 0:
                return i
    raise ValueError("unbalanced braces in the style block")


def _css_rules(css: str, media: str = "") -> list[Rule]:
    rules: list[Rule] = []
    pos = 0
    while (open_at := css.find("{", pos)) != -1:
        prelude = css[pos:open_at].strip()
        close_at = _close_brace(css, open_at)
        body = css[open_at + 1 : close_at]
        if prelude.startswith("@media"):
            rules += _css_rules(body, prelude)
        else:
            rules.append((media, prelude, body))
        pos = close_at + 1
    return rules


def _mobile_rules(css: str) -> list[Rule]:
    return [r for r in _css_rules(css) if "view-mobile" in r[1] or ":not(.view-desktop)" in r[1]]


def _bodies(rules: list[Rule], *selectors: str) -> list[str]:
    wanted = set(selectors)
    return [body for _, sel, body in rules if wanted & {s.strip() for s in sel.split(",")}]


def test_dashboard_has_viewport_meta(dashboard_html: str) -> None:
    assert '<meta name="viewport" content="width=device-width, initial-scale=1">' in dashboard_html


def test_dashboard_view_script_runs_before_first_style(dashboard_html: str) -> None:
    script_at = dashboard_html.index('"ahawr-dashboard-view"')
    assert dashboard_html.index("<script>") < script_at < dashboard_html.index("<style>")


def test_dashboard_view_localstorage_access_is_in_try_catch(dashboard_html: str) -> None:
    head = _early_script(dashboard_html)
    calls = list(re.finditer(r"localStorage\.(?:get|set|remove)Item\(", head))
    assert calls
    for call in calls:
        before = head[: call.start()]
        assert before.rfind("try {") > before.rfind("catch ("), f"outside try at {call.start()}"
        assert "catch (" in head[call.start() :]


def test_dashboard_view_switch_cycles_auto_mobile_desktop(dashboard_html: str) -> None:
    start = dashboard_html.index('const names = ["auto", "mobile", "desktop"];')
    switch = dashboard_html[start : start + 600]
    for name in ("auto", "mobile", "desktop"):
        assert f'"{name}"' in switch
    assert "% 3]" in switch
    assert "window.ahawrView.set(cur)" in switch


def test_dashboard_view_listens_for_media_changes(dashboard_html: str) -> None:
    head = _early_script(dashboard_html)
    assert 'matchMedia("(max-width: 760px)")' in head
    assert 'narrow.addEventListener("change"' in head
    assert 'coarse.addEventListener("change"' in head


def test_dashboard_coarse_pointer_rule(dashboard_html: str) -> None:
    assert any("(pointer: coarse)" in media for media, _, _ in _css_rules(_style(dashboard_html)))


def test_dashboard_430px_rule(dashboard_html: str) -> None:
    rules = _css_rules(_style(dashboard_html))
    assert any("(max-width: 430px)" in media for media, _, _ in rules)


def test_dashboard_mobile_touch_targets_are_44px(dashboard_html: str) -> None:
    mobile = _mobile_rules(_style(dashboard_html))
    assert len(mobile) >= MOBILE_RULES_MIN
    for scope in ("html.view-mobile", "html:not(.view-desktop)"):
        bodies = _bodies(mobile, f"{scope} button", f"{scope} select")
        assert any("min-height: 44px" in body for body in bodies), scope


def test_dashboard_mobile_pre_and_code_scroll_horizontally(dashboard_html: str) -> None:
    mobile = _mobile_rules(_style(dashboard_html))
    for tag in ("pre", "code"):
        bodies = _bodies(mobile, f"html.view-mobile {tag}", f"html:not(.view-desktop) {tag}")
        assert any("overflow-x: auto" in body for body in bodies), tag


def test_dashboard_mobile_timeline_panel_is_full_screen(dashboard_html: str) -> None:
    mobile = _mobile_rules(_style(dashboard_html))
    panel = _bodies(
        mobile,
        "html.view-mobile #overview.mpanel-open",
        "html:not(.view-desktop) #overview.mpanel-open",
    )
    assert len(panel) == 2
    for body in panel:
        assert "position: fixed" in body
        assert "inset: 0" in body


def test_dashboard_mobile_panel_has_back_button(dashboard_html: str) -> None:
    mobile = _mobile_rules(_style(dashboard_html))
    shown = _bodies(
        mobile,
        "html.view-mobile #overview.mpanel-open + .ov-back",
        "html:not(.view-desktop) #overview.mpanel-open + .ov-back",
    )
    assert len(shown) == 2
    assert all("display: block" in body for body in shown)
    assert '<button class="ov-back" type="button">Back</button>' in dashboard_html
    assert '$(".ov-back").onclick' in dashboard_html


def test_dashboard_stop_run_asks_for_confirmation(dashboard_html: str) -> None:
    start = dashboard_html.index("async function stopRun(id)")
    body = dashboard_html[start : dashboard_html.index("\n}\n", start)]
    assert body.index("confirm(") < body.index("fetch(")
    assert "stopRun(b.dataset.stop)" in dashboard_html


def test_dashboard_mobile_rules_have_no_fixed_width_over_430px(dashboard_html: str) -> None:
    mobile = _mobile_rules(_style(dashboard_html))
    assert len(mobile) >= MOBILE_RULES_MIN
    too_wide = [
        (selector, int(px))
        for _, selector, body in mobile
        for px in WIDTH_PX.findall(body)
        if int(px) > MOBILE_WIDTH_MAX_PX
    ]
    assert not too_wide


def test_login_has_viewport_meta(login_html: str) -> None:
    assert '<meta name="viewport" content="width=device-width, initial-scale=1">' in login_html


def test_login_touch_targets_are_44px(login_html: str) -> None:
    rules = _css_rules(_style(login_html))
    for selector in ("input", "button"):
        assert any("min-height: 44px" in body for body in _bodies(rules, selector)), selector


def test_manifest_is_standalone_and_starts_at_the_dashboard() -> None:
    manifest = json.loads((SOURCE / "dashboard_manifest.json").read_text(encoding="utf-8"))
    assert manifest["start_url"] == "/"
    assert manifest["display"] == "standalone"
    assert {"name", "short_name", "background_color", "theme_color"} <= manifest.keys()


def test_manifest_and_icon_are_linked_from_the_head(dashboard_html: str) -> None:
    assert '<link rel="manifest" href="/manifest.json">' in dashboard_html
    assert '<link rel="icon" href="/icon.svg" type="image/svg+xml">' in dashboard_html


def test_dashboard_registers_no_service_worker(dashboard_html: str) -> None:
    assert "serviceWorker" not in dashboard_html


def test_manifest_and_icon_answer_without_auth() -> None:
    app = create_dashboard_app(
        DashboardSettings(), transport=httpx.MockTransport(lambda _request: httpx.Response(503))
    )
    with TestClient(app, base_url="http://localhost", follow_redirects=False) as client:
        manifest = client.get("/manifest.json")
        icon = client.get("/icon.svg")
    assert manifest.status_code == 200
    assert manifest.json()["start_url"] == "/"
    assert icon.status_code == 200
    assert icon.headers["content-type"] == "image/svg+xml"


def test_dashboard_phone_hides_the_runs_column_on_the_board_and_guards_tabs(
    dashboard_html: str,
) -> None:
    assert 'document.documentElement.dataset.pane = name;' in dashboard_html
    rules = _css_rules(_style(dashboard_html))
    hiding = [
        sel for _, sel, body in rules if "display: none" in body and 'data-pane="board"' in sel
    ]
    assert len(hiding) == 2 and all("#side" in sel for sel in hiding)


def test_board_page_scrolls_as_one_page_on_a_phone() -> None:
    css = (DASHBOARD.parent / "board.css").read_text(encoding="utf-8")
    rule = "#board:not(.provopen):not(.bd-mission) #boardviewbody { flex: none; overflow: visible"
    assert rule + "; }" in css


def test_kanban_keeps_two_status_columns_per_row_on_a_phone() -> None:
    css = (DASHBOARD.parent / "board.css").read_text(encoding="utf-8")
    assert "@media (max-width: 520px) { #boardviewbody .kb { grid-template-columns: repeat(2" in css
    assert "#boardviewbody .kb { grid-template-columns: 1fr; }" not in css
