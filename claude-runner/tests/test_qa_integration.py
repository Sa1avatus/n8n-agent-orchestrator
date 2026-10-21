from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from claude_runner import qa_runner, qa_stack

SCENARIO_TEXT = """
name: dashboard opens
tags: [ui]
preconditions:
  fixture: empty
steps:
  - open: /
"""
INSTALL_HINT = (
    "install the qa extra with pip install -e .[qa], then run python -m playwright install chromium"
)
DOCKER_INFO_TIMEOUT_SECONDS = 20


def _skip_reason() -> str | None:
    if importlib.util.find_spec("playwright") is None:
        return f"playwright is not installed: {INSTALL_HINT}"
    from playwright.sync_api import sync_playwright

    try:
        with sync_playwright() as playwright:
            chromium = Path(playwright.chromium.executable_path)
    except Exception as error:
        return f"playwright cannot start: {error}; {INSTALL_HINT}"
    if not chromium.exists():
        return f"chromium is not installed: {INSTALL_HINT}"
    if shutil.which("docker") is None:
        return "docker is not on PATH"
    try:
        docker = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            check=False,
            timeout=DOCKER_INFO_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return f"docker is unavailable: {error}"
    if docker.returncode != 0:
        return "docker daemon is not reachable"
    return None


SKIP_REASON = _skip_reason()


@pytest.mark.qa_integration
@pytest.mark.skipif(SKIP_REASON is not None, reason=SKIP_REASON or "")
def test_browser_scenario_writes_verdict_json(tmp_path: Path) -> None:
    from playwright.sync_api import sync_playwright

    scenario = qa_runner.parse_scenario(SCENARIO_TEXT)
    run_dir = tmp_path / "qa-integration"
    run_dir.mkdir()
    started = time.monotonic()
    deadline = started + qa_runner.DEFAULT_MAX_MINUTES * qa_runner.SECONDS_PER_MINUTE
    stack = qa_stack.stack_up(scenario.fixture)
    try:
        progress, recorder = qa_runner._drive(scenario, stack, sync_playwright, run_dir, deadline)
        verdict = qa_runner._settle(scenario, run_dir, started, progress, recorder)
    finally:
        qa_stack.stack_down(stack)

    written = json.loads((run_dir / "verdict.json").read_text(encoding="utf-8"))
    assert written == verdict
    assert verdict["verdict"] == "pass", verdict
