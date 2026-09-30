from __future__ import annotations

import os
import stat
import sys
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from claude_runner.api import create_app
from claude_runner.config import Settings

FAKE = Path(__file__).with_name("fake_claude.py")


@pytest.fixture
def fake_bin(tmp_path: Path) -> str:
    wrapper = tmp_path / "claude"
    wrapper.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{FAKE}" "$@"\n')
    wrapper.chmod(wrapper.stat().st_mode | stat.S_IEXEC)
    return str(wrapper)


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "workspace"
    (ws / "sub").mkdir(parents=True)
    return ws


@pytest.fixture(autouse=True)
def claude_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    config = tmp_path / "claude-config"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config))
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(tmp_path / "calls.jsonl"))
    # the tests may run inside a runner-started Claude Code whose env already carries a probed
    # window; the child env inherits it and the window assertions would see it
    for name in (
        "CLAUDE_CODE_MAX_CONTEXT_TOKENS",
        "CLAUDE_CODE_AUTO_COMPACT_WINDOW",
        "CLAUDE_AUTOCOMPACT_PCT_OVERRIDE",
    ):
        monkeypatch.delenv(name, raising=False)
    return config


@pytest.fixture
def settings(tmp_path: Path, fake_bin: str, workspace: Path) -> Settings:
    env = {
        "CLAUDE_RUNNER_DATA_DIR": str(tmp_path / "data"),
        "CLAUDE_RUNNER_CLAUDE_BIN": fake_bin,
        "CLAUDE_RUNNER_WORKSPACE": str(workspace),
        "CLAUDE_RUNNER_PATH_MAP": f"D:\\n8n\\workspace={workspace}",
        "CLAUDE_RUNNER_MAX_CONCURRENT": "2",
        "CLAUDE_RUNNER_COMPACT_MIN_TOKENS": "2500",
    }
    s = Settings.from_env(env)
    object.__setattr__(s, "interrupt_grace_seconds", 2.0)
    return s


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings)) as c:
        yield c


def calls(tmp_path: Path) -> list[dict[str, Any]]:
    import json

    path = tmp_path / "calls.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines()]


def wait_for(
    client: TestClient,
    run_id: str,
    done: Callable[[dict[str, Any]], bool] | None = None,
    timeout: float = 20.0,
) -> dict[str, Any]:
    done = done or (lambda r: r.get("status") not in ("queued", "running", "started"))
    deadline = time.monotonic() + timeout
    while True:
        body: dict[str, Any] = client.get(f"/v1/runs/{run_id}").json()
        if done(body) or time.monotonic() > deadline:
            return body
        time.sleep(0.05)


os.environ.setdefault("PYTHONUNBUFFERED", "1")
