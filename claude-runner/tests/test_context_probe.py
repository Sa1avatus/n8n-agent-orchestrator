from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from claude_runner.api import create_app
from claude_runner.config import Settings
from claude_runner.context_probe import autocompact_trigger, ctx_from_models, window_env

from .conftest import calls, wait_for
from .test_providers import LOCAL


def models(*items: tuple[str, list[str]]) -> dict[str, Any]:
    return {
        "data": [
            {"id": mid, "aliases": [], "status": {"value": "unloaded", "args": args}}
            for mid, args in items
        ]
    }


QWEN = "qwen3.8-27b-gsq-rco-iq2-s-mtp"
ARGS = ["/app/llama-server", "--ctx-size", "98304", "--kv-unified", "--parallel", "1"]


def test_ctx_from_router_models() -> None:
    payload = models(
        ("gemma", ["--ctx-size", "65536", "--parallel", "10", "--kv-unified"]), (QWEN, ARGS)
    )
    assert ctx_from_models(payload, QWEN) == 98304
    # a unified KV cache is shared by the parallel slots; without it each slot gets a share
    assert ctx_from_models(payload, "gemma") == 65536
    split = models(("m", ["-c", "65536", "-np", "4"]))
    assert ctx_from_models(split, "m") == 16384
    assert ctx_from_models(models(("m", ["--ctx-size=32768"])), "m") == 32768
    alias = {"data": [{"id": "x", "aliases": ["m"], "status": {"args": ["--ctx-size", "4096"]}}]}
    assert ctx_from_models(alias, "m") == 4096
    # unknown model, no --ctx-size, or 0 (= the training context, not known here)
    assert ctx_from_models(payload, "other") is None
    assert ctx_from_models(models(("m", ["--port", "0"])), "m") is None
    assert ctx_from_models(models(("m", ["--ctx-size", "0"])), "m") is None


def test_window_env_matches_claude_codes_trigger() -> None:
    local = Settings.from_env(dict(LOCAL)).providers["LOCAL"]
    # the static 65536 window gives the documented 44344 trigger
    assert autocompact_trigger(65536, local) == local.autocompact_threshold == 44344
    assert window_env(98304, local) == {
        "CLAUDE_CODE_MAX_CONTEXT_TOKENS": "98304",
        "CLAUDE_CODE_AUTO_COMPACT_WINDOW": "98304",
    }
    assert autocompact_trigger(98304, local) == 77112
    assert window_env(32768, local) is not None  # IQ3_XXS at 32K: trigger 11576
    assert window_env(16384, local) is None  # trigger < 8000: keep the static settings


class _Llama(BaseHTTPRequestHandler):
    payload: dict[str, Any] = {}
    auth: list[str] = []

    def do_GET(self) -> None:  # noqa: N802
        self.auth.append(self.headers.get("Authorization", ""))
        body = json.dumps(self.payload).encode()
        self.send_response(200 if self.path == "/v1/models" else 404)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:
        pass


@pytest.fixture
def llama() -> Iterator[str]:
    _Llama.payload, _Llama.auth = models((QWEN, ARGS)), []
    server = HTTPServer(("127.0.0.1", 0), _Llama)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


def probing(settings: Settings, url: str) -> Settings:
    env = {
        **LOCAL,
        "CLAUDE_RUNNER_PROVIDER_LOCAL__CONTEXT_PROBE_URL": url,
        "CLAUDE_RUNNER_PROVIDER_LOCAL__CONTEXT_PROBE_KEY": "llama-key",
        "CLAUDE_RUNNER_PROVIDER_LOCAL__CLAUDE_AUTOCOMPACT_PCT_OVERRIDE": "67",
    }
    object.__setattr__(settings, "providers", Settings.from_env(env).providers)
    object.__setattr__(settings, "compact_min_tokens", 999_999)
    return settings


def run_events(settings: Settings, run_id: str) -> list[dict[str, Any]]:
    path = Path(settings.data_dir) / "events" / f"{run_id}.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_run_gets_the_servers_window(settings: Settings, llama: str, tmp_path: Path) -> None:
    probing(settings, llama)
    with TestClient(create_app(settings)) as client:
        rid = client.post(
            "/v1/runs", json={"input": "t", "model": QWEN, "provider": "local", "role": "worker"}
        ).json()["run_id"]
        assert wait_for(client, rid)["status"] == "completed"
    (worker,) = calls(tmp_path)
    # the probed window replaces the static 65536 and the percentage of it
    assert worker["window"] == {
        "CLAUDE_CODE_MAX_CONTEXT_TOKENS": "98304",
        "CLAUDE_CODE_AUTO_COMPACT_WINDOW": "98304",
    }
    assert _Llama.auth == ["Bearer llama-key"]  # the key goes to llama-server only
    (ev,) = [e for e in run_events(settings, rid) if e.get("kind") == "context_window"]
    assert ev["source"] == "probe" and ev["probed"] == 98304 and ev["window"] == 98304


def test_unreachable_server_keeps_static_settings(settings: Settings, tmp_path: Path) -> None:
    probing(settings, "http://127.0.0.1:9")  # nothing listens there
    with TestClient(create_app(settings)) as client:
        rid = client.post(
            "/v1/runs", json={"input": "t", "model": QWEN, "provider": "local", "role": "worker"}
        ).json()["run_id"]
        assert wait_for(client, rid)["status"] == "completed"
    (worker,) = calls(tmp_path)
    assert worker["window"] == {
        "CLAUDE_CODE_AUTO_COMPACT_WINDOW": "65536",
        "CLAUDE_AUTOCOMPACT_PCT_OVERRIDE": "67",
    }
    (ev,) = [e for e in run_events(settings, rid) if e.get("kind") == "context_window"]
    assert ev["source"] == "static" and ev["probed"] is None
