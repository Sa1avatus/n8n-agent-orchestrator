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
from claude_runner.context_probe import (
    autocompact_trigger,
    ctx_from_models,
    max_trigger,
    resume_compact_threshold,
    window_env,
)

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


def test_trigger_is_a_percentage_of_the_window() -> None:
    local = Settings.from_env(dict(LOCAL)).providers["LOCAL"]
    # without COMPACT_PCT the static trigger's share of 65536 (44344 = 67.66%) is kept, so a
    # 64K server compacts exactly where it did before
    assert autocompact_trigger(65536, local) == local.autocompact_threshold == 44344
    assert autocompact_trigger(98304, local) == 66516  # 1.5 x 44344
    assert autocompact_trigger(81234, local) == 81234 * 44344 // 65536
    assert window_env(98304, local) == {
        "CLAUDE_CODE_MAX_CONTEXT_TOKENS": "98304",
        "CLAUDE_CODE_AUTO_COMPACT_WINDOW": "98304",
        "CLAUDE_AUTOCOMPACT_PCT_OVERRIDE": "67.66",
    }
    # the percentage never eats the room for one reply and one large tool output:
    # 32K (IQ3_XXS) is capped at 32768 - 8192 - 13000 = 11576
    assert max_trigger(32768, local) == 11576
    assert autocompact_trigger(32768, local) == 11576
    assert window_env(16384, local) is None  # trigger < 8000: keep the static settings


def test_compact_pct_setting() -> None:
    env = {**LOCAL, "CLAUDE_RUNNER_PROVIDER_LOCAL__COMPACT_PCT": "75"}
    local = Settings.from_env(env).providers["LOCAL"]
    assert local.compact_pct == 75 and "COMPACT_PCT" not in local.env
    assert autocompact_trigger(98304, local) == 73728
    assert window_env(98304, local)["CLAUDE_AUTOCOMPACT_PCT_OVERRIDE"] == "75.00"  # type: ignore[index]
    high = Settings.from_env({**LOCAL, "CLAUDE_RUNNER_PROVIDER_LOCAL__COMPACT_PCT": "95"})
    assert autocompact_trigger(98304, high.providers["LOCAL"]) == 77112  # capped
    with pytest.raises(ValueError, match="COMPACT_PCT"):
        Settings.from_env({**LOCAL, "CLAUDE_RUNNER_PROVIDER_LOCAL__COMPACT_PCT": "120"})


def test_resume_compaction_threshold_follows_the_window() -> None:
    local = Settings.from_env(dict(LOCAL)).providers["LOCAL"]
    # COMPACT_MIN_TOKENS is a share of the static 65536 window: 40000 -> 60000 at 96K
    assert resume_compact_threshold(65536, local, 40000) == 40000
    assert resume_compact_threshold(98304, local, 40000) == 60000
    # never above the autocompact trigger (a resumed session must start below it)
    assert resume_compact_threshold(98304, local, 60000) == autocompact_trigger(98304, local)
    pct = Settings.from_env({**LOCAL, "CLAUDE_RUNNER_PROVIDER_LOCAL__COMPACT_MIN_PCT": "50"})
    local50 = pct.providers["LOCAL"]
    assert local50.compact_min_pct == 50 and "COMPACT_MIN_PCT" not in local50.env
    assert resume_compact_threshold(98304, local50, 40000) == 49152
    with pytest.raises(ValueError, match="COMPACT_MIN_PCT"):
        Settings.from_env({**LOCAL, "CLAUDE_RUNNER_PROVIDER_LOCAL__COMPACT_MIN_PCT": "0"})


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
    # the block's CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=67 now means 67% of the probed window
    assert worker["window"] == {
        "CLAUDE_CODE_MAX_CONTEXT_TOKENS": "98304",
        "CLAUDE_CODE_AUTO_COMPACT_WINDOW": "98304",
        "CLAUDE_AUTOCOMPACT_PCT_OVERRIDE": "67.00",
    }
    assert _Llama.auth == ["Bearer llama-key"]  # the key goes to llama-server only
    (ev,) = [e for e in run_events(settings, rid) if e.get("kind") == "context_window"]
    assert ev["source"] == "probe" and ev["probed"] == 98304 and ev["window"] == 98304
    assert ev["compact_pct"] == "67.00"


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


@pytest.mark.parametrize(("ctx", "status"), [("98304", "skipped"), ("65536", "completed")])
def test_resume_compaction_uses_the_probed_window(
    settings: Settings, llama: str, ctx: str, status: str
) -> None:
    probing(settings, llama)
    _Llama.payload = models((QWEN, ["--ctx-size", ctx]))
    body = {"input": "t", "model": QWEN, "provider": "local", "role": "worker"}
    with TestClient(create_app(settings)) as client:
        sid = wait_for(client, client.post("/v1/runs", json=body).json()["run_id"])["session_id"]
        wait_for(client, client.post("/v1/runs", json={**body, "session_id": sid}).json()["run_id"])
        # 2015 context tokens; COMPACT_MIN_TOKENS 1500 of 65536 = 2250 at 96K, 1500 at 64K
        done = client.post(f"/v1/sessions/{sid}/compact", json={}).json()
    assert done["status"] == status
    if status == "skipped":
        assert done["reason"] == "below_threshold"
        assert done["min_tokens"] == 2250 and done["window"] == 98304
