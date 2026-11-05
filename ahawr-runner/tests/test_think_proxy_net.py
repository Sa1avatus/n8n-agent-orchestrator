import json
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ahawr_runner import think_proxy
from ahawr_runner.config import ProviderProfile

from .test_call_net import SCHEMAS, TOOLS, WANT, raw, stream

assert SCHEMAS

_REAL_CLIENT = httpx.AsyncClient


def _client(
    reply: bytes,
    seen: list[httpx.Request],
    *,
    repair: bool = False,
    feedback: bool = False,
    sampling: dict[str, float] | None = None,
) -> TestClient:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200, headers={"content-type": "text/event-stream"}, stream=httpx.ByteStream(reply)
        )

    transport = httpx.MockTransport(handler)
    think_proxy.httpx.AsyncClient = lambda **kw: _REAL_CLIENT(transport=transport, **kw)  # type: ignore[assignment,misc]
    app = FastAPI()
    app.include_router(
        think_proxy.build_router(
            lambda name: ProviderProfile(
                name="strata", env={"ANTHROPIC_BASE_URL": "http://s.test"}
            ),
            lambda: ("strata",),
            lambda: "on",
            repair=repair,
            feedback=feedback,
            sampling=lambda name: sampling or {},
        )
    )
    return TestClient(app, client=("127.0.0.1", 50000))


@pytest.fixture(autouse=True)
def _restore_client() -> Any:
    yield
    think_proxy.httpx.AsyncClient = _REAL_CLIENT  # type: ignore[misc]


def _payloads(content: bytes) -> list[dict[str, Any]]:
    return [json.loads(ln[5:]) for ln in content.decode().split("\n") if ln.startswith("data:")]


def test_with_the_net_on_a_raw_call_comes_back_as_tool_use() -> None:
    seen: list[httpx.Request] = []
    client = _client(stream([raw("<function=Bash=")]), seen, repair=True)
    reply = client.post(
        "/think-proxy/strata/v1/messages", json={"stream": True, "tools": TOOLS, "messages": []}
    )
    payloads = _payloads(reply.content)
    assert payloads[4]["content_block"]["name"] == "Bash"
    assert json.loads(payloads[5]["delta"]["partial_json"]) == WANT[0][1]
    assert payloads[7]["delta"]["stop_reason"] == "tool_use"


def test_with_the_net_off_the_reply_is_untouched() -> None:
    data = stream([raw("<function=Bash=")])
    reply = _client(data, [], repair=False).post(
        "/think-proxy/strata/v1/messages", json={"stream": True, "tools": TOOLS}
    )
    assert reply.content == data


def test_a_request_without_stream_or_tools_is_not_netted() -> None:
    data = stream([raw("<function=Bash=")])
    for body in ({"tools": TOOLS}, {"stream": True}):
        reply = _client(data, [], repair=True).post("/think-proxy/strata/v1/messages", json=body)
        assert reply.content == data


def test_the_sampling_of_the_provider_is_set_on_messages_only() -> None:
    seen: list[httpx.Request] = []
    client = _client(b"{}", seen, sampling={"temperature": 0.6, "top_k": 10, "bogus": 1})
    client.post("/think-proxy/strata/v1/messages", json={"model": "m", "temperature": 1.0})
    client.post("/think-proxy/strata/v1/other", json={"model": "m"})
    sent = json.loads(seen[0].content)
    assert (sent["temperature"], sent["top_k"], "bogus" in sent) == (0.6, 10, False)
    assert "temperature" not in json.loads(seen[1].content)
