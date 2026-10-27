"""LiteLLM sync (claude_runner/provider_litellm.py) against a fake local HTTP server only."""

from __future__ import annotations

import http.server
import json
import socket
import threading
from collections.abc import Iterator
from typing import Any

import pytest

from claude_runner.provider_litellm import (
    NOT_SYNCED,
    SYNCED,
    LitellmClient,
    LitellmError,
    LitellmRoute,
)

KEY = "sk-test-0000-made-up-key"
ROUTE = LitellmRoute(
    alias="qwen-worker",
    api_type="openai-chat",
    upstream_model="qwen3-example",
    api_base="http://upstream.example/v1",
)
OK: tuple[int, Any] = (200, {})


class FakeLitellm:
    def __init__(self) -> None:
        self.replies: list[tuple[int, Any]] = [OK]
        self.requests: list[tuple[str, str, str, Any]] = []
        self._server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._server.server_address[1]}"

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()

    def _handler(self) -> type[http.server.BaseHTTPRequestHandler]:
        fake = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def _answer(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                body = json.loads(raw) if raw else None
                fake.requests.append(
                    (self.command, self.path, self.headers.get("Authorization", ""), body)
                )
                status, payload = fake.replies.pop(0) if len(fake.replies) > 1 else fake.replies[0]
                data = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            do_GET = _answer
            do_POST = _answer

            def log_message(self, format: str, *args: Any) -> None:
                pass

        return Handler


@pytest.fixture
def fake_litellm() -> Iterator[FakeLitellm]:
    server = FakeLitellm()
    server.start()
    try:
        yield server
    finally:
        server.stop()


def _client(url: str, *, enabled: bool = True) -> LitellmClient:
    return LitellmClient(url, KEY, enabled=enabled, retry_delay_seconds=0.0)


def test_flag_off_makes_no_request(fake_litellm: FakeLitellm) -> None:
    client = _client(fake_litellm.url, enabled=False)
    assert client.register(ROUTE) is False
    assert client.sync_state(ROUTE) is None
    assert fake_litellm.requests == []


def test_anthropic_route_is_never_sent(fake_litellm: FakeLitellm) -> None:
    client = _client(fake_litellm.url)
    direct = LitellmRoute("opus", "anthropic", "claude-opus-4-8", "https://api.anthropic.com")
    assert client.register(direct) is False
    assert client.sync_state(direct) is None
    assert fake_litellm.requests == []


def test_register_sends_the_exact_payload(fake_litellm: FakeLitellm) -> None:
    assert _client(fake_litellm.url).register(ROUTE) is True
    assert fake_litellm.requests == [
        (
            "POST",
            "/model/new",
            f"Bearer {KEY}",
            {
                "model_name": "qwen-worker",
                "litellm_params": {
                    "model": "openai/qwen3-example",
                    "api_base": "http://upstream.example/v1",
                },
            },
        )
    ]


def test_sync_state_reports_listed_model(fake_litellm: FakeLitellm) -> None:
    listed = {
        "data": [
            {
                "model_name": "qwen-worker",
                "litellm_params": {"model": "openai/qwen3-example"},
            }
        ]
    }
    fake_litellm.replies = [(200, listed)]
    assert _client(fake_litellm.url).sync_state(ROUTE) == SYNCED
    assert fake_litellm.requests[0][:2] == ("GET", "/model/info")


def test_sync_state_reports_missing_model(fake_litellm: FakeLitellm) -> None:
    fake_litellm.replies = [(200, {"data": [{"model_name": "*", "litellm_params": {}}]})]
    assert _client(fake_litellm.url).sync_state(ROUTE) == NOT_SYNCED


def test_server_error_is_retried_then_succeeds(fake_litellm: FakeLitellm) -> None:
    fake_litellm.replies = [(503, {}), (500, {}), OK]
    assert _client(fake_litellm.url).register(ROUTE) is True
    assert len(fake_litellm.requests) == 3


def test_server_error_gives_up_after_bounded_attempts(fake_litellm: FakeLitellm) -> None:
    fake_litellm.replies = [(503, {"error": "down"})]
    with pytest.raises(LitellmError, match="failed after 3 attempts: HTTP 503"):
        _client(fake_litellm.url).register(ROUTE)
    assert len(fake_litellm.requests) == 3


def test_client_error_is_not_retried(fake_litellm: FakeLitellm) -> None:
    fake_litellm.replies = [(400, {"error": "bad model"})]
    with pytest.raises(LitellmError, match="HTTP 400"):
        _client(fake_litellm.url).register(ROUTE)
    assert len(fake_litellm.requests) == 1


def test_unreachable_server_is_a_bounded_error() -> None:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    with pytest.raises(LitellmError, match="failed after 3 attempts") as caught:
        _client(f"http://127.0.0.1:{port}").sync_state(ROUTE)
    assert KEY not in str(caught.value)


def test_key_is_not_in_the_error_text(fake_litellm: FakeLitellm) -> None:
    fake_litellm.replies = [(500, {"error": KEY})]
    with pytest.raises(LitellmError) as caught:
        _client(fake_litellm.url).register(ROUTE)
    assert KEY not in str(caught.value)


def test_enabled_without_url_is_refused() -> None:
    with pytest.raises(ValueError, match="litellm URL is not set"):
        LitellmClient("", KEY, enabled=True)
