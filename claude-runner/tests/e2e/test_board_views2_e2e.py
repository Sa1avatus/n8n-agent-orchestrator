"""Browser checks for the board Lanes view, the mission task table and the double-click paths.

The static board files are served from src/claude_runner through page routing
on the fake origin http://board.test.

tests/e2e/serve_board.py does not exist in this repository, so every
/api/board/* call is answered by page.route mocks defined in this file. The
mock keeps the plan of one mission in memory: a reorder POST that the server
accepts changes the order the next GET of the plan and the summary return, so
the Lanes view and the task table redraw from the stored state.

Every non-board.test URL is aborted and recorded. Every non-GET request is
recorded as a write. The tests fail if a request reaches the live dashboard
(127.0.0.1:8701).

Requires: pip install playwright && playwright install chromium
Without Playwright or a Chromium build the module is skipped with the reason.
"""

import copy
import json
import mimetypes
import types
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
MISSION = "m-alpha-run"

TASKS = [
    {"task_id": "T000", "title": "Scaffold", "status": "accepted", "attempts": 1, "deps": []},
    {"task_id": "T001", "title": "Parse input", "status": "waiting", "attempts": 0, "deps": []},
    {"task_id": "T002", "title": "Build index", "status": "waiting", "attempts": 0, "deps": []},
    {"task_id": "T003", "title": "Write report", "status": "waiting", "attempts": 0, "deps": ["T001"]},
]

STATS = {
    "worker_hours": 0.5,
    "reviewer_minutes": 0,
    "architect_minutes": 0,
    "input_tokens": 12000,
    "output_tokens": 3000,
    "cache_ratio": 0.25,
    "tok_per_s": 42,
    "cost_usd": 0.12,
    "thinking_steps": 0,
    "thinking_runs": 0,
    "attempts": 1,
    "max_attempts_per_task": 3,
}


def _read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _new_store():
    return {"version": 1, "tasks": copy.deepcopy(TASKS)}


def _summary(store):
    alpha = {"key": "alpha", "name": "Alpha", "rule": "alpha-*"}
    beta = {"key": "beta", "name": "Beta", "rule": "beta-*"}
    running = {
        "mission_id": MISSION,
        "name": "Alpha running mission",
        "enabled": True,
        "state_group": "running",
        "project": alpha,
        "position": 0,
        "tasks": [
            {"id": t["task_id"], "title": t["title"], "status": t["status"],
             "attempts": t["attempts"], "deps": list(t["deps"])}
            for t in store["tasks"]
        ],
        "hours": 1.5,
        "tokens": 15000,
        "cost": 0.42,
    }
    finished = {
        "mission_id": "m-alpha-done",
        "name": "Alpha finished mission",
        "enabled": True,
        "state_group": "completed",
        "project": alpha,
        "position": 1,
        "tasks": [{"id": "T900", "title": "Done", "status": "accepted", "attempts": 1, "deps": []}],
        "hours": 0.5,
        "tokens": 3000,
        "cost": 0.1,
    }
    queued = {
        "mission_id": "m-beta-queued",
        "name": "Beta queued mission",
        "enabled": True,
        "state_group": "queued",
        "project": beta,
        "position": 2,
        "tasks": [],
        "hours": 0,
        "tokens": 0,
        "cost": 0,
    }
    return {
        "projects": [
            {"key": "alpha", "name": "Alpha", "rule": "alpha-*", "mission_ids": [MISSION, "m-alpha-done"]},
            {"key": "beta", "name": "Beta", "rule": "beta-*", "mission_ids": ["m-beta-queued"]},
        ],
        "missions": [running, finished, queued],
    }


def _groups(missions):
    groups = {}
    for m in missions:
        groups.setdefault(m["state_group"], []).append(m["mission_id"])
    return groups


def _plan(store):
    return {
        "version": store["version"],
        "plan": {"tasks": [{"id": t["task_id"], "dependencies": list(t["deps"])} for t in store["tasks"]]},
        "tasks": [
            {"task_id": t["task_id"], "title": t["title"], "status": t["status"], "attempts": t["attempts"]}
            for t in store["tasks"]
        ],
    }


def _apply_order(store, order):
    slots = [i for i, t in enumerate(store["tasks"]) if t["status"] == "waiting"]
    by_id = {t["task_id"]: t for t in store["tasks"]}
    waiting_ids = [store["tasks"][i]["task_id"] for i in slots]
    if sorted(order) != sorted(waiting_ids):
        return False
    tasks = list(store["tasks"])
    for slot, tid in zip(slots, order):
        tasks[slot] = by_id[tid]
    store["tasks"] = tasks
    store["version"] += 1
    return True


def _i18n_state():
    strings = {code: _read_json(SRC / f"board_i18n_{code}.json") for code in ("en", "ru")}
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
    mission = _read_json(FIXTURES / "mission.json")
    store = _new_store()
    i18n = _i18n_state()
    seen = []
    writes = []
    console_errors = []

    context = browser.new_context(viewport={"width": 1280, "height": 900})

    def handle(route):
        url = route.request.url
        seen.append(url)
        if not url.startswith(ORIGIN + "/"):
            return route.abort()
        method = route.request.method
        path = url[len(ORIGIN):].split("?", 1)[0]
        if method != "GET":
            writes.append((method, path, route.request.post_data or ""))
        if path == "/api/board/summary":
            return route.fulfill(json=_summary(store))
        if path == "/api/board/missions":
            summary = _summary(store)
            return route.fulfill(json={"groups": _groups(summary["missions"]), "missions": summary["missions"]})
        if path == "/api/board/queue":
            summary = _summary(store)
            return route.fulfill(json={
                "groups": _groups(summary["missions"]),
                "missions": [{"mission_id": m["mission_id"], "state_group": m["state_group"]}
                             for m in summary["missions"]],
                "queue": [{"mission_id": "m-beta-queued", "position": 0}],
                "version": "v1",
            })
        if path == "/api/board/i18n":
            return route.fulfill(json=i18n)
        if path.startswith("/api/board/i18n/"):
            return route.fulfill(status=405, json={"detail": "method not allowed"})
        if path.startswith("/api/board/missions/"):
            mid, _, sub = path[len("/api/board/missions/"):].partition("/")
            if sub == "" and method == "GET":
                if mid != MISSION:
                    return route.fulfill(status=404, json={"detail": "not found"})
                return route.fulfill(json=mission)
            if sub == "claudemd":
                return route.fulfill(json={"text": "", "source": "none"})
            if sub == "plan" and method == "GET":
                return route.fulfill(json=_plan(store))
            if sub == "tasks/reorder" and method == "POST":
                body = json.loads(route.request.post_data or "{}")
                if body.get("expected_version") != store["version"]:
                    return route.fulfill(status=409, json={"error": {"message": "plan changed"}})
                if not _apply_order(store, body.get("order", [])):
                    return route.fulfill(status=400, json={"error": {"message": "bad order"}})
                return route.fulfill(json={"ok": True, "running_warning": False})
            if sub.startswith("tasks/") and sub.endswith("/stats") and method == "GET":
                return route.fulfill(json=STATS)
            if sub.startswith("plan/tasks/") and sub.endswith("/dependencies"):
                return route.fulfill(status=409, json={"error": {"message": "not expected in this test"}})
            return route.fulfill(status=404, json={"detail": "not mocked"})
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

    b = types.SimpleNamespace(
        page=page, seen=seen, writes=writes, errors=console_errors, context=context, store=store,
    )
    yield b
    context.close()


def _open(b, fragment, ready, init_script=None):
    if init_script:
        b.context.add_init_script(init_script)
    b.page.goto(f"{ORIGIN}/{fragment}")
    b.page.wait_for_selector(ready, timeout=10_000)
    # Let the first summary fetch and the i18n load settle (each redraws once).
    b.page.wait_for_timeout(500)


def _wait_hash(b, fragment):
    b.page.wait_for_function(f"() => location.hash === {fragment!r}", timeout=10_000)


def _note(page, tid):
    return page.locator(f'#boardviewbody .ln-note[data-mid="{MISSION}"][data-tid="{tid}"]')


def _writes_to(b, suffix):
    return [w for w in b.writes if w[1].endswith(suffix)]


def _reorder_body_after(page, src, dst):
    with page.expect_request(
        lambda r: r.method == "POST" and r.url.endswith("/tasks/reorder"), timeout=10_000
    ) as info:
        _note(page, src).drag_to(_note(page, dst))
    return json.loads(info.value.post_data)


def _wait_waiting_order(page, expected):
    page.wait_for_function(
        """(want) => {
            const ids = [...document.querySelectorAll('#boardviewbody .lane[data-lane="%s"] .pend .ln-note')]
                .map((e) => e.dataset.tid);
            return ids.length === want.length && ids.every((x, i) => x === want[i]);
        }""" % MISSION,
        arg=expected,
        timeout=10_000,
    )


LANE_READY = f'#boardviewbody .lane[data-lane="{MISSION}"] .ln-note'


def test_lanes_drag_independent_task_posts_new_order(board):
    _open(board, "#board/view/lane", ready=LANE_READY)
    page = board.page
    body = _reorder_body_after(page, "T002", "T001")
    assert body["order"] == ["T002", "T001", "T003"]
    assert body["expected_version"] == 1
    assert _writes_to(board, f"/missions/{MISSION}/tasks/reorder")
    _wait_waiting_order(page, ["T002", "T001", "T003"])


def test_lanes_dependent_move_shows_warning_without_post(board):
    _open(board, "#board/view/lane", ready=LANE_READY)
    page = board.page
    _note(page, "T001").drag_to(_note(page, "T003"))
    page.wait_for_selector("#boardviewbody .warnbox", timeout=10_000)
    warning = page.locator("#boardviewbody .warnbox").first.text_content()
    assert "T001" in warning and "T003" in warning
    assert page.locator("#boardviewbody .ln-group").text_content() == "Move together"
    assert _writes_to(board, "/tasks/reorder") == []
    _wait_waiting_order(page, ["T001", "T002", "T003"])


def test_lanes_group_move_posts_the_group_result_order(board):
    _open(board, "#board/view/lane", ready=LANE_READY)
    page = board.page
    _note(page, "T001").drag_to(_note(page, "T003"))
    page.wait_for_selector("#boardviewbody .ln-group", timeout=10_000)
    # The group result for moving T001 with its dependent T003 down to T003's slot.
    expected = ["T002", "T001", "T003"]
    with page.expect_request(
        lambda r: r.method == "POST" and r.url.endswith("/tasks/reorder"), timeout=10_000
    ) as info:
        page.locator("#boardviewbody .ln-group").click()
    body = json.loads(info.value.post_data)
    assert body["order"] == expected
    _wait_waiting_order(page, expected)
    assert page.locator("#boardviewbody .warnbox").count() == 0


def test_lanes_warning_in_russian_uses_russian_strings(board):
    ru = _read_json(SRC / "board_i18n_ru.json")
    _open(
        board, "#board/view/lane", ready=LANE_READY,
        init_script="localStorage.setItem('board.lang', 'ru')",
    )
    page = board.page
    _note(page, "T001").drag_to(_note(page, "T003"))
    page.wait_for_selector("#boardviewbody .warnbox", timeout=10_000)
    assert page.locator("#boardviewbody .ln-group").text_content() == ru["btn.group"]
    assert _writes_to(board, "/tasks/reorder") == []


def test_mission_task_table_refuses_cycle_without_post(board):
    _open(
        board, f"#board/{MISSION}",
        ready='select.dep-select[data-task-id="T001"]',
    )
    page = board.page
    page.locator('select.dep-select[data-task-id="T001"]').select_option("T003")
    page.wait_for_selector(".lane-notice.bad", timeout=10_000)
    notice = page.locator(".lane-notice.bad").text_content()
    assert "Cycle" in notice and "T003" in notice and "T001" in notice
    assert _writes_to(board, "/dependencies") == []


def test_lanes_stats_line_for_accepted_and_waiting_tasks(board):
    _open(board, "#board/view/lane", ready=LANE_READY)
    page = board.page
    page.wait_for_function(
        """() => {
            const e = document.querySelector('#boardviewbody .ln-note[data-tid="T000"] .ln-stats');
            return !!e && e.textContent.includes('15K tok') && e.textContent.includes('cache 25%');
        }""",
        timeout=10_000,
    )
    assert page.locator(f'#boardviewbody .ln-note[data-tid="T001"] .ln-stats').text_content() == "not started yet"


def test_dblclick_project_opens_kanban_filtered_to_project(board):
    _open(board, "#board/view/proj", ready='.bv-card.pr[data-project="alpha"]')
    page = board.page
    page.locator('.bv-card.pr[data-project="alpha"]').dblclick()
    _wait_hash(board, "#board/view/kan?project=alpha")
    page.wait_for_selector(".kb .kcard", timeout=10_000)
    projects = page.locator(".kb .kcard").evaluate_all("els => els.map(e => e.dataset.project)")
    assert projects and set(projects) == {"alpha"}


def test_dblclick_mission_inside_project_opens_mission_screen(board):
    _open(board, "#board/view/proj", ready='.bv-card.pr[data-project="alpha"]')
    page = board.page
    page.locator('.pr[data-project="alpha"] .arr').click()
    page.wait_for_selector('.pr[data-project="alpha"] .prb .kc[data-m="m-alpha-run"]', timeout=5_000)
    page.locator('.pr[data-project="alpha"] .kc[data-m="m-alpha-run"]').dblclick()
    _wait_hash(board, f"#board/{MISSION}")
    page.wait_for_selector("#boardmission", timeout=10_000)


def test_dblclick_kanban_card_opens_runs_dashboard_with_mission_tag(board):
    _open(board, "#board/view/kan", ready=".kb .kcard")
    page = board.page
    page.locator(f'.kcard[data-mission-id="{MISSION}"]').dblclick()
    page.wait_for_function(
        f"() => location.hash.startsWith('#mission={MISSION}&from=')", timeout=10_000,
    )
    assert page.evaluate("() => location.hash") == f"#mission={MISSION}&from=board%2Fview%2Fkan"


def test_language_switch_en_and_ru_in_lanes_view(board):
    en = _read_json(SRC / "board_i18n_en.json")
    ru = _read_json(SRC / "board_i18n_ru.json")
    _open(board, "#board/view/lane", ready=LANE_READY)
    page = board.page
    page.locator(".bv-lang").select_option("ru")
    page.wait_for_function(
        f"() => {{ const s = document.querySelector('#boardviewbody .sub');"
        f" return !!s && s.textContent === {json.dumps(ru['lane.hint'])}; }}",
        timeout=10_000,
    )
    page.wait_for_function("() => localStorage.getItem('board.lang') === 'ru'", timeout=5_000)
    page.locator(".bv-lang").select_option("en")
    page.wait_for_function(
        f"() => {{ const s = document.querySelector('#boardviewbody .sub');"
        f" return !!s && s.textContent === {json.dumps(en['lane.hint'])}; }}",
        timeout=10_000,
    )
    page.wait_for_function("() => localStorage.getItem('board.lang') === 'en'", timeout=5_000)


def test_no_console_errors_and_no_request_to_live_dashboard(board):
    _open(board, "#board/view/lane", ready=LANE_READY)
    board.page.wait_for_timeout(300)
    assert board.errors == [], f"console errors: {board.errors}"
    forbidden = [u for u in board.seen if f":{FORBIDDEN_PORT}" in u]
    assert forbidden == [], f"requests reached the live dashboard port: {forbidden}"
    assert [w for w in board.writes if w[0] != "GET"] == [], "unexpected write during page load"
