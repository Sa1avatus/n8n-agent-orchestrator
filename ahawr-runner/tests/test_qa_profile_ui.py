"""Strict parsing of the optional per-project `ui` block in qa/projects.yaml.

Every case here goes through load_profiles with real YAML text written to a temp file,
so the parser itself is the thing under test, not a hand-built dict.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ahawr_runner import qa_runner
from ahawr_runner.qa_regression import Profile, ProfileError, RegressionCommand, load_profiles

PROJECT_HEAD = """\
projects:
  - name: mi-find-tracker
    working_directory: /d/claudeprojects/mi-find-tracker
    regression:
      - name: pytest
        command: python -m pytest -q --no-cov
    baseline: qa/baselines/mi-find-tracker.json
"""

GOOD_UI = """\
    ui:
      start_command: ["python", "-m", "uvicorn", "app:app", "--port", "{port}"]
      env:
        MIFIND_DATA_DIR: "{data_dir}"
        MIFIND_PORT: "{port}"
        MIFIND_LOG_LEVEL: "info"
      port_env: MIFIND_PORT
      health_path: /healthz
      ready_timeout_seconds: 20
      scenarios_dir: mi-find-tracker
"""


def _profiles(tmp_path: Path, ui_block: str) -> list[Profile]:
    path = tmp_path / "projects.yaml"
    path.write_text(PROJECT_HEAD + ui_block, encoding="utf-8")
    return load_profiles(path)


def test_good_ui_block_is_parsed(tmp_path: Path) -> None:
    ui = _profiles(tmp_path, GOOD_UI)[0].ui
    assert ui is not None
    assert ui.start_command == ("python", "-m", "uvicorn", "app:app", "--port", "{port}")
    assert ui.env == {
        "MIFIND_DATA_DIR": "{data_dir}",
        "MIFIND_PORT": "{port}",
        "MIFIND_LOG_LEVEL": "info",
    }
    assert ui.port_env == "MIFIND_PORT"
    assert ui.health_path == "/healthz"
    assert ui.ready_timeout_seconds == 20
    assert ui.scenarios_dir == "mi-find-tracker"


def test_unknown_ui_key_is_rejected(tmp_path: Path) -> None:
    block = GOOD_UI.replace("      port_env: MIFIND_PORT\n", "      browser: chromium\n")
    with pytest.raises(ProfileError, match="unknown keys") as caught:
        _profiles(tmp_path, block)
    assert "browser" in str(caught.value)


def test_shell_string_start_command_is_rejected(tmp_path: Path) -> None:
    block = GOOD_UI.replace(
        '      start_command: ["python", "-m", "uvicorn", "app:app", "--port", "{port}"]',
        '      start_command: "python -m uvicorn app:app --port {port}"',
    )
    with pytest.raises(ProfileError, match="start_command must be a non-empty list") as caught:
        _profiles(tmp_path, block)
    assert "shell string" in str(caught.value)


@pytest.mark.parametrize("seconds", [0, 0.5, 6000], ids=["zero", "below-min", "above-max"])
def test_ready_timeout_out_of_bounds_is_rejected(tmp_path: Path, seconds: float) -> None:
    block = GOOD_UI.replace("ready_timeout_seconds: 20", f"ready_timeout_seconds: {seconds}")
    with pytest.raises(ProfileError, match="ready_timeout_seconds must be between") as caught:
        _profiles(tmp_path, block)
    assert str(seconds) in str(caught.value)


def test_ready_timeout_must_be_a_number(tmp_path: Path) -> None:
    block = GOOD_UI.replace("ready_timeout_seconds: 20", 'ready_timeout_seconds: "20"')
    with pytest.raises(ProfileError, match="ready_timeout_seconds must be a number"):
        _profiles(tmp_path, block)


def test_env_placeholder_outside_port_and_data_dir_is_rejected(tmp_path: Path) -> None:
    block = GOOD_UI.replace('MIFIND_DATA_DIR: "{data_dir}"', 'MIFIND_DATA_DIR: "{home}"')
    with pytest.raises(ProfileError, match="unknown placeholder") as caught:
        _profiles(tmp_path, block)
    assert "home" in str(caught.value)


@pytest.mark.parametrize(
    "scenarios_dir",
    ["../evil", "mi-find-tracker/../other", "/etc/scenarios", "D:/scenarios"],
    ids=["parent", "middle", "absolute", "drive"],
)
def test_scenarios_dir_must_be_relative_without_dotdot(tmp_path: Path, scenarios_dir: str) -> None:
    block = GOOD_UI.replace("scenarios_dir: mi-find-tracker", f"scenarios_dir: {scenarios_dir}")
    with pytest.raises(ProfileError, match="scenarios_dir must be relative"):
        _profiles(tmp_path, block)


def test_health_path_must_start_with_slash(tmp_path: Path) -> None:
    block = GOOD_UI.replace("health_path: /healthz", "health_path: healthz")
    with pytest.raises(ProfileError, match="health_path must start with"):
        _profiles(tmp_path, block)


def test_port_env_must_be_a_variable_name(tmp_path: Path) -> None:
    block = GOOD_UI.replace("port_env: MIFIND_PORT", "port_env: MIFIND-PORT")
    with pytest.raises(ProfileError, match="port_env must be a variable name"):
        _profiles(tmp_path, block)


def test_profile_without_ui_is_unchanged(tmp_path: Path) -> None:
    profile = _profiles(tmp_path, "")[0]
    assert profile.ui is None
    assert profile == Profile(
        name="mi-find-tracker",
        working_directory="/d/claudeprojects/mi-find-tracker",
        regression=(RegressionCommand(name="pytest", command="python -m pytest -q --no-cov"),),
        baseline="qa/baselines/mi-find-tracker.json",
    )


PROJECTS_FILE = Path(__file__).resolve().parents[1] / "qa" / "projects.yaml"
MI_FIND_TRACKER = "mi-find-tracker"
# Every page text the mi-find-tracker scenarios assert, quoted from the project source:
# index.html:14 the h1, index.html:36 the "Точки" heading, index.html:39-41 the Google Maps link,
# app.js:197 the empty-state status line written when the point list is empty. The Google note is
# quoted from the project's own tests (test_page.py:30, test_tiles.py:57), which assert it is
# absent when no key is set: the app has no such note, so it can only be asserted as absent.
SOURCE_PAGE_TEXTS = frozenset(
    {
        "Последние координаты устройства",
        "Точки",
        "Открыть в Google Картах",
        "Точек за выбранный период нет.",
        "Google Карты не подключены",
    }
)
# The variables the dashboard never needs: a profile that sets one of them would carry a secret.
SECRET_ENV_NAMES = frozenset(
    {"GOOGLE_MAPS_API_KEY", "ACCESS_TOKEN", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"}
)


def _mi_find_tracker_profile() -> Profile:
    for profile in load_profiles(PROJECTS_FILE):
        if profile.name == MI_FIND_TRACKER:
            return profile
    raise AssertionError(f"{MI_FIND_TRACKER} is not in {PROJECTS_FILE}")


def test_mi_find_tracker_profile_is_parsed_next_to_the_runner_profile() -> None:
    names = [profile.name for profile in load_profiles(PROJECTS_FILE)]
    assert names == ["ahawr-runner", MI_FIND_TRACKER]


def test_mi_find_tracker_ui_block_validates_against_the_documented_start() -> None:
    ui = _mi_find_tracker_profile().ui
    assert ui is not None
    # README.md:28 "Without Docker (development): ... PYTHONPATH=src python -m mifind"
    assert ui.start_command == ("python", "-m", "mifind")
    assert ui.port_env == "HOST_PORT"
    assert ui.health_path == "/health"
    assert 1 <= ui.ready_timeout_seconds <= 300
    assert ui.scenarios_dir == MI_FIND_TRACKER


def test_mi_find_tracker_ui_env_is_offline_and_free_of_secrets() -> None:
    ui = _mi_find_tracker_profile().ui
    assert ui is not None
    assert not SECRET_ENV_NAMES & set(ui.env)
    assert "GOOGLE_MAPS_API_KEY" not in PROJECTS_FILE.read_text(encoding="utf-8")
    # .env.example:30-31: LOCATE_ENABLED=false and POLL_SECONDS clamped to its 30 s floor.
    assert ui.env["LOCATE_ENABLED"] == "false"
    assert ui.env["POLL_SECONDS"] == "30"
    assert ui.env["BIND_ADDRESS"] == "127.0.0.1"
    # The poll cycle reads the curl dumps before any request (client.py:109-121), so a path
    # that does not exist inside the throwaway data dir is what keeps Xiaomi out of the loop.
    assert ui.env["CURL_STATUS_FILE"].startswith("{data_dir}")
    assert ui.env["CURL_LOCATE_FILE"].startswith("{data_dir}")
    assert ui.env["DB_PATH"].startswith("{data_dir}")


def test_mi_find_tracker_scenarios_target_the_app_and_parse() -> None:
    scenarios = qa_runner.load_project_scenarios(MI_FIND_TRACKER)
    assert len(scenarios) == 4, [scenario.name for scenario in scenarios]
    for scenario in scenarios:
        assert scenario.target == "app", scenario.name
        assert scenario.steps, scenario.name
        for step in scenario.steps:
            texts = step.expect.text_visible + step.expect.text_absent
            for text in texts:
                reason = f"{scenario.name}: {text!r} is not in the app source"
                assert text in SOURCE_PAGE_TEXTS, reason


def test_mi_find_tracker_scenarios_cover_health_status_and_the_narrow_dark_page() -> None:
    scenario_dir = qa_runner.SCENARIO_DIR / MI_FIND_TRACKER
    paths = sorted(scenario_dir.glob("*.yaml"))
    assert len(paths) == 4
    for path in paths:
        scenario = qa_runner.load_scenario(path)
        actions = {step.action for step in scenario.steps}
        if "health" in path.name:
            assert "api_get" in actions and "open" in actions, path.name
        if "375" in path.name:
            assert "viewport" in actions, path.name
            assert "color_scheme" in actions, path.name
