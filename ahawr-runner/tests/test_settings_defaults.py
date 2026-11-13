"""The shared settings defaults live in one JSON file; the copies the dashboard needs are generated from it."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from ahawr_runner import (
    board_engine,
    overrides_config,
    retrieval_config,
    search_cli,
    settings_defaults,
)

SRC = Path(settings_defaults.__file__).parent
# The values that must be written once; a second copy in the source tree is a drift waiting to happen.
SINGLE_SOURCE_LITERALS = ("http://ahawr-runner:8700", "http://ahawr-retrieval:8500", "http://127.0.0.1:8700")
ALLOWED_FILES = {"settings_defaults.json", "board_defaults.js"}


def test_generated_js_is_current() -> None:
    generated = (SRC / "board_defaults.js").read_text(encoding="utf-8")
    assert generated == settings_defaults.render_js(), (
        "board_defaults.js is out of date: run python -m ahawr_runner.settings_defaults"
    )


def test_python_modules_read_the_shared_values() -> None:
    runner_url = settings_defaults.runner_url()
    assert board_engine.ENGINE_DEFAULTS["runner_url"] == runner_url
    assert board_engine.ENGINE_DEFAULTS["retry_delays"] == settings_defaults.section("engine")["retry_delays"]
    retrieval = settings_defaults.section("retrieval")
    assert overrides_config.DEFAULTS["retrieval_url"] == retrieval["url"]
    assert retrieval_config.DEFAULTS["retrieval_url"] == retrieval["url"]
    assert search_cli.DEFAULT_URL == retrieval["url"]
    for module_defaults in (overrides_config.DEFAULTS, retrieval_config.DEFAULTS):
        assert module_defaults["retrieval_timeout_ms"] == retrieval["timeout_ms"]
        assert module_defaults["retrieval_worker_max_tokens"] == retrieval["worker_max_tokens"]
        assert module_defaults["retrieval_reviewer_max_tokens"] == retrieval["reviewer_max_tokens"]


def test_section_returns_a_copy() -> None:
    first = settings_defaults.section("engine")
    first["retry_delays"].append(999)
    assert 999 not in settings_defaults.section("engine")["retry_delays"]


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_js_modules_read_the_shared_values() -> None:
    script = (
        "const E = require('./board_engine.js').ENGINE_DEFAULTS;"
        "const R = require('./board_retrieval.js').DEFAULTS;"
        "const O = require('./board_overrides.js').DEFAULTS;"
        "console.log(JSON.stringify({E, R, O}));"
    )
    run = subprocess.run(["node", "-e", script], cwd=SRC, capture_output=True, text=True, check=True)
    seen = json.loads(run.stdout)
    shared = json.loads((SRC / "settings_defaults.json").read_text(encoding="utf-8"))
    assert seen["E"]["runner_url"] == shared["runner"]["url"]
    assert seen["E"]["retry_delays"] == shared["engine"]["retry_delays"]
    for key in ("R", "O"):
        assert seen[key]["retrieval_url"] == shared["retrieval"]["url"]
        assert seen[key]["retrieval_timeout_ms"] == shared["retrieval"]["timeout_ms"]
        assert seen[key]["retrieval_worker_max_tokens"] == shared["retrieval"]["worker_max_tokens"]
        assert seen[key]["retrieval_reviewer_max_tokens"] == shared["retrieval"]["reviewer_max_tokens"]


def test_the_shared_literals_are_written_once() -> None:
    offenders: list[str] = []
    for path in sorted([*SRC.glob("*.py"), *SRC.glob("*.js"), *SRC.glob("*.json"), *SRC.glob("*.html")]):
        if path.name in ALLOWED_FILES:
            continue
        text = path.read_text(encoding="utf-8")
        offenders += [f"{path.name}: {literal}" for literal in SINGLE_SOURCE_LITERALS if literal in text]
    assert not offenders, offenders
