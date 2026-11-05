import json
from typing import Any

import pytest

from ahawr_runner import call_net

TOOLS = [
    {
        "name": "Bash",
        "input_schema": {
            "type": "object",
            "properties": {"command": {"type": "string"}, "description": {"type": "string"}},
            "required": ["command"],
        },
    },
    {
        "name": "Read",
        "input_schema": {
            "properties": {"file_path": {"type": "string"}},
            "required": ["file_path"],
        },
    },
]
SCHEMAS = call_net.schemas_of({"tools": TOOLS})
BODY = (
    "<parameter=command>\ngrep -n x f | head\n</parameter>\n"
    "<parameter=description>\nlook\n</parameter>\n"
)
WANT = [("Bash", {"command": "grep -n x f | head", "description": "look"})]


def raw(opener: str, middle: str = "") -> str:
    return f"<tool_call>\n{opener}\n{middle}{BODY}</function>\n</tool_call>"


@pytest.mark.parametrize(
    ("opener", "middle"),
    [
        ("<function=Bash>", ""),
        ("<function=Bash=", ""),
        ("<function=Bash", ""),
        ("<function=Bash=skip>", ""),
        ("<function=Bash=none", "</parameter>\n"),
        ("<function=Bash=", "</command>\n"),
    ],
)
def test_the_seen_openers_are_read_as_the_call(opener: str, middle: str) -> None:
    assert call_net.parse_raw_calls(raw(opener, middle), SCHEMAS) == WANT


def test_a_parameter_name_folded_into_the_opener() -> None:
    text = (
        "<tool_call>\n<function=Bash=command>\ngrep -n x f | head\n</parameter>\n"
        "<parameter=description>\nlook\n</parameter>\n</function>\n</tool_call>"
    )
    assert call_net.parse_raw_calls(text, SCHEMAS) == WANT


@pytest.mark.parametrize(
    "text",
    [
        "<tool_call>\n=\n</function>\n</tool_call>",
        "<tool_call>\n<function=Bjsck>\n" + BODY + "</function>\n</tool_call>",
        "<tool_call>\n<function=Bash=\n</function>\n</tool_call>",
        "an ordinary answer that mentions <function=Bash>",
        "",
    ],
)
def test_what_cannot_be_read_stays_unread(text: str) -> None:
    assert call_net.parse_raw_calls(text, SCHEMAS) is None


def test_two_calls_in_one_text() -> None:
    text = raw("<function=Bash=") + "\n" + raw("<function=Bash")
    assert call_net.parse_raw_calls(text, SCHEMAS) == WANT + WANT


def event(kind: str, **fields: Any) -> bytes:
    return f"event: {kind}\ndata: {json.dumps({'type': kind, **fields})}\n\n".encode()


def stream(text_parts: list[str]) -> bytes:
    events = [
        event("message_start", message={"id": "m", "content": []}),
        event("content_block_start", index=0, content_block={"type": "thinking", "thinking": ""}),
        event("content_block_delta", index=0, delta={"type": "thinking_delta", "thinking": "hm"}),
        event("content_block_stop", index=0),
        event("content_block_start", index=1, content_block={"type": "text", "text": ""}),
    ]
    events += [
        event("content_block_delta", index=1, delta={"type": "text_delta", "text": t})
        for t in text_parts
    ]
    events += [
        event("content_block_stop", index=1),
        event("message_delta", delta={"stop_reason": "end_turn"}, usage={"output_tokens": 9}),
        event("message_stop"),
    ]
    return b"".join(events)


def run(
    data: bytes, width: int, allow_feedback: bool = False
) -> tuple[list[dict[str, Any]], call_net.SseNet]:
    net = call_net.SseNet(SCHEMAS, allow_feedback)
    out = b""
    for k in range(0, len(data), width):
        out += b"".join(net.feed(data[k : k + width]))
    out += b"".join(net.finish())
    payloads = [
        json.loads(line[5:]) for line in out.decode().split("\n") if line.startswith("data:")
    ]
    return payloads, net


@pytest.mark.parametrize("width", [10_000, 64, 7])
def test_a_raw_call_becomes_a_tool_use_block(width: int) -> None:
    text = raw("<function=Bash=")
    payloads, net = run(stream([text[:20], text[20:]]), width)
    kinds = [p["type"] for p in payloads]
    assert kinds == [
        "message_start",
        "content_block_start",
        "content_block_delta",
        "content_block_stop",
        "content_block_start",
        "content_block_delta",
        "content_block_stop",
        "message_delta",
        "message_stop",
    ]
    block = payloads[4]["content_block"]
    assert (block["type"], block["name"]) == ("tool_use", "Bash")
    assert json.loads(payloads[5]["delta"]["partial_json"]) == WANT[0][1]
    assert payloads[7]["delta"]["stop_reason"] == "tool_use"
    assert net.rewrote


@pytest.mark.parametrize("width", [10_000, 5])
def test_an_ordinary_answer_passes_through_unchanged(width: int) -> None:
    data = stream(["Done. ", "The file is fixed."])
    net = call_net.SseNet(SCHEMAS, True)
    out = b""
    for k in range(0, len(data), width):
        out += b"".join(net.feed(data[k : k + width]))
    out += b"".join(net.finish())
    assert out == data
    assert not net.rewrote


def test_an_unreadable_call_passes_as_text_without_feedback() -> None:
    text = "<tool_call>\n=\n</function>\n</tool_call>"
    payloads, net = run(stream([text]), 10_000)
    assert [p["type"] for p in payloads].count("content_block_delta") == 2
    assert payloads[-2]["delta"]["stop_reason"] == "end_turn"
    assert not net.rewrote


def test_an_unreadable_call_gets_the_feedback_tool_use_when_allowed() -> None:
    payloads, net = run(
        stream(["<tool_call>\n=\n</function>\n</tool_call>"]), 10_000, allow_feedback=True
    )
    block = payloads[4]["content_block"]
    assert block["name"] == "Bash"
    assert call_net.FEEDBACK_MARK in json.loads(payloads[5]["delta"]["partial_json"])["command"]
    assert payloads[7]["delta"]["stop_reason"] == "tool_use"
    assert net.rewrote


def test_the_feedback_stops_after_two_in_a_row() -> None:
    result = {
        "role": "user",
        "content": [{"type": "tool_result", "content": call_net.FEEDBACK_TEXT}],
    }
    assistant = {"role": "assistant", "content": [{"type": "text", "text": "x"}]}
    assert call_net.feedback_streak({"messages": [assistant]}) == 0
    assert (
        call_net.feedback_streak({"messages": [result, assistant, result]})
        == call_net.MAX_FEEDBACK_IN_ROW
    )
    assert call_net.feedback_streak({}) == 0


def test_a_ping_inside_a_held_block_does_not_end_the_hold() -> None:
    text = raw("<function=Bash=")
    data = stream([text[:20], text[20:]])
    ping = event("ping")
    cut = data.index(b"event: content_block_delta", data.index(b'"index": 1'))
    payloads, net = run(data[:cut] + ping + data[cut:], 10_000)
    assert net.rewrote
    assert [p["type"] for p in payloads].count("ping") == 1
