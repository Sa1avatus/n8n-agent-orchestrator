from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from claude_runner import qa_smoke, qa_stack
from tests.test_qa_integration import DOCKER_INFO_TIMEOUT_SECONDS, SKIP_REASON

SMOKE_FIXTURE = "default"
SMOKE_LANG = "en"
SMOKE_MAX_PAGES = 2
SMOKE_MAX_CLICKS_PER_PAGE = 2
SMOKE_MAX_MINUTES = 2
VERDICTS = {"pass", "fail", "blocked"}


def _docker_objects_left(stack: qa_stack.QaStack) -> list[str]:
    names = [*stack.containers, *stack.volumes, *stack.networks, *stack.images]
    left = []
    for name in names:
        result = subprocess.run(
            ["docker", "inspect", name],
            capture_output=True,
            check=False,
            timeout=DOCKER_INFO_TIMEOUT_SECONDS,
        )
        if result.returncode == 0:
            left.append(name)
    return left


@pytest.mark.qa_integration
@pytest.mark.skipif(SKIP_REASON is not None, reason=SKIP_REASON or "")
def test_smoke_crawl_on_throwaway_stack_returns_verdict(tmp_path: Path) -> None:
    seeds = qa_smoke.load_seeds()
    stack = qa_stack.stack_up(SMOKE_FIXTURE)
    try:
        verdict = qa_smoke.run_smoke(
            seeds,
            base_url=stack.dashboard_url,
            max_pages=SMOKE_MAX_PAGES,
            max_clicks_per_page=SMOKE_MAX_CLICKS_PER_PAGE,
            max_minutes=SMOKE_MAX_MINUTES,
            runs_dir=tmp_path,
            lang=SMOKE_LANG,
        )
    finally:
        qa_stack.stack_down(stack)

    assert verdict["verdict"] in VERDICTS, verdict
    assert isinstance(verdict["findings"], list), verdict
    assert _docker_objects_left(stack) == []
