from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

OPS = Path(__file__).resolve().parents[2] / "data-tables" / "ops"
LAUNCH = OPS / "launch_mission.sh"
BASH = shutil.which("bash")


def _launch_snippet(select_fragment: str) -> str:
    lines = LAUNCH.read_text(encoding="utf-8").splitlines()
    line = next(text for text in lines if select_fragment in text)
    match = re.search(r'python -c "([^"]*)"', line)
    assert match is not None, line
    return match.group(1)


def _run_snippet(snippet: str, row: dict[str, Any]) -> str:
    result = subprocess.run(
        [sys.executable, "-c", snippet],
        input=json.dumps([row]), capture_output=True, text=True, check=True,
    )
    return result.stdout.strip()


@pytest.mark.parametrize(
    ("roles_json", "expected"),
    [
        ('{"worker": {"1": "local/qwen-made-up"}}', "local"),
        ('{"worker": "anthropic/sonnet-made-up"}', "anthropic"),
        ('{"worker": {}}', ""),
        (None, ""),
    ],
)
def test_launch_worker_provider_comes_from_roles_json(
    roles_json: str | None, expected: str
) -> None:
    snippet = _launch_snippet("select roles_json r ")
    assert _run_snippet(snippet, {"r": roles_json}) == expected


@pytest.mark.parametrize(
    ("engine_json", "expected"),
    [
        ('{"run_manager_workflow_id": "RM-MADE-UP-1"}', "RM-MADE-UP-1"),
        ("{}", ""),
        (None, ""),
    ],
)
def test_launch_run_manager_id_comes_from_engine_json(
    engine_json: str | None, expected: str
) -> None:
    snippet = _launch_snippet("select engine_json e ")
    assert _run_snippet(snippet, {"e": engine_json}) == expected


def test_launch_mission_selects_no_legacy_column() -> None:
    text = LAUNCH.read_text(encoding="utf-8")
    assert "select worker_provider" not in text
    assert "select run_manager_workflow_id" not in text


@pytest.mark.skipif(BASH is None, reason="bash is not installed")
def test_launch_mission_passes_bash_syntax_check() -> None:
    assert BASH is not None
    result = subprocess.run([BASH, "-n", LAUNCH.as_posix()], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


@pytest.fixture
def notify(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.syspath_prepend(str(OPS))
    import notify as module
    import pgdb

    monkeypatch.setattr(module, "mission_tag", lambda: "")
    monkeypatch.setattr(module, "_env", lambda name: "made-up-hook-path")
    return module, pgdb


class _Response:
    status = 200

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *exc: object) -> None:
        return None


def _capture_send(
    monkeypatch: pytest.MonkeyPatch, module: Any, pgdb: Any, engine_json: str | None
) -> tuple[bool, list[str], list[bytes]]:
    sql_seen: list[str] = []
    bodies: list[bytes] = []

    def fake_q(sql: str, params: Any = None) -> list[dict[str, Any]]:
        sql_seen.append(sql)
        return [{"e": engine_json}]

    def fake_urlopen(request: Any, timeout: float) -> _Response:
        bodies.append(request.data)
        return _Response()

    monkeypatch.setattr(pgdb, "q", fake_q)
    monkeypatch.setattr(module.urllib.request, "urlopen", fake_urlopen)
    return module.send("made-up notice"), sql_seen, bodies


def test_notify_reads_chat_id_from_engine_json(
    notify: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    module, pgdb = notify
    ok, sql_seen, bodies = _capture_send(
        monkeypatch, module, pgdb, json.dumps({"telegram_chat_id": "-100777"})
    )
    assert ok is True
    assert len(sql_seen) == 1 and "select engine_json e from" in sql_seen[0]
    assert json.loads(bodies[0])["chat_id"] == "-100777"


def test_notify_sends_nothing_without_engine_json_chat_id(
    notify: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    module, pgdb = notify
    ok, _, bodies = _capture_send(monkeypatch, module, pgdb, "{}")
    assert ok is False
    assert bodies == []
