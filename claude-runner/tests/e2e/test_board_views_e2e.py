"""Browser checks for the board views (Projects, Kanban, Grid).

The static board files are served from src/claude_runner through page routing
on the fake origin http://board.test. The /api/board/* calls are answered from
tests/fixtures/board_views_e2e/. No request goes to the live dashboard
(127.0.0.1:8701): every non-board.test URL is aborted and recorded, and the
tests fail if any recorded URL points at that port.

tests/e2e/serve_board.py does not exist in this repository, so the network is
mocked with page.route. The mock is stateful for the i18n endpoints: PUT
/api/board/i18n/<lang>/<key> stores the text and the next GET
/api/board/i18n returns it, which is how the in-memory translation editor
check (edit a translation and see it in the UI at once) is run here.

Requires: pip install playwright && playwright install chromium
Without Playwright or a Chromium build the module is skipped with the reason.
"""

import json
import mimetypes
from pathlib import Path

import pytest

sync_api = pytest.importorskip(
    "playwright.sync_api",
    reason="playwright is not installed (pip install playwright && playwright install chromium)",
)

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src" / "claude_runner"
FIXTURES = ROOT / "tests" / "fixtures" / "board_views_e2e"
ORIGIN = "http://board.test"
FORBIDDEN_PORT = "8701"


def _load(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _i18n_state():
    """The in-memory i18n store the mock answers from.

    Starts from the bundled JSON files (the same data the live server
    seeds with) and is mutated by PUT /api/board/i18n/<lang>/<key>,
    so a GET after a PUT sees the edited text (in-memory store semantics).
    """
    strings = {}
    for code in ("en", "ru"):
        strings[code] = json.loads((SRC / f"board_i18n_{code}.json").read_text(encoding="utf-8"))
    return {
        "languages": [
            {"code": "en", "name": "English", "enabled": True},
            {"code": "ru", "name": "Русский", "enabled": True},
        ],
        "strings": strings,
    }


@pytest.fixture(scope="module")
def browser():
    pw = sync_api.sync_playwright().start()
    try:
        browser = pw.chromium.launch()
    except Exception as exc:  # missing browser binary or sandbox problem
        pw.stop()
        pytest.skip(f"chromium is not available for playwright: {exc}")
    yield browser
    browser.close()
    pw.stop()


@pytest.fixture
def board(browser):
    """A page on the mocked board origin, plus the list of URLs it requested."""
    summary = _load("summary.json")
    mission = _load("mission.json")
    i18n = _i18n_state()
    seen = []
    console_errors = []

    context = browser.new_context(viewport={"width": 1280, "height": 900})

    def handle(route):
        url = route.request.url
        seen.append(url)
        if not url.startswith(ORIGIN + "/"):
            return route.abort()
        method = route.request.method
        path = url[len(ORIGIN):].split("?", 1)[0]
        if path == "/api/board/summary":
            return route.fulfill(json=summary)
        if path == "/api/board/queue":
            # Shape the board scripts read: { groups, missions, queue, version }
            # (board_dnd.loadBoardData merges GET /missions and GET /queue).
            return route.fulfill(json={
                "groups": {
                    "running": ["m-alpha-run"],
                    "queued": ["m-beta-queued"],
                    "not_completed": ["m-hostile"],
                    "completed": ["m-alpha-done"],
                },
                "missions": [
                    {"mission_id": "m-alpha-run", "state_group": "running"},
                    {"mission_id": "m-alpha-done", "state_group": "completed"},
                    {"mission_id": "m-beta-queued", "state_group": "queued"},
                    {"mission_id": "m-hostile", "state_group": "not_completed"},
                ],
                "queue": [{"mission_id": "m-beta-queued", "position": 0}],
                "version": "v1",
            })
        if path == "/api/board/missions":
            return route.fulfill(json={
                "groups": {
                    "running": ["m-alpha-run"],
                    "queued": ["m-beta-queued"],
                    "not_completed": ["m-hostile"],
                    "completed": ["m-alpha-done"],
                },
                "missions": summary["missions"],
            })
        if path == "/api/board/i18n" and method == "PUT":
            # Not used by the UI; keep the mock complete anyway.
            return route.fulfill(status=405, json={"detail": "method not allowed"})
        if path == "/api/board/i18n":
            return route.fulfill(json=i18n)
        if path.startswith("/api/board/i18n/"):
            if method != "PUT":
                return route.fulfill(status=405, json={"detail": "method not allowed"})
            rest = path[len("/api/board/i18n/"):]
            lang, _, key = rest.partition("/")
            body = route.request.post_data or "{}"
            try:
                text = json.loads(body).get("text", "")
            except ValueError:
                return route.fulfill(status=400, json={"detail": "bad body"})
            if not isinstance(text, str):
                return route.fulfill(status=400, json={"detail": "text must be a string"})
            known = {lang_row["code"] for lang_row in i18n["languages"]} | {"en"}
            if lang not in known:
                return route.fulfill(status=400, json={"detail": f"unknown language {lang}"})
            table = i18n["strings"].setdefault(lang, {})
            if text:
                table[key] = text
            else:
                table.pop(key, None)
            return route.fulfill(json={"lang": lang, "key": key, "text": text})
        if path.startswith("/api/board/missions/"):
            if path.endswith("/claudemd"):
                return route.fulfill(json={"text": "", "source": "none"})
            return route.fulfill(json=mission)
        if path.startswith("/static/"):
            name = path[len("/static/"):]
            target = SRC / name
            if target.is_file() and target.parent == SRC:
                mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
                return route.fulfill(path=str(target), content_type=mime)
            return route.fulfill(status=404, body="not found")
        if path.startswith("/api/"):
            return route.fulfill(status=404, json={"detail": "not mocked"})
        return route.fulfill(path=str(SRC / "board.html"), content_type="text/html; charset=utf-8")

    context.route("**/*", handle)
    page = context.new_page()
    page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)
    page.on("pageerror", lambda err: console_errors.append(str(err)))

    class Board:
        pass

    b = Board()
    b.page = page
    b.seen = seen
    b.errors = console_errors
    b.context = context
    b.i18n = i18n
    yield b
    context.close()


def _open(b, fragment="", init_script=None):
    if init_script:
        b.context.add_init_script(init_script)
    b.page.goto(f"{ORIGIN}/{fragment}")
    b.page.wait_for_selector("#board .bv-card, #board .bv-note", timeout=10_000)
    # Let the first summary fetch and i18n load settle (they redraw once).
    b.page.wait_for_timeout(500)


def _wait_hash(b, fragment):
    b.page.wait_for_function(
        f"() => location.hash === {fragment!r}", timeout=10_000
    )


def test_projects_view_shows_hostile_names_as_text(board):
    _open(board, "#board/view/proj")
    page = board.page
    hostile = page.locator(".bv-card", has_text="<img src=x")
    assert hostile.count() >= 1
    assert page.evaluate("() => window.__pwned === undefined")
    # The hostile markup must appear as text only: no <img> element exists.
    assert page.locator("#board img").count() == 0


def test_kanban_renders_state_columns_and_mission_cards(board):
    _open(board, "#board/view/kan")
    page = board.page
    assert page.locator(".kb .kcol").count() == 4
    assert page.locator('.kcol[data-col="run"] .kcard[data-mission-id="m-alpha-run"]').count() == 1
    assert page.locator('.kcol[data-col="ok"] .kcard[data-mission-id="m-alpha-done"]').count() == 1
    assert page.locator('.kcol[data-col="q"] .kcard[data-mission-id="m-beta-queued"]').count() == 1


def test_kanban_click_highlights_card(board):
    _open(board, "#board/view/kan")
    page = board.page
    card = page.locator('.kcol[data-col="run"] .kcard[data-mission-id="m-alpha-run"]')
    card.click()
    # A single click highlights the card; the double click is wired in part 2.
    assert "sel" in (card.get_attribute("class") or "")


def test_kanban_project_hash_filter_keeps_only_that_project(board):
    _open(board, "#board/view/kan?project=alpha")
    page = board.page
    projects = page.locator(".kb .kcard").evaluate_all("els => els.map(e => e.dataset.project)")
    assert projects, "no kanban cards rendered"
    assert set(projects) == {"alpha"}


def test_grid_renders_highlights_and_dblclick_opens_mission(board):
    _open(board, "#board/view/grid")
    page = board.page
    rows = page.locator(".gridwrap tr.row.bv-card")
    assert page.locator('.gridwrap tr.row.bv-card[data-project="alpha"]').count() == 2
    row = page.locator('.gridwrap tr.row.bv-card[data-id="m-alpha-run"]')
    row.click()
    assert "sel" in (row.get_attribute("class") or "")
    row.dblclick()
    _wait_hash(board, "#board/m-alpha-run")
    page.wait_for_selector("#boardmission", timeout=10_000)


def test_project_click_highlights_and_dblclick_filters_kanban(board):
    _open(board, "#board/view/proj")
    page = board.page
    card = page.locator('.bv-card.pr[data-project="alpha"]')
    cards_before = page.locator(".bv-card").count()
    card.click()
    # A click only adds the highlight class; the element is not replaced.
    assert "sel" in (card.get_attribute("class") or "")
    assert card.count() == 1
    assert page.locator(".bv-card").count() == cards_before
    # A double click filters Kanban by the project.
    card.dblclick()
    _wait_hash(board, "#board/view/kan?project=alpha")
    projects = page.locator(".kb .kcard").evaluate_all("els => els.map(e => e.dataset.project)")
    assert projects, "no kanban cards rendered"
    assert set(projects) == {"alpha"}


def test_dblclick_mission_in_project_opens_mission(board):
    _open(board, "#board/view/proj")
    page = board.page
    card = page.locator('.bv-card.pr[data-project="alpha"]')
    # The arrow expands the project's missions.
    page.locator('.pr[data-project="alpha"] .arr').click()
    page.wait_for_selector('.pr[data-project="alpha"] .prb .kc[data-m="m-alpha-run"]', timeout=5_000)
    page.locator('.pr[data-project="alpha"] .kc[data-m="m-alpha-run"]').dblclick()
    _wait_hash(board, "#board/m-alpha-run")
    page.wait_for_selector("#boardmission", timeout=10_000)


def test_mission_screen_renders_mission_name(board):
    _open(board, "#board/m-alpha-run")
    board.page.wait_for_function(
        "() => (document.getElementById('boardmission') || {}).textContent"
        " && document.getElementById('boardmission').textContent.includes('Alpha running mission')",
        timeout=10_000,
    )


def test_language_switch_ru_and_back(board):
    _open(board, "#board/view/kan")
    page = board.page
    ru = json.loads((SRC / "board_i18n_ru.json").read_text(encoding="utf-8"))
    page.locator(".bv-lang").select_option("ru")
    page.wait_for_function(
        "() => document.querySelector('.bv-tabs') !== null"
        " && document.querySelector('.bv-tabs').textContent.includes('Канбан')",
        timeout=10_000,
    )
    assert ru.get("tab.kan") == "Канбан"
    page.wait_for_function("() => localStorage.getItem('board.lang') === 'ru'", timeout=5_000)
    page.locator(".bv-lang").select_option("en")
    page.wait_for_function(
        "() => document.querySelector('.bv-tabs') !== null"
        " && document.querySelector('.bv-tabs').textContent.includes('Kanban')",
        timeout=10_000,
    )
    page.wait_for_function("() => localStorage.getItem('board.lang') === 'en'", timeout=5_000)


def test_translation_edit_visible_at_once(board):
    _open(board, "#board/view/proj")
    page = board.page
    # Open the translation editor from the header button.
    page.locator(".bv-tr").click()
    page.wait_for_selector(".bvi18n", timeout=10_000)
    page.wait_for_selector(".bvi18n table", timeout=10_000)
    key = "tab.kan"
    # The editor's language select must be on Russian before editing.
    page.locator(".bvi18n select").select_option("ru")
    page.wait_for_selector(f".bvi18n tr:has-text({key}) input[type=text]", timeout=5_000)
    row_input = page.locator(f".bvi18n tr:has-text({key}) input[type=text]")
    # The input listens to "change": blur (Tab out of the field) fires it,
    # which saves the text with PUT /api/board/i18n/ru/tab.kan.
    with page.expect_request(lambda r: r.method == "PUT" and "/api/board/i18n/ru/" + key in r.url):
        row_input.fill("Канбан-тест")
        row_input.press("Tab")
    # PUT landed in the in-memory store: the next GET returns the new text.
    stored = page.evaluate("() => window.fetch('/api/board/i18n').then(r => r.json())")
    assert stored["strings"]["ru"].get(key) == "Канбан-тест"
    # Switch to Russian: the edited text shows in the UI at once.
    page.locator(".bv-lang").select_option("ru")
    page.wait_for_function(
        "() => document.querySelector('.bv-tabs') !== null"
        " && document.querySelector('.bv-tabs').textContent.includes('Канбан-тест')",
        timeout=10_000,
    )


def test_dark_theme_from_storage(board):
    _open(board, "#board/view/proj", init_script="localStorage.setItem('ahawr-dashboard-theme', 'dark')")
    page = board.page
    theme = page.evaluate("() => document.documentElement.getAttribute('data-theme')")
    assert theme == "dark"
    assert page.locator(".bv-card").count() > 0


def test_narrow_viewport_has_no_horizontal_overflow(board):
    board.page.set_viewport_size({"width": 375, "height": 800})
    _open(board, "#board/view/kan")
    scroll_width = board.page.evaluate("() => document.documentElement.scrollWidth")
    assert scroll_width <= 375, f"page is {scroll_width}px wide at a 375px viewport"


def test_no_console_errors_and_no_request_to_live_dashboard(board):
    _open(board, "#board/view/proj")
    board.page.wait_for_timeout(300)
    assert board.errors == [], f"console errors: {board.errors}"
    forbidden = [u for u in board.seen if f":{FORBIDDEN_PORT}" in u]
    assert forbidden == [], f"requests reached the live dashboard port: {forbidden}"
