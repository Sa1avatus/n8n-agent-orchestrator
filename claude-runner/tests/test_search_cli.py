"""Tests for ``search_cli`` — the ``ahawr-search`` on-demand retrieval command."""

from __future__ import annotations

import asyncio
import io
import json
import os
from collections.abc import Callable
from contextlib import redirect_stdout
from typing import Any

import httpx
import pytest

from claude_runner.search_cli import corpus_slug, fetch, main, resolve_root


def _payload(chunks: list[dict[str, Any]]) -> dict[str, Any]:
    return {"chunks": chunks, "context_tokens": 100}


class _CapturingTransport(httpx.MockTransport):
    """Mock transport that records the request it receives."""

    def __init__(self, handler: Callable[[httpx.Request], httpx.Response]) -> None:
        self.request: httpx.Request | None = None
        super().__init__(self._wrap(handler))

    def _wrap(
        self,
        handler: Callable[[httpx.Request], httpx.Response],
    ) -> Callable[[httpx.Request], httpx.Response]:
        def capture(request: httpx.Request) -> httpx.Response:
            self.request = request
            return handler(request)

        return capture


def _run(
    argv: list[str],
    transport: httpx.AsyncBaseTransport | None,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[int, str]:
    """Run ``main`` against an (optional) mock transport; capture stdout."""
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = main(argv, transport=transport)
    return code, buf.getvalue()


# --- resolve_root -----------------------------------------------------------


def test_resolve_root_inside_allowed() -> None:
    resolved, err = resolve_root("/d/rag-tmp/proj")
    assert resolved == "/d/rag-tmp/proj" and err is None


def test_resolve_root_outside_allowed() -> None:
    resolved, err = resolve_root("/home/user/secret")
    assert resolved == "/home/user/secret"
    assert "unavailable to the retrieval service" in err


def test_resolve_root_workspace_allowed() -> None:
    resolved, err = resolve_root("/workspace")
    assert resolved == "/workspace" and err is None


def test_tmp_symlink_resolves_to_d_rag_tmp(monkeypatch: pytest.MonkeyPatch) -> None:
    # Simulate: realpath("/tmp/foo") -> "/d/rag-tmp/foo"
    orig_realpath = os.path.realpath

    def fake_realpath(p: str, *a: Any, **kw: Any) -> str:
        if p.startswith("/tmp/"):
            tail = p.removeprefix("/tmp/")
            return "/d/rag-tmp/" + tail
        return orig_realpath(p, *a, **kw)

    monkeypatch.setattr(os.path, "realpath", fake_realpath)
    resolved, err = resolve_root("/tmp/foo")
    assert resolved == "/d/rag-tmp/foo"
    assert err is None


# --- corpus slug ------------------------------------------------------------


def test_corpus_slug_matches_service_pattern() -> None:
    expected = "d-openaiprojects-job-searching-assistant"
    assert corpus_slug("/d/OpenAIProjects/job-searching-assistant") == expected
    assert corpus_slug("D:\\OpenAIProjects\\job-searching-assistant") == expected
    assert corpus_slug("/workspace") == "workspace"


# --- success path -----------------------------------------------------------


def test_success_prints_path_lines_and_content(monkeypatch: pytest.MonkeyPatch) -> None:
    chunks = [
        {"path": "src/app.py", "start_line": 10, "end_line": 20, "content": "line a\nline b"},
        {"path": "docs/guide.md", "start_line": 5, "end_line": 8, "content": "chunk2"},
    ]
    transport = _CapturingTransport(lambda r: httpx.Response(200, json=_payload(chunks)))
    code, out = _run(["q", "--k", "2", "--root", "/d/rag-tmp/proj"], transport, monkeypatch)
    assert code == 0
    assert "src/app.py:10-20" in out
    assert "line a\nline b" in out
    assert "docs/guide.md:5-8" in out


def test_default_budget_and_profile_in_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = _CapturingTransport(lambda r: httpx.Response(200, json=_payload([])))
    code, _ = _run(["q", "--root", "/d/rag-tmp/proj"], transport, monkeypatch)
    assert code == 0
    body = json.loads(transport.request.content)
    assert body["budget"] == {"max_tokens": 1500}
    assert body["profile"] == "worker"


def test_corpus_roots_and_corpora_from_cwd(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir("/d/rag-tmp")
    transport = _CapturingTransport(lambda r: httpx.Response(200, json=_payload([])))
    code, _ = _run(["q", "--root", "."], transport, monkeypatch)
    assert code == 0
    body = json.loads(transport.request.content)
    assert body["corpora"] == ["d-rag-tmp"]
    assert body["corpus_roots"] == {"d-rag-tmp": "/d/rag-tmp"}


def test_k_limits_fragments(monkeypatch: pytest.MonkeyPatch) -> None:
    chunks = [
        {"path": f"f{i}", "start_line": 1, "end_line": 2, "content": f"c{i}"} for i in range(10)
    ]
    transport = _CapturingTransport(lambda r: httpx.Response(200, json=_payload(chunks)))
    code, out = _run(["q", "--k", "3", "--root", "/workspace"], transport, monkeypatch)
    assert code == 0
    # each fragment: one "path:start-end" line + one content line → 6 lines
    assert len(out.strip().split("\n")) == 6


def test_non_json_200_is_fail_open(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = _CapturingTransport(lambda r: httpx.Response(200, content=b"<html>not json</html>"))
    code, out = _run(["q", "--root", "/workspace"], transport, monkeypatch)
    assert code == 0
    assert "retrieval unavailable" in out


# --- fail-open: connection error, timeout, 5xx --------------------------------


def test_fail_open_connection_error(monkeypatch: pytest.MonkeyPatch) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    transport = _CapturingTransport(handler)
    code, out = _run(["q", "--root", "/workspace"], transport, monkeypatch)
    assert code == 0
    assert "retrieval unavailable" in out


def test_fail_open_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("timed out")

    transport = _CapturingTransport(handler)
    code, out = _run(["q", "--root", "/workspace"], transport, monkeypatch)
    assert code == 0
    assert "retrieval unavailable" in out


def test_fail_open_5xx(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = _CapturingTransport(lambda r: httpx.Response(503, json={"error": "unavailable"}))
    code, out = _run(["q", "--root", "/workspace"], transport, monkeypatch)
    assert code == 0
    assert "retrieval unavailable" in out


# --- env var override -------------------------------------------------------


def test_url_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AHAWR_RETRIEVAL_URL", "http://custom:9999")
    transport = _CapturingTransport(lambda r: httpx.Response(200, json=_payload([])))
    code, _ = _run(["q", "--root", "/workspace"], transport, monkeypatch)
    assert code == 0
    assert transport.request.url == "http://custom:9999/retrieve"


# --- direct fetch checks ----------------------------------------------------


def test_fetch_returns_payload_on_success(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = _CapturingTransport(lambda r: httpx.Response(200, json=_payload([])))
    payload = asyncio.run(
        fetch(
            "http://x", "q", 1500, ["slug"], {"slug": "/d/rag-tmp/proj"}, 10.0, transport=transport
        )
    )
    assert payload == _payload([])


def test_fetch_returns_none_on_4xx(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = _CapturingTransport(lambda r: httpx.Response(404, json={"error": "not found"}))
    payload = asyncio.run(
        fetch(
            "http://x", "q", 1500, ["slug"], {"slug": "/d/rag-tmp/proj"}, 10.0, transport=transport
        )
    )
    assert payload is None
