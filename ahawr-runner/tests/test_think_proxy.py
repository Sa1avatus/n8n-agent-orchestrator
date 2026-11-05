import json
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ahawr_runner import think_proxy
from ahawr_runner.config import ProviderProfile
from ahawr_runner.store import Store


def _profile(base_url: str) -> ProviderProfile:
    return ProviderProfile(name="strata", env={"ANTHROPIC_BASE_URL": base_url})


def test_child_base_url_names_the_provider() -> None:
    assert (
        think_proxy.child_base_url("http://127.0.0.1:8700/", "strata")
        == "http://127.0.0.1:8700/think-proxy/strata"
    )


def test_off_flag_disables_thinking_and_keeps_the_rest() -> None:
    body = json.dumps({"model": "m", "messages": [], "thinking": {"type": "enabled"}}).encode()
    out = json.loads(think_proxy.apply_think_flag(body, "off"))
    assert out["thinking"] == {"type": "disabled"}
    assert out["model"] == "m"


@pytest.mark.parametrize("flag", ["on", "", "other"])
def test_other_flags_leave_the_body_unchanged(flag: str) -> None:
    body = b'{"model": "m"}'
    assert think_proxy.apply_think_flag(body, flag) == body


@pytest.mark.parametrize("body", [b"not json", b"[1, 2]"])
def test_a_body_that_is_no_json_object_passes_through(body: bytes) -> None:
    assert think_proxy.apply_think_flag(body, "off") == body


def _app(flag: str, seen: list[httpx.Request]) -> TestClient:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, stream=httpx.ByteStream(b'{"ok": true}'))

    real_client = httpx.AsyncClient
    transport = httpx.MockTransport(handler)
    think_proxy.httpx.AsyncClient = lambda **kw: real_client(transport=transport, **kw)  # type: ignore[assignment]
    app = FastAPI()
    app.include_router(
        think_proxy.build_router(
            lambda name: _profile("http://strata.test:8080") if name == "strata" else None,
            lambda: ("strata",),
            lambda: flag,
        )
    )
    return TestClient(app, client=("127.0.0.1", 50000))


@pytest.fixture(autouse=True)
def _restore_client() -> object:
    original = think_proxy.httpx.AsyncClient
    yield
    think_proxy.httpx.AsyncClient = original  # type: ignore[misc]


def test_messages_request_is_forwarded_with_thinking_off_and_the_clients_key() -> None:
    seen: list[httpx.Request] = []
    client = _app("off", seen)
    reply = client.post(
        "/think-proxy/strata/v1/messages",
        json={"model": "m", "messages": []},
        headers={"x-api-key": "client-key"},
    )
    assert reply.status_code == 200
    assert str(seen[0].url) == "http://strata.test:8080/v1/messages"
    assert seen[0].headers["x-api-key"] == "client-key"
    assert json.loads(seen[0].content)["thinking"] == {"type": "disabled"}


def test_with_the_flag_on_the_request_keeps_the_engine_default() -> None:
    seen: list[httpx.Request] = []
    _app("on", seen).post("/think-proxy/strata/v1/messages", json={"model": "m"})
    assert "thinking" not in json.loads(seen[0].content)


def test_other_paths_are_never_changed() -> None:
    seen: list[httpx.Request] = []
    _app("off", seen).post("/think-proxy/strata/v1/other", json={"model": "m"})
    assert "thinking" not in json.loads(seen[0].content)


def test_unknown_provider_is_404_and_a_remote_caller_is_403() -> None:
    seen: list[httpx.Request] = []
    client = _app("off", seen)
    assert client.post("/think-proxy/other/v1/messages", json={}).status_code == 404
    remote = TestClient(client.app, client=("10.0.0.5", 50000))
    assert remote.post("/think-proxy/strata/v1/messages", json={}).status_code == 403
    assert seen == []


def _health(monkeypatch: pytest.MonkeyPatch, payload: object) -> list[str]:
    asked: list[str] = []

    class Reply:
        def __enter__(self) -> "Reply":
            return self

        def __exit__(self, *exc: object) -> None:
            return None

        def read(self) -> bytes:
            return json.dumps(payload).encode()

    def fake_urlopen(request: object, timeout: float) -> Reply:
        asked.append(request.full_url)  # type: ignore[attr-defined]
        return Reply()

    monkeypatch.setattr(think_proxy.urllib.request, "urlopen", fake_urlopen)
    think_proxy._probe_cache.clear()
    return asked


def test_the_endpoint_not_the_name_tells_the_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    asked = _health(monkeypatch, {"status": "ok", "service": "strata"})
    assert think_proxy.ignores_client_thinking("http://any-name.test:8080/") is True
    assert asked == ["http://any-name.test:8080/health"]
    # the answer is cached for a minute: no second request
    assert think_proxy.ignores_client_thinking("http://any-name.test:8080/") is True
    assert len(asked) == 1


@pytest.mark.parametrize("payload", [{"status": "ok"}, {"service": "llama"}, ["strata"]])
def test_other_endpoints_are_not_proxied(monkeypatch: pytest.MonkeyPatch, payload: object) -> None:
    _health(monkeypatch, payload)
    assert think_proxy.ignores_client_thinking("http://x.test") is False


def test_an_endpoint_that_does_not_answer_is_not_proxied(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(request: object, timeout: float) -> None:
        raise OSError("refused")

    monkeypatch.setattr(think_proxy.urllib.request, "urlopen", refuse)
    think_proxy._probe_cache.clear()
    assert think_proxy.ignores_client_thinking("http://down.test") is False


def test_store_follows_the_think_providers(tmp_path: Path) -> None:
    store = Store(tmp_path / "runner.sqlite")
    assert store.think_providers == ("local",)
    store.think_providers = ("local", "strata")
    assert store.running_local_workers() == []
    assert store.local_worker_running() is False
    assert store.local_compaction_running() == []
