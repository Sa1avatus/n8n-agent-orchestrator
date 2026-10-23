from __future__ import annotations

import importlib
import json
import sys
import time
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

from claude_runner import qa_runner, qa_stack
from claude_runner.qa_runner import ScenarioError, build_verdict, load_scenario, parse_scenario

VALID = """
name: queue drag keeps order
tags: [ui, queue]
preconditions:
  fixture: default
steps:
  - open: /board
    expect:
      text_visible: [Queue]
      request_2xx: [/api/board/queue]
  - drag:
      from: Task A
      onto: Task B
    expect:
      order: [Task B, Task A]
      no_mutation_without_header: true
  - type:
      into: Search
      text: alpha
  - press: Enter
  - wait_for_text: Done
  - switch_language: ru
    expect:
      no_console_error: true
  - click: Save
reload_persistence: true
"""


def test_valid_scenario_parses_every_field() -> None:
    scenario = parse_scenario(VALID)

    assert scenario.name == "queue drag keeps order"
    assert scenario.tags == ("ui", "queue")
    assert scenario.fixture == "default"
    assert scenario.reload_persistence is True
    actions = [step.action for step in scenario.steps]
    assert actions == ["open", "drag", "type", "press", "wait_for_text", "switch_language", "click"]
    open_step, drag_step = scenario.steps[0], scenario.steps[1]
    assert open_step.value == "/board"
    assert open_step.expect.text_visible == ("Queue",)
    assert open_step.expect.request_2xx == ("/api/board/queue",)
    assert drag_step.value == ("Task A", "Task B")
    assert drag_step.expect.order == ("Task B", "Task A")
    assert drag_step.expect.no_mutation_without_header is True
    assert scenario.steps[2].value == ("Search", "alpha")
    assert scenario.steps[5].value == "ru"
    assert scenario.steps[5].expect.no_console_error is True


API_AND_BELOW = """
name: conflict and drop below
tags: [queue]
preconditions:
  fixture: default
steps:
  - api_post:
      path: /api/board/queue/move
      body: {mission_id: m03, to_position: 0}
  - drag:
      from: Parser rewrite
      onto: Metrics
      below: true
"""


def test_api_post_and_drag_below_parse_their_fields() -> None:
    scenario = parse_scenario(API_AND_BELOW)

    post_step, drag_step = scenario.steps
    assert post_step.action == "api_post"
    assert post_step.value == "/api/board/queue/move"
    assert post_step.body == {"mission_id": "m03", "to_position": 0}
    assert drag_step.value == ("Parser rewrite", "Metrics")
    assert drag_step.below is True


@pytest.mark.parametrize(
    ("step", "field"),
    [
        ("api_post: {path: /board/queue/move}", "steps[0].api_post.path"),
        ("api_post: {path: 'http://evil.test/api/board'}", "steps[0].api_post.path"),
        ("api_post: {path: /api/board, body: [1]}", "steps[0].api_post.body"),
        ("api_post: {path: /api/board, headers: {}}", "steps[0].api_post"),
        ("drag: {from: A, onto: B, below: yes_please}", "steps[0].drag.below"),
        ("drag: {from: A, onto: B, side: left}", "steps[0].drag"),
    ],
)
def test_invalid_api_post_and_drag_name_the_bad_field(step: str, field: str) -> None:
    text = "name: x\ntags: [ui]\npreconditions: {fixture: a}\nsteps: [{" + step + "}]\n"
    with pytest.raises(ScenarioError) as info:
        parse_scenario(text)

    assert field in str(info.value)


def test_optional_fields_take_defaults() -> None:
    scenario = parse_scenario(
        "name: smoke\ntags: [nightly]\npreconditions: {fixture: empty}\nsteps:\n  - open: /\n"
    )

    assert scenario.reload_persistence is False
    assert scenario.steps[0].expect == qa_runner.Expect()


@pytest.mark.parametrize(
    ("text", "field"),
    [
        ("tags: [ui]\npreconditions: {fixture: a}\nsteps: [{open: /}]\n", "name"),
        ("name: x\ntags: [billing]\npreconditions: {fixture: a}\nsteps: [{open: /}]\n", "tags[0]"),
        ("name: x\ntags: []\npreconditions: {fixture: a}\nsteps: [{open: /}]\n", "tags"),
        ("name: x\ntags: [ui]\nsteps: [{open: /}]\n", "preconditions"),
        ("name: x\ntags: [ui]\npreconditions: {}\nsteps: [{open: /}]\n", "preconditions.fixture"),
        (
            "name: x\ntags: [ui]\npreconditions: {fixture: ../secret}\nsteps: [{open: /}]\n",
            "preconditions.fixture",
        ),
        ("name: x\ntags: [ui]\npreconditions: {fixture: a}\nsteps: []\n", "steps"),
        ("name: x\ntags: [ui]\npreconditions: {fixture: a}\nsteps: [{}]\n", "steps[0]"),
        (
            "name: x\ntags: [ui]\npreconditions: {fixture: a}\nsteps: [{open: /, click: Save}]\n",
            "steps[0]",
        ),
        (
            "name: x\ntags: [ui]\npreconditions: {fixture: a}\nsteps: [{fly: here}]\n",
            "steps[0]",
        ),
        (
            "name: x\ntags: [ui]\npreconditions: {fixture: a}\nsteps: [{drag: {from: A}}]\n",
            "steps[0].drag.onto",
        ),
        (
            "name: x\ntags: [ui]\npreconditions: {fixture: a}\nsteps: [{switch_language: de}]\n",
            "steps[0].switch_language",
        ),
        (
            "name: x\ntags: [ui]\npreconditions: {fixture: a}\n"
            "steps: [{open: /, expect: {no_console_error: yes_please}}]\n",
            "steps[0].expect.no_console_error",
        ),
        (
            "name: x\ntags: [ui]\npreconditions: {fixture: a}\n"
            "steps: [{open: /, expect: {order: Queue}}]\n",
            "steps[0].expect.order",
        ),
        (
            "name: x\ntags: [ui]\npreconditions: {fixture: a}\n"
            "steps: [{open: /, expect: {bogus: 1}}]\n",
            "steps[0].expect",
        ),
        (
            "name: x\ntags: [ui]\npreconditions: {fixture: a}\nsteps: [{open: /}]\nextra: 1\n",
            "extra",
        ),
        ("name: [\n", "invalid YAML"),
    ],
)
def test_invalid_scenarios_name_the_bad_field(text: str, field: str) -> None:
    with pytest.raises(ScenarioError) as info:
        parse_scenario(text)

    assert field in str(info.value)


def test_load_scenario_names_the_file(tmp_path: Path) -> None:
    path = tmp_path / "broken.yaml"
    path.write_text("name: x\ntags: [billing]\npreconditions: {fixture: a}\nsteps: []\n")

    with pytest.raises(ScenarioError, match=r"broken\.yaml: tags\[0\]"):
        load_scenario(path)


def test_verdict_is_blocked_when_stack_is_blocked_even_if_clean() -> None:
    verdict = build_verdict(scenario="s", blocked_reason="qa database is down")

    assert verdict["verdict"] == "blocked"
    assert verdict["verdict"] != "pass"


def test_verdict_stays_blocked_when_steps_also_failed() -> None:
    verdict = build_verdict(
        scenario="s",
        blocked_reason="stack unhealthy",
        failed_step=2,
        console_errors=["TypeError: x"],
    )

    assert verdict["verdict"] == "blocked"


def test_verdict_passes_only_for_clean_run() -> None:
    verdict = build_verdict(scenario="s", screenshots=["/shots/1.png"], minutes=1.5, cost_usd=0.02)

    assert verdict["verdict"] == "pass"
    assert verdict["failed_step"] is None
    assert verdict["screenshots"] == ["/shots/1.png"]
    assert verdict["minutes"] == 1.5
    assert verdict["cost_usd"] == 0.02


def test_verdict_fails_on_failed_step() -> None:
    verdict = build_verdict(scenario="s", failed_step=3, failed_reason="text not visible")

    assert verdict["verdict"] == "fail"
    assert verdict["failed_step"] == 3
    assert verdict["failed_reason"] == "text not visible"


def test_verdict_fails_on_console_or_network_error_without_failed_step() -> None:
    assert build_verdict(scenario="s", console_errors=["boom"])["verdict"] == "fail"
    assert build_verdict(scenario="s", network_errors=["500 /api"])["verdict"] == "fail"


def test_verdict_is_json_serialisable() -> None:
    verdict = build_verdict(scenario="s", failed_step=0, screenshots=["a.png"])

    assert json.loads(json.dumps(verdict)) == verdict


def test_verdict_masks_dsn_in_logs_and_paths() -> None:
    dsn = "postgresql://qa_user:hunter2pass@db.internal:5432/qa"
    verdict = build_verdict(
        scenario="s",
        blocked_reason=f"cannot connect to {dsn}",
        console_errors=[f"connect failed: {dsn}"],
        screenshots=[f"/shots/{dsn}.png"],
    )

    text = json.dumps(verdict)
    assert "hunter2pass" not in text
    assert "qa_user" not in text
    assert "db.internal" not in text
    assert "***" in verdict["blocked_reason"]


def test_verdict_masks_token_like_values() -> None:
    api_key = "sk-" + "a1B2c3D4e5F6g7H8i9J0"
    bot_token = "123456789:" + "A" * 35
    bearer = "Bearer " + "x" * 24
    verdict = build_verdict(
        scenario="s",
        failed_step=1,
        failed_reason=f"used {api_key}",
        network_errors=[f"401 {bearer}", f"TELEGRAM_BOT_TOKEN={bot_token}"],
    )

    text = json.dumps(verdict)
    assert api_key not in text
    assert bearer not in text
    assert bot_token not in text
    assert "TELEGRAM_BOT_TOKEN=***" in verdict["network_errors"][1]
    assert verdict["failed_reason"] == "used ***"


def test_module_imports_without_playwright(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "playwright", None)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", None)
    monkeypatch.delitem(sys.modules, "claude_runner.qa_runner", raising=False)

    module = importlib.import_module("claude_runner.qa_runner")

    assert module.build_verdict is not None


BASE_URL = "http://127.0.0.1:8123"


def test_type_without_replace_appends_text() -> None:
    page = _FakePage()
    step = qa_runner.Step("type", ("Search", "alpha"), qa_runner.Expect())

    qa_runner._perform(page, step, BASE_URL)

    assert page.typed == [("Search", "alpha")]
    assert page.select_all_calls == 0


def test_type_with_replace_clears_before_typing() -> None:
    page = _FakePage()
    step = qa_runner.Step("type", ("Search", "alpha"), qa_runner.Expect(), replace=True)

    qa_runner._perform(page, step, BASE_URL)

    assert page.select_all_calls == 1
    assert page.typed == [("Search", "alpha")]


def test_type_rejects_unknown_fields() -> None:
    text = (
        "name: x\ntags: [ui]\npreconditions: {fixture: a}\n"
        'steps: [{type: {into: Search, text: alpha, bogus: 1}}]\n'
    )
    with pytest.raises(ScenarioError, match="unknown field"):
        parse_scenario(text)


def test_type_replace_rejects_non_bool() -> None:
    text = (
        "name: x\ntags: [ui]\npreconditions: {fixture: a}\n"
        'steps: [{type: {into: Search, text: alpha, replace: yes_please}}]\n'
    )
    with pytest.raises(ScenarioError, match="expected true or false"):
        parse_scenario(text)


def test_api_post_sends_dashboard_header_to_the_stack_only() -> None:
    page = _FakePage()
    step = qa_runner.Step(
        "api_post",
        "/api/board/queue/move",
        qa_runner.Expect(),
        body={"mission_id": "m03", "to_position": 0},
    )

    qa_runner._perform(page, step, BASE_URL)

    assert page.request.posts == [
        (
            BASE_URL + "/api/board/queue/move",
            {"X-AHAWR-Dashboard": "1", "Content-Type": "application/json"},
            '{"mission_id": "m03", "to_position": 0}',
        )
    ]


def test_api_post_error_status_fails_the_step() -> None:
    page = _FakePage(api_status=409)
    step = qa_runner.Step("api_post", "/api/board/queue/move", qa_runner.Expect())

    with pytest.raises(qa_runner.StepFailed, match="answered 409"):
        qa_runner._perform(page, step, BASE_URL)


def test_drag_without_below_drops_on_the_target_name() -> None:
    page = _FakePage()
    step = qa_runner.Step("drag", ("Cache layer", "Parser rewrite"), qa_runner.Expect())

    qa_runner._perform(page, step, BASE_URL)

    assert page.drags == [("Cache layer", "Parser rewrite", qa_runner.VISIBLE_ONLY, None)]


def test_drag_below_drops_on_the_lower_part_of_the_target_card() -> None:
    page = _FakePage()
    step = qa_runner.Step(
        "drag", ("Parser rewrite", "Metrics"), qa_runner.Expect(), below=True
    )

    qa_runner._perform(page, step, BASE_URL)

    source, target, scope, position = page.drags[0]
    assert (source, target, scope) == ("Parser rewrite", "Metrics", qa_runner.CARD_ANCESTOR)
    assert position is not None
    assert position["x"] == pytest.approx(100.0)
    assert position["y"] == pytest.approx(85.0)


def test_drag_below_fails_when_the_target_card_is_not_on_the_page() -> None:
    page = _FakePage()
    page.hidden.add("Metrics")
    step = qa_runner.Step(
        "drag", ("Parser rewrite", "Metrics"), qa_runner.Expect(), below=True
    )

    with pytest.raises(qa_runner.StepFailed, match="Metrics"):
        qa_runner._perform(page, step, BASE_URL)
    assert page.drags == []


SCENARIO_TEXT = (
    "name: board\ntags: [ui]\npreconditions: {fixture: default}\n"
    "steps:\n  - open: /board\n    expect:\n      text_visible: [Queue]\n"
    "reload_persistence: true\n"
)


class _Locator:
    def __init__(self, page: _FakePage, text: str, scope: str = "") -> None:
        self._page = page
        self._text = text
        self._scope = scope

    @property
    def first(self) -> _Locator:
        return self

    def locator(self, selector: str) -> _Locator:
        return _Locator(self._page, self._text, scope=selector)

    def wait_for(self, state: str, timeout: float) -> None:
        if self._text in self._page.hidden:
            raise RuntimeError(f"{self._text} is not visible")

    def bounding_box(self) -> dict[str, float] | None:
        if self._text in self._page.hidden:
            return None
        return {"x": 0.0, "y": 0.0, "width": 200.0, "height": 100.0}

    def click(self) -> None:
        self._page.clicks.append(self._text)

    def drag_to(self, target: _Locator, target_position: dict[str, float] | None = None) -> None:
        self._page.drags.append((self._text, target._text, target._scope, target_position))

    def or_(self, other: _Locator) -> _Locator:
        return self

    def fill(self, text: str) -> None:
        assert text == ""
        self._page.select_all_calls += 1

    def press_sequentially(self, text: str) -> None:
        self._page.typed.append((self._text, text))


class _PlaceholderLocator:
    def __init__(self, found: int) -> None:
        self._found = found

    def count(self) -> int:
        return self._found


class _FakeApiResponse:
    def __init__(self, status: int) -> None:
        self.status = status
        self.ok = 200 <= status < 300


class _FakeRequestContext:
    def __init__(self, status: int) -> None:
        self.status = status
        self.posts: list[tuple[str, dict[str, str], str]] = []

    def post(self, url: str, headers: dict[str, str], data: str) -> _FakeApiResponse:
        self.posts.append((url, headers, data))
        return _FakeApiResponse(self.status)


class _FakePage:
    def __init__(self, hidden_after_reload: set[str] | None = None, api_status: int = 200) -> None:
        self.hidden: set[str] = set()
        self.hidden_after_reload = set(hidden_after_reload or ())
        self.clicks: list[str] = []
        self.drags: list[tuple[str, str, str, dict[str, float] | None]] = []
        self.handlers: dict[str, Any] = {}
        self.placeholders: set[str] = set()
        self.request = _FakeRequestContext(api_status)
        self.reloads = 0
        self.shots: list[str] = []
        self.visited = ""
        self.typed: list[tuple[str, str]] = []
        self.select_all_calls = 0

    def on(self, event: str, handler: Any) -> None:
        self.handlers[event] = handler

    def goto(self, url: str) -> None:
        self.visited = url

    def get_by_text(self, text: str, exact: bool = False) -> _Locator:
        return _Locator(self, text)

    def get_by_role(self, role: str, name: str) -> _Locator:
        return _Locator(self, name)

    def locator(self, selector: str) -> _PlaceholderLocator:
        matches = [p for p in self.placeholders if f'placeholder="{p}"' in selector]
        return _PlaceholderLocator(len(matches))

    def reload(self) -> None:
        self.reloads += 1
        self.hidden = set(self.hidden_after_reload)

    def wait_for_load_state(self, state: str, timeout: float) -> None:
        return None

    def screenshot(self, path: str) -> None:
        self.shots.append(path)
        Path(path).write_bytes(b"png")


def test_run_is_blocked_without_playwright_and_starts_no_stack(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setitem(sys.modules, "playwright", None)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", None)
    monkeypatch.setattr(qa_stack, "stack_up", _stack_must_not_start)

    verdict = qa_runner.run_browser_scenario(parse_scenario(SCENARIO_TEXT), runs_dir=tmp_path)

    assert verdict["verdict"] == "blocked"
    assert "pip install -e '.[qa]'" in verdict["blocked_reason"]
    assert list(tmp_path.iterdir()) == []


def test_stack_start_failure_is_blocked(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    playwright = ModuleType("playwright")
    sync_api = ModuleType("playwright.sync_api")
    vars(sync_api)["sync_playwright"] = lambda: None
    monkeypatch.setitem(sys.modules, "playwright", playwright)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", sync_api)

    def failing_up(fixture: str = "default", mode: str = "docker") -> qa_stack.QaStack:
        raise qa_stack.QaStackError("docker build failed")

    monkeypatch.setattr(qa_stack, "stack_up", failing_up)

    verdict = qa_runner.run_browser_scenario(parse_scenario(SCENARIO_TEXT), runs_dir=tmp_path)

    assert verdict["verdict"] == "blocked"
    assert verdict["blocked_reason"] == "QA stack did not start: docker build failed"
    run_dirs = [path.name for path in tmp_path.iterdir()]
    assert len(run_dirs) == 1 and run_dirs[0].startswith("qa-")
    assert json.loads((tmp_path / run_dirs[0] / "verdict.json").read_text())["verdict"] == "blocked"


def test_run_prints_the_stack_id_before_tearing_it_down(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capfd: pytest.CaptureFixture
) -> None:
    playwright = ModuleType("playwright")
    sync_api = ModuleType("playwright.sync_api")
    vars(sync_api)["sync_playwright"] = lambda: None
    monkeypatch.setitem(sys.modules, "playwright", playwright)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", sync_api)

    stack = qa_stack.QaStack(
        run_id="0a1b2c3d",
        host="qa-0a1b2c3d",
        database="qa_0a1b2c3d",
        dsn="",
        dashboard_url="http://127.0.0.1:1",
    )
    monkeypatch.setattr(qa_stack, "stack_up", lambda fixture="default", mode="docker": stack)
    monkeypatch.setattr(qa_stack, "stack_down", lambda _stack: None)

    def drive(*_args: object) -> tuple[Any, Any]:
        return qa_runner._Progress(blocked_reason="browser not launched"), qa_runner._Recorder()

    monkeypatch.setattr(qa_runner, "_drive", drive)

    verdict = qa_runner.run_browser_scenario(parse_scenario(SCENARIO_TEXT), runs_dir=tmp_path)

    assert "stack id: 0a1b2c3d" in capfd.readouterr().err
    assert "postgresql://" not in capfd.readouterr().err
    assert verdict["verdict"] == "blocked"


def test_reload_recheck_fails_when_text_is_gone_after_reload(tmp_path: Path) -> None:
    scenario = parse_scenario(SCENARIO_TEXT)
    page = _FakePage(hidden_after_reload={"Queue"})

    progress = qa_runner._execute(
        scenario, page, qa_runner._Recorder(), "http://127.0.0.1:1", tmp_path, float("inf")
    )

    assert page.reloads == 1
    assert progress.failed_step == 1
    assert "Queue is not visible" in progress.failed_reason


def test_reload_recheck_uses_only_the_last_step_expectation(tmp_path: Path) -> None:
    text = (
        "name: board\ntags: [ui]\npreconditions: {fixture: default}\n"
        "steps:\n  - open: /board\n    expect:\n      text_visible: [Queue]\n"
        "  - open: /board\n    expect:\n      text_visible: [Board]\n"
        "reload_persistence: true\n"
    )
    scenario = parse_scenario(text)
    page = _FakePage(hidden_after_reload={"Queue"})

    progress = qa_runner._execute(
        scenario, page, qa_runner._Recorder(), "http://127.0.0.1:1", tmp_path, float("inf")
    )

    assert page.reloads == 1
    assert progress.failed_step is None


def test_reload_recheck_is_skipped_without_reload_persistence(tmp_path: Path) -> None:
    scenario = parse_scenario(SCENARIO_TEXT.replace("reload_persistence: true\n", ""))
    page = _FakePage(hidden_after_reload={"Queue"})

    progress = qa_runner._execute(
        scenario, page, qa_runner._Recorder(), "http://127.0.0.1:1", tmp_path, float("inf")
    )

    assert page.reloads == 0
    assert progress.failed_step is None


def test_mutation_without_dashboard_header_is_recorded() -> None:
    recorder = qa_runner._Recorder()
    unheadered = SimpleNamespace(
        method="POST", url="http://127.0.0.1:9/api/board/queue", headers={}
    )
    headered = SimpleNamespace(
        method="PUT",
        url="http://127.0.0.1:9/api/board/queue",
        headers={"x-ahawr-dashboard": "1"},
    )
    reads = SimpleNamespace(method="GET", url="http://127.0.0.1:9/api/board/queue", headers={})

    for request in (unheadered, headered, reads):
        qa_runner._check_header("http://127.0.0.1:9", recorder, request)

    assert recorder.network_errors == ["POST /api/board/queue without X-AHAWR-Dashboard header"]


def test_no_mutation_without_header_expectation_fails_on_new_violation() -> None:
    recorder = qa_runner._Recorder()
    mark = qa_runner._mark(recorder)
    recorder.network_errors.append("DELETE /api/board/task without X-AHAWR-Dashboard header")
    expect = qa_runner.Expect(no_mutation_without_header=True)

    with pytest.raises(qa_runner.StepFailed, match="DELETE /api/board/task"):
        qa_runner._check(_FakePage(), expect, recorder, mark)


def test_screenshots_are_written_only_to_the_run_dir(tmp_path: Path) -> None:
    run_dir = tmp_path / "qa-0a1b2c3d"
    run_dir.mkdir()
    page = _FakePage()
    progress = qa_runner._Progress()

    qa_runner._keep_screenshot(page, run_dir, "final.png", progress)

    assert page.shots == [str(run_dir / "final.png")]
    assert progress.screenshots == ["qa-0a1b2c3d/final.png"]


def test_deadline_passed_before_a_step_is_blocked(tmp_path: Path) -> None:
    progress = qa_runner._execute(
        parse_scenario(SCENARIO_TEXT),
        _FakePage(),
        qa_runner._Recorder(),
        "http://127.0.0.1:1",
        tmp_path,
        time.monotonic() - 1,
    )

    assert progress.blocked_reason == "max_minutes exceeded before step 0"


def test_load_scenarios_rejects_empty_directory(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="no scenario files"):
        qa_runner.load_scenarios(tmp_path)


def test_every_shipped_scenario_parses_with_load_scenario() -> None:
    paths = sorted(qa_runner.SCENARIO_DIR.glob("*.yaml"))

    assert len(paths) >= 3
    for path in paths:
        assert qa_runner.load_scenario(path).steps, path.name


def test_attached_page_accepts_confirm_dialogs() -> None:
    class _Dialog:
        accepted = False

        def accept(self) -> None:
            self.accepted = True

    page = _FakePage()
    qa_runner._attach(page, "http://127.0.0.1:1", qa_runner._Recorder())
    dialog = _Dialog()

    page.handlers["dialog"](dialog)

    assert dialog.accepted


def test_placeholder_expectation_passes_when_the_field_has_it() -> None:
    page = _FakePage()
    page.placeholders.add("example-large")
    expect = qa_runner.Expect(placeholder_visible=("example-large",))

    qa_runner._check(page, expect, qa_runner._Recorder(), qa_runner._mark(qa_runner._Recorder()))


def test_placeholder_expectation_fails_when_no_field_has_it() -> None:
    page = _FakePage()
    page.placeholders.add("example-small")
    recorder = qa_runner._Recorder()
    expect = qa_runner.Expect(placeholder_visible=("example-large",))

    with pytest.raises(qa_runner.StepFailed, match="placeholder 'example-large'"):
        qa_runner._check(page, expect, recorder, qa_runner._mark(recorder))


def test_placeholder_expectation_is_parsed_from_the_step() -> None:
    expect = qa_runner._expect({"placeholder_visible": ["example-large"]}, "steps[0].expect")

    assert expect.placeholder_visible == ("example-large",)


def test_placeholder_expectation_rejects_a_double_quote() -> None:
    with pytest.raises(qa_runner.ScenarioError, match="double quote"):
        qa_runner._expect({"placeholder_visible": ['a"b']}, "steps[0].expect")


def test_settings_scenario_checks_the_worker_model_placeholder_on_execution() -> None:
    scenario = qa_runner.load_scenario(qa_runner.SCENARIO_DIR / "settings_launch_window.yaml")
    execution = scenario.steps[2]

    assert execution.expect.text_visible == ("Execution", "Roles")
    assert execution.expect.placeholder_visible == ("example-large",)


def test_select_scenarios_rejects_no_match() -> None:
    scenarios = [parse_scenario(SCENARIO_TEXT)]

    assert qa_runner.select_scenarios(scenarios, tag="ui") == scenarios
    with pytest.raises(ValueError, match="no scenario matches"):
        qa_runner.select_scenarios(scenarios, tag="nightly")


def _stack_must_not_start(fixture: str = "default", mode: str = "docker") -> qa_stack.QaStack:
    raise AssertionError("the QA stack must not start without Playwright")


def test_freeze_refresh_and_allowed_status_are_read_from_the_scenario() -> None:
    scenario = parse_scenario(
        SCENARIO_TEXT + "allow_http_status: [409]\nfreeze_refresh: true\n"
    )

    assert scenario.allow_http_status == (409,)
    assert scenario.freeze_refresh is True


def test_an_unlisted_http_status_is_refused() -> None:
    with pytest.raises(ScenarioError, match="allow_http_status"):
        parse_scenario(SCENARIO_TEXT + "allow_http_status: [500]\n")
