from __future__ import annotations

import importlib.util
import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from claude_runner import qa, qa_api, qa_regression, qa_runner, qa_smoke, qa_stack, qa_worker
from claude_runner.board import InMemoryBoardStore
from claude_runner.qa_runner import parse_scenario
from claude_runner.qa_stack import QaStack, QaStackError

STACK_ID = "0a1b2c3d"
PASSWORD = "s3cr3t-Pw_for_test"
DSN = f"postgresql://qa:{PASSWORD}@qa-{STACK_ID}:5432/qa_{STACK_ID}"
ROLES_JSON = '{"qa": {"1": "anthropic/sonnet", "options": {"mode": "final", "smoke": true}}}'


def _stack() -> QaStack:
    return QaStack(
        run_id=STACK_ID,
        host=f"qa-{STACK_ID}",
        database=f"qa_{STACK_ID}",
        dsn=DSN,
        dashboard_url="http://127.0.0.1:49152",
    )


def test_stack_help_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        qa.main(["stack", "--help"])
    assert exit_info.value.code == 0
    assert "up" in capsys.readouterr().out


def test_stack_up_defaults_to_default_fixture(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def fake_up(fixture: str = "default") -> QaStack:
        calls.append(fixture)
        return _stack()

    monkeypatch.setattr(qa_stack, "stack_up", fake_up)
    assert qa.main(["stack", "up"]) == 0
    assert calls == ["default"]


def test_stack_up_passes_empty_fixture(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def fake_up(fixture: str = "default") -> QaStack:
        calls.append(fixture)
        return _stack()

    monkeypatch.setattr(qa_stack, "stack_up", fake_up)
    assert qa.main(["stack", "up", "--fixture", "empty"]) == 0
    assert calls == ["empty"]


def test_stack_up_rejects_unknown_fixture(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(qa_stack, "stack_up", _unexpected_call)
    with pytest.raises(SystemExit) as exit_info:
        qa.main(["stack", "up", "--fixture", "nightly"])
    assert exit_info.value.code == 2
    assert "invalid choice" in capsys.readouterr().err


def test_stack_up_prints_dashboard_url_and_stack_id_never_dsn(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(qa_stack, "stack_up", lambda fixture="default": _stack())
    assert qa.main(["stack", "up"]) == 0
    output = capsys.readouterr()
    assert output.out == f"dashboard: http://127.0.0.1:49152\nstack id: {STACK_ID}\n"
    assert PASSWORD not in output.out + output.err
    assert "postgresql://" not in output.out + output.err


def test_stack_up_error_is_reported_without_dsn(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def failing_up(fixture: str = "default") -> QaStack:
        raise QaStackError("docker build failed with exit code 1: no space left")

    monkeypatch.setattr(qa_stack, "stack_up", failing_up)
    assert qa.main(["stack", "up"]) == 1
    output = capsys.readouterr()
    assert "no space left" in output.err
    assert PASSWORD not in output.out + output.err
    assert "postgresql://" not in output.out + output.err


def test_stack_down_removes_objects_named_from_stack_id(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    removed: list[QaStack] = []
    monkeypatch.setattr(qa_stack, "stack_down", removed.append)
    assert qa.main(["stack", "down", STACK_ID]) == 0
    assert len(removed) == 1
    stack = removed[0]
    assert stack.run_id == STACK_ID
    assert stack.containers == [f"qa-{STACK_ID}", f"qa-{STACK_ID}-dashboard"]
    assert stack.volumes == [f"qa-{STACK_ID}-data"]
    assert stack.networks == [f"qa-{STACK_ID}-net"]
    assert stack.images == [f"qa-dashboard:{STACK_ID}"]
    assert "removed" in capsys.readouterr().out


@pytest.mark.parametrize("bad_id", ["../x", "qa-0a1b2c3d", "0A1B2C3D", "0a1b2c3"])
def test_stack_down_rejects_malformed_stack_id(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], bad_id: str
) -> None:
    monkeypatch.setattr(qa_stack, "stack_down", _unexpected_call)
    assert qa.main(["stack", "down", bad_id]) == 1
    assert "not 8 lowercase hex digits" in capsys.readouterr().err


def test_stack_down_failure_returns_one(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def failing_down(stack: QaStack) -> None:
        raise QaStackError(f"teardown of QA stack {stack.run_id} incomplete: rm failed")

    monkeypatch.setattr(qa_stack, "stack_down", failing_down)
    assert qa.main(["stack", "down", STACK_ID]) == 1
    assert "incomplete" in capsys.readouterr().err


def test_command_is_required(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        qa.main([])
    assert exit_info.value.code == 2
    assert "required" in capsys.readouterr().err


def _scenario_text(name: str, tag: str) -> str:
    return (
        f"name: {name}\ntags: [{tag}]\npreconditions: {{fixture: default}}\nsteps: [{{open: /}}]\n"
    )


def _verdict(name: str, value: str) -> dict[str, Any]:
    return {"scenario": name, "verdict": value}


def _roles_store() -> InMemoryBoardStore:
    store = InMemoryBoardStore()
    store.update_hermes_config(None, roles_json=ROLES_JSON)
    return store


def test_qa_options_reads_smoke_from_the_roles_config() -> None:
    assert qa._qa_options(_roles_store())["smoke"] is True


def test_qa_options_returns_the_qa_role_options() -> None:
    options = qa._qa_options(_roles_store())
    assert options["smoke"] is True
    assert options["mode"] == "final"


def test_qa_options_returns_defaults_when_the_roles_config_has_no_qa() -> None:
    store = _roles_store()
    store.update_hermes_config(None, roles_json="{}")
    options = qa._qa_options(store)
    assert "smoke" not in options
    assert options == {"fresh": "auto", "mode": "off", "max_minutes": 20, "max_cost_usd": 1}


def test_qa_options_is_empty_when_the_config_text_is_not_json(
    caplog: pytest.LogCaptureFixture,
) -> None:
    store = _roles_store()
    store.update_hermes_config(None, roles_json="not json")
    assert qa._qa_options(store) == {}
    assert "qa options not read" in caplog.records[-1].message


def test_run_exits_zero_when_every_verdict_passes(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    scenarios = [parse_scenario(_scenario_text("board", "ui"))]
    monkeypatch.setattr(qa_runner, "load_scenarios", lambda: scenarios)
    monkeypatch.setattr(
        qa_runner, "run_browser_scenario", lambda scenario, **options: _verdict("board", "pass")
    )

    assert qa.main(["run"]) == 0
    assert json.loads(capsys.readouterr().out) == [_verdict("board", "pass")]


def test_run_exits_one_on_blocked_verdict(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    scenarios = [parse_scenario(_scenario_text("board", "ui"))]
    monkeypatch.setattr(qa_runner, "load_scenarios", lambda: scenarios)
    blocked = _verdict("board", "blocked")
    monkeypatch.setattr(qa_runner, "run_browser_scenario", lambda scenario, **options: blocked)

    assert qa.main(["run"]) == 1
    assert json.loads(capsys.readouterr().out) == [blocked]


def test_run_filters_by_tag_and_scenario_name(monkeypatch: pytest.MonkeyPatch) -> None:
    scenarios = [
        parse_scenario(_scenario_text("board", "ui")),
        parse_scenario(_scenario_text("nightly-sweep", "nightly")),
    ]
    ran: list[str] = []

    def record(scenario: Any, **options: Any) -> dict[str, Any]:
        ran.append(scenario.name)
        return _verdict(scenario.name, "pass")

    monkeypatch.setattr(qa_runner, "load_scenarios", lambda: scenarios)
    monkeypatch.setattr(qa_runner, "run_browser_scenario", record)

    assert qa.main(["run", "--tag", "nightly"]) == 0
    assert qa.main(["run", "--scenario", "board"]) == 0
    assert ran == ["nightly-sweep", "board"]


def test_run_rejects_unknown_tag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(qa_runner, "run_browser_scenario", _unexpected_call)
    with pytest.raises(SystemExit) as exit_info:
        qa.main(["run", "--tag", "billing"])
    assert exit_info.value.code == 2


def _capture_stack_modes(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    modes: list[str] = []

    def record(scenario: Any, **options: Any) -> dict[str, Any]:
        modes.append(options["stack_mode"])
        return _verdict(scenario.name, "pass")

    scenarios = [parse_scenario(_scenario_text("board", "ui"))]
    monkeypatch.setattr(qa_runner, "load_scenarios", lambda: scenarios)
    monkeypatch.setattr(qa_runner, "run_browser_scenario", record)
    monkeypatch.delenv(qa.STACK_MODE_ENV, raising=False)
    return modes


def test_run_defaults_to_docker_stack(monkeypatch: pytest.MonkeyPatch) -> None:
    modes = _capture_stack_modes(monkeypatch)
    assert qa.main(["run"]) == 0
    assert modes == ["docker"]


def test_run_reads_stack_mode_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    modes = _capture_stack_modes(monkeypatch)
    monkeypatch.setenv(qa.STACK_MODE_ENV, "inprocess")
    assert qa.main(["run"]) == 0
    assert modes == ["inprocess"]


def test_run_stack_option_overrides_env(monkeypatch: pytest.MonkeyPatch) -> None:
    modes = _capture_stack_modes(monkeypatch)
    monkeypatch.setenv(qa.STACK_MODE_ENV, "inprocess")
    assert qa.main(["run", "--stack", "docker"]) == 0
    assert modes == ["docker"]


def test_run_rejects_unknown_stack_option(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(qa_runner, "run_browser_scenario", _unexpected_call)
    with pytest.raises(SystemExit) as exit_info:
        qa.main(["run", "--stack", "kubernetes"])
    assert exit_info.value.code == 2
    assert "invalid choice" in capsys.readouterr().err


def test_run_rejects_unknown_stack_env_before_running(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(qa_runner, "run_browser_scenario", _unexpected_call)
    monkeypatch.setenv(qa.STACK_MODE_ENV, "kubernetes")
    assert qa.main(["run"]) == 1
    err = capsys.readouterr().err
    assert "QA_STACK_MODE 'kubernetes' is not one of docker, inprocess, postgres" in err


def test_run_without_scenario_files_exits_one(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def no_files() -> list[Any]:
        raise ValueError("no scenario files (*.yaml) in qa/scenarios/")

    monkeypatch.setattr(qa_runner, "load_scenarios", no_files)
    assert qa.main(["run"]) == 1
    assert "no scenario files" in capsys.readouterr().err


def test_nightly_runs_every_scenario_and_writes_verdict_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    scenarios = [
        parse_scenario(_scenario_text("board", "ui")),
        parse_scenario(_scenario_text("nightly-sweep", "nightly")),
    ]
    ran: list[str] = []

    def record(scenario: Any, **options: Any) -> dict[str, Any]:
        ran.append(scenario.name)
        return _verdict(scenario.name, "pass")

    verdict_path = tmp_path / "nightly-verdict.json"
    monkeypatch.setattr(qa, "NIGHTLY_VERDICT_PATH", verdict_path)
    monkeypatch.setattr(qa_runner, "load_scenarios", lambda: scenarios)
    monkeypatch.setattr(qa_runner, "run_browser_scenario", record)
    monkeypatch.setattr(qa_regression, "load_profiles", lambda path: [])

    assert qa.main(["nightly"]) == 0
    assert ran == ["board", "nightly-sweep"]
    written = json.loads(verdict_path.read_text(encoding="utf-8"))
    assert written["scenarios"] == [_verdict("board", "pass"), _verdict("nightly-sweep", "pass")]
    assert written["layers"] == []


def test_nightly_writes_verdict_file_and_exits_one_on_blocked_verdict(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    verdict_path = tmp_path / "nightly-verdict.json"
    blocked = _verdict("board", "blocked")
    monkeypatch.setattr(qa, "NIGHTLY_VERDICT_PATH", verdict_path)
    monkeypatch.setattr(
        qa_runner, "load_scenarios", lambda: [parse_scenario(_scenario_text("board", "ui"))]
    )
    monkeypatch.setattr(qa_runner, "run_browser_scenario", lambda scenario, **options: blocked)
    monkeypatch.setattr(qa_regression, "load_profiles", lambda path: [])

    assert qa.main(["nightly"]) == 1
    written = json.loads(verdict_path.read_text(encoding="utf-8"))
    assert written["scenarios"] == [blocked]
    assert written["layers"] == []


def test_nightly_runs_layers_for_two_profiles_with_one_failing_layer(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    scenarios = [parse_scenario(_scenario_text("board", "ui"))]
    monkeypatch.setattr(qa_runner, "load_scenarios", lambda: scenarios)
    monkeypatch.setattr(
        qa_runner, "run_browser_scenario", lambda scenario, **options: _verdict("board", "pass")
    )

    profile_a = qa_regression.Profile(
        name="alpha", working_directory="/proj/alpha",
        regression=(), baseline="qa/baselines/alpha.json",
    )
    profile_b = qa_regression.Profile(
        name="beta", working_directory="/proj/beta",
        regression=(), baseline="qa/baselines/beta.json",
    )
    monkeypatch.setattr(qa_regression, "load_profiles", lambda path: [profile_a, profile_b])
    monkeypatch.setattr(qa_regression, "read_baseline", lambda path: {})

    layer_calls: list[str] = []

    def fake_run_layers(layers: list[str], runners: dict[str, Any]) -> list[dict[str, Any]]:
        layer_calls.extend(layers)
        return [
            {"layer": "regression", "scenario": "regression", "verdict": "pass"},
            {"layer": "api", "scenario": "api", "verdict": "pass"},
            {"layer": "ui", "scenario": "board", "verdict": "fail", "reason": "step 1 failed"},
        ]

    monkeypatch.setattr(qa_worker, "run_layers", fake_run_layers)

    verdict_path = tmp_path / "nightly-verdict.json"
    monkeypatch.setattr(qa, "NIGHTLY_VERDICT_PATH", verdict_path)

    assert qa.main(["nightly"]) == 1
    written = json.loads(verdict_path.read_text(encoding="utf-8"))
    assert written["scenarios"] == [_verdict("board", "pass")]
    assert written["layers"] == fake_run_layers([], {}) * 2
    assert len(layer_calls) == 6


def _unexpected_call(*args: Any, **kwargs: Any) -> Any:
    raise AssertionError("the QA stack must not be touched by a rejected command")


PYTEST_COMMAND = "python -m pytest -q"
RUFF_COMMAND = "python -m ruff check src"


def _profiles_file(tmp_path: Path, working_directory: str) -> Path:
    path = tmp_path / "projects.yaml"
    path.write_text(
        "projects:\n"
        "  - name: claude-runner\n"
        f"    working_directory: {json.dumps(working_directory)}\n"
        "    regression:\n"
        "      - name: pytest\n"
        f"        command: {PYTEST_COMMAND}\n"
        "      - name: ruff\n"
        f"        command: {RUFF_COMMAND}\n"
        "    baseline: qa/baselines/claude-runner.json\n",
        encoding="utf-8",
    )
    return path


@pytest.fixture
def regression_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    project = tmp_path / "claude-runner"
    project.mkdir()
    monkeypatch.setattr(qa, "PROFILES_PATH", _profiles_file(tmp_path, str(project)))
    monkeypatch.setattr(qa_worker, "container_path", lambda host_path: host_path)
    return project


def _outcome(output: str = "", *, missing: bool = False) -> qa_worker.CommandOutcome:
    return qa_worker.CommandOutcome(
        output=output,
        exit_code=None if missing else 0,
        timed_out=False,
        missing_command=missing,
        log_path=None,
    )


def _fake_executor(
    monkeypatch: pytest.MonkeyPatch, outcomes: dict[str, qa_worker.CommandOutcome]
) -> list[str]:
    calls: list[str] = []

    def run(command: str, working_directory: str, **options: Any) -> qa_worker.CommandOutcome:
        calls.append(command)
        return outcomes[command]

    monkeypatch.setattr(qa_worker, "run_command", run)
    return calls


PYTEST_NEW = "FAILED tests/test_a.py::test_new - boom\n"
PYTEST_OLD = "FAILED tests/test_a.py::test_old - boom\n"
RUFF_CLEAN = "All checks passed!\n"
RUFF_F401 = "F401 `os` imported but unused\n --> src/a.py:3:8\n"


def test_regression_reports_a_new_failure(
    regression_project: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls = _fake_executor(
        monkeypatch, {PYTEST_COMMAND: _outcome(PYTEST_NEW), RUFF_COMMAND: _outcome(RUFF_CLEAN)}
    )

    assert qa.main(["regression", "--workdir", str(regression_project)]) == 1
    verdict = json.loads(capsys.readouterr().out)
    assert verdict["verdict"] == "fail"
    assert verdict["reason"] == "new failure tests/test_a.py::test_new"
    assert calls == [PYTEST_COMMAND, RUFF_COMMAND]


def test_regression_passes_when_the_failure_is_in_the_baseline(
    regression_project: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    baseline = regression_project / "qa" / "baselines" / "claude-runner.json"
    baseline.parent.mkdir(parents=True)
    baseline.write_text(json.dumps({"pytest": ["tests/test_a.py::test_old"]}), encoding="utf-8")
    _fake_executor(
        monkeypatch, {PYTEST_COMMAND: _outcome(PYTEST_OLD), RUFF_COMMAND: _outcome(RUFF_CLEAN)}
    )

    assert qa.main(["regression", "--workdir", str(regression_project)]) == 0
    assert json.loads(capsys.readouterr().out) == {
        "layer": "regression",
        "scenario": "regression",
        "verdict": "pass",
    }


def test_regression_skips_a_project_without_profile(
    regression_project: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls = _fake_executor(monkeypatch, {})

    assert qa.main(["regression", "--workdir", str(regression_project.parent / "other")]) == 1
    verdict = json.loads(capsys.readouterr().out)
    assert verdict["verdict"] == "skipped"
    assert verdict["reason"] == "no QA profile"
    assert calls == []


def test_regression_blocks_when_a_command_is_missing(
    regression_project: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls = _fake_executor(
        monkeypatch, {PYTEST_COMMAND: _outcome(missing=True), RUFF_COMMAND: _outcome(RUFF_CLEAN)}
    )

    assert qa.main(["regression", "--workdir", str(regression_project)]) == 1
    verdict = json.loads(capsys.readouterr().out)
    assert verdict["verdict"] == "blocked"
    assert verdict["reason"] == "regression command not found: pytest"
    assert calls == [PYTEST_COMMAND]


def test_baseline_writes_the_file_and_prints_counts_per_tool(
    regression_project: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _fake_executor(
        monkeypatch,
        {
            PYTEST_COMMAND: _outcome(
                "FAILED tests/test_a.py::test_one - x\nFAILED tests/test_b.py::test_two - y\n"
            ),
            RUFF_COMMAND: _outcome(RUFF_F401),
        },
    )

    assert qa.main(["baseline", "claude-runner"]) == 0
    written = regression_project / "qa" / "baselines" / "claude-runner.json"
    assert json.loads(written.read_text(encoding="utf-8")) == {
        "pytest": ["tests/test_a.py::test_one", "tests/test_b.py::test_two"],
        "ruff": ["src/a.py:F401"],
    }
    output = capsys.readouterr().out
    assert "pytest: 2 failures" in output
    assert "ruff: 1 failures" in output


def test_baseline_is_not_written_when_a_command_is_missing(
    regression_project: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _fake_executor(
        monkeypatch, {PYTEST_COMMAND: _outcome(PYTEST_OLD), RUFF_COMMAND: _outcome(missing=True)}
    )

    assert qa.main(["baseline", "claude-runner"]) == 1
    assert "baseline not written: regression command not found: ruff" in capsys.readouterr().err
    assert not (regression_project / "qa" / "baselines").exists()


API_ROUTES = qa_api.ApiRoutes(
    get_routes=(
        qa_api.GetRoute(
            path="/api/board/queue",
            allowed_statuses=frozenset({200}),
            non_empty=True,
            url_fields=(),
        ),
    ),
    mutations=(qa_api.Mutation(method="POST", path="/api/board/missions/{mission_id}/start"),),
)


def test_api_runs_routes_on_the_stack_and_stops_it(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    events: list[str] = []
    stack = _stack()

    def fake_up(fixture: str = "default", mode: str = "docker") -> QaStack:
        events.append(f"up {mode}")
        return stack

    def fake_down(stopped: QaStack) -> None:
        events.append(f"down {stopped.run_id}")

    class FakeApiClient:
        def __init__(self, base_url: str) -> None:
            events.append(f"client {base_url}")

        def request(
            self, method: str, path: str, *, headers: Any, timeout_seconds: float
        ) -> qa_api.ApiResponse:
            if method == "GET":
                return qa_api.ApiResponse(status_code=200, body='{"queued": []}')
            return qa_api.ApiResponse(status_code=403, body="")

    monkeypatch.setattr(qa_api, "load_routes", lambda path: API_ROUTES)
    monkeypatch.setattr(qa_stack, "stack_up", fake_up)
    monkeypatch.setattr(qa_stack, "stack_down", fake_down)
    monkeypatch.setattr(qa, "_HttpApiClient", FakeApiClient)

    assert qa.main(["api", "--stack", "inprocess"]) == 0
    assert json.loads(capsys.readouterr().out) == [
        {"layer": "api", "scenario": "GET /api/board/queue", "verdict": "pass"},
        {
            "layer": "api",
            "scenario": "POST /api/board/missions/{mission_id}/start",
            "verdict": "pass",
        },
    ]
    assert events == ["up inprocess", "client http://127.0.0.1:49152", f"down {STACK_ID}"]


def test_api_stops_the_stack_when_a_check_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    stopped: list[str] = []

    class BrokenApiClient:
        def __init__(self, base_url: str) -> None:
            pass

        def request(
            self, method: str, path: str, *, headers: Any, timeout_seconds: float
        ) -> qa_api.ApiResponse:
            raise RuntimeError("client broke")

    monkeypatch.setattr(qa_api, "load_routes", lambda path: API_ROUTES)
    monkeypatch.setattr(qa_stack, "stack_up", lambda fixture="default", mode="docker": _stack())
    monkeypatch.setattr(qa_stack, "stack_down", lambda stack: stopped.append(stack.run_id))
    monkeypatch.setattr(qa, "_HttpApiClient", BrokenApiClient)

    with pytest.raises(RuntimeError, match="client broke"):
        qa.main(["api"])
    assert stopped == [STACK_ID]


def _api_skip_reason() -> str | None:
    if shutil.which("docker") is None:
        return "docker is not on PATH"
    if importlib.util.find_spec("playwright") is None:
        return "playwright is not installed: pip install playwright && playwright install chromium"
    return None


@pytest.mark.qa_integration
@pytest.mark.skipif(_api_skip_reason() is not None, reason=_api_skip_reason() or "")
def test_api_command_runs_on_a_docker_stack(capsys: pytest.CaptureFixture[str]) -> None:
    assert qa.main(["api", "--stack", "docker"]) == 0
    output = capsys.readouterr().out
    assert '"verdict": "fail"' not in output
    assert "postgresql://" not in output


def test_smoke_help_lists_every_flag(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        qa.main(["smoke", "--help"])
    assert exit_info.value.code == 0
    output = capsys.readouterr().out
    for flag in (
        "--url",
        "--fixture",
        "--lang",
        "--allow-host",
        "--max-pages",
        "--max-clicks-per-page",
        "--max-minutes",
    ):
        assert flag in output


def test_smoke_passes_parsed_limits_to_the_crawl(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []

    def fake_run_smoke(seeds: Any, **kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return {"verdict": "pass"}

    monkeypatch.setattr(qa_smoke, "load_seeds", lambda: "seeds")
    monkeypatch.setattr(qa_smoke, "run_smoke", fake_run_smoke)
    argv = [
        "smoke",
        "--url",
        "http://127.0.0.1:8000",
        "--lang",
        "ru",
        "--max-pages",
        "3",
        "--max-clicks-per-page",
        "2",
        "--max-minutes",
        "1.5",
    ]
    assert qa.main(argv) == 0
    assert calls == [
        {
            "base_url": "http://127.0.0.1:8000",
            "lang": "ru",
            "allow_hosts": (),
            "max_pages": 3,
            "max_clicks_per_page": 2,
            "max_minutes": 1.5,
        }
    ]


def test_smoke_without_lang_crawls_both_languages(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []

    def fake_run_smoke(seeds: Any, **kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return {"verdict": "pass", "language": kwargs["lang"]}

    monkeypatch.setattr(qa_smoke, "load_seeds", lambda: "seeds")
    monkeypatch.setattr(qa_smoke, "run_smoke", fake_run_smoke)

    assert qa.main(["smoke", "--url", "http://127.0.0.1:8000"]) == 0
    assert [call["lang"] for call in calls] == ["en", "ru"]


def test_smoke_with_allow_host_passes_the_host_to_the_crawl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []

    def fake_run_smoke(seeds: Any, **kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return {"verdict": "pass"}

    monkeypatch.setattr(qa_smoke, "load_seeds", lambda: "seeds")
    monkeypatch.setattr(qa_smoke, "run_smoke", fake_run_smoke)

    assert qa.main(["smoke", "--url", "http://board.example-prod.net", "--lang", "en",
                   "--allow-host", "board.example-prod.net"]) == 0
    assert calls == [
        {
            "base_url": "http://board.example-prod.net",
            "lang": "en",
            "allow_hosts": ("board.example-prod.net",),
            "max_pages": qa_smoke.MAX_PAGES,
            "max_clicks_per_page": qa_smoke.MAX_CLICKS_PER_PAGE,
            "max_minutes": qa_smoke.MAX_MINUTES,
        }
    ]


def test_smoke_without_allow_host_still_blocks_a_non_local_host() -> None:
    with pytest.raises(qa_smoke.HostRefusedError):
        qa_smoke.check_host("http://board.example-prod.net")


def test_smoke_merged_verdict_keeps_blocked_or_fail_when_a_language_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run_smoke(seeds: Any, **kwargs: Any) -> dict[str, Any]:
        return {"verdict": "pass" if kwargs["lang"] == "en" else "fail"}

    monkeypatch.setattr(qa_smoke, "load_seeds", lambda: "seeds")
    monkeypatch.setattr(qa_smoke, "run_smoke", fake_run_smoke)

    assert qa.main(["smoke", "--url", "http://127.0.0.1:8000"]) == 1


def test_smoke_merged_verdict_keeps_the_findings_of_both_languages(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    en_finding = {
        "severity": "warning",
        "detector": "no-effect",
        "page": "/static/board.html",
        "element": "Open kanban",
        "reason": "no visible change",
        "screenshot": "",
    }
    ru_finding = {
        "severity": "error",
        "detector": "pageerror",
        "page": "/static/board.html",
        "element": "",
        "reason": "TypeError: x",
        "screenshot": "",
    }

    def fake_run_smoke(seeds: Any, **kwargs: Any) -> dict[str, Any]:
        if kwargs["lang"] == "en":
            return {"verdict": "pass", "findings": [en_finding]}
        return {"verdict": "fail", "findings": [ru_finding]}

    monkeypatch.setattr(qa_smoke, "load_seeds", lambda: "seeds")
    monkeypatch.setattr(qa_smoke, "run_smoke", fake_run_smoke)

    assert qa.main(["smoke", "--url", "http://127.0.0.1:8000"]) == 1
    merged = json.loads(capsys.readouterr().out)
    assert merged["verdict"] == "fail"
    assert merged["findings"] == [
        {**en_finding, "lang": "en"},
        {**ru_finding, "lang": "ru"},
    ]


def test_smoke_refuses_a_non_local_host_before_the_browser_starts(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert qa.main(["smoke", "--url", "https://board.example-prod.net"]) == 1
    output = capsys.readouterr().out
    assert '"verdict": "blocked"' in output
    assert "is not local" in output


def test_smoke_on_throwaway_stack_crawls_both_languages_on_one_stack(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stopped: list[QaStack] = []
    crawled: list[str] = []
    stack = _stack()
    monkeypatch.setattr(qa_smoke, "load_seeds", lambda: "seeds")
    monkeypatch.setattr(qa_stack, "stack_up", lambda fixture, mode: stack)
    monkeypatch.setattr(qa_stack, "stack_down", stopped.append)

    def fake_run_smoke(seeds: Any, **kwargs: Any) -> dict[str, Any]:
        crawled.append(kwargs["base_url"])
        return _verdict("smoke", "pass")

    monkeypatch.setattr(qa_smoke, "run_smoke", fake_run_smoke)
    assert qa.main(["smoke", "--fixture", "empty"]) == 0
    assert crawled == [stack.dashboard_url, stack.dashboard_url]
    assert stopped == [stack]


def test_nightly_without_smoke_option_has_no_smoke_key(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    verdict_path = tmp_path / "nightly-verdict.json"
    monkeypatch.setattr(qa, "NIGHTLY_VERDICT_PATH", verdict_path)
    monkeypatch.setattr(qa_runner, "load_scenarios", lambda: [])
    monkeypatch.setattr(qa_regression, "load_profiles", lambda path: [])
    monkeypatch.setattr(qa_smoke, "run_smoke", _unexpected_call)

    assert qa.main(["nightly"]) == 0
    written = json.loads(verdict_path.read_text(encoding="utf-8"))
    assert written == {"scenarios": [], "layers": []}


def test_nightly_with_smoke_option_crawls_en_then_ru(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    verdict_path = tmp_path / "nightly-verdict.json"
    stack = _stack()
    stopped: list[QaStack] = []
    crawled: list[str] = []
    monkeypatch.setattr(qa, "NIGHTLY_VERDICT_PATH", verdict_path)
    monkeypatch.setattr(qa, "_qa_options", lambda store: {"smoke": True})
    monkeypatch.setattr(qa_runner, "load_scenarios", lambda: [])
    monkeypatch.setattr(qa_regression, "load_profiles", lambda path: [])
    monkeypatch.setattr(qa_smoke, "load_seeds", lambda: "seeds")
    monkeypatch.setattr(qa_stack, "stack_up", lambda fixture, mode: stack)
    monkeypatch.setattr(qa_stack, "stack_down", stopped.append)

    def fake_run_smoke(seeds: Any, **kwargs: Any) -> dict[str, Any]:
        crawled.append(kwargs["lang"])
        return _verdict("smoke", "pass")

    monkeypatch.setattr(qa_smoke, "run_smoke", fake_run_smoke)

    assert qa.main(["nightly"]) == 0
    written = json.loads(verdict_path.read_text(encoding="utf-8"))
    assert written == {
        "scenarios": [],
        "layers": [],
        "smoke": {**_verdict("smoke", "pass"), "findings": []},
    }
    assert crawled == ["en", "ru"]
    assert stopped == [stack]
