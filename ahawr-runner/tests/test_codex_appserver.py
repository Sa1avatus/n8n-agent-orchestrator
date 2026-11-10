import json
from typing import Any

import pytest

from ahawr_runner.codex_appserver import AppServerMapper, AppServerSession, exec_item
from ahawr_runner.codex_events import classify_failure

THREAD = "01a1c39f-5ada-7e63-a859-d5113df80100"


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def line(**message: Any) -> str:
    return json.dumps(message)


def make(
    resume: bool = False,
) -> tuple[AppServerSession, AppServerMapper, list[dict[str, Any]], Clock]:
    clock = Clock()
    sent: list[dict[str, Any]] = []
    mapper = AppServerMapper("qwen", clock)
    session = AppServerSession(
        mapper,
        lambda text: sent.append(json.loads(text)),
        cwd="/work",
        prompt="do it",
        thread_id=THREAD,
        resume=resume,
    )
    return session, mapper, sent, clock


def handshake(session: AppServerSession) -> None:
    session.start()
    session.feed(line(id=1, result={}))
    session.feed(line(id=2, result={"thread": {"id": THREAD}}))


def usage(last: dict[str, int]) -> str:
    return line(method="thread/tokenUsage/updated", params={"tokenUsage": {"last": last}})


def test_handshake_starts_the_thread_then_the_turn() -> None:
    session, mapper, sent, _clock = make()
    handshake(session)
    assert [m.get("method") for m in sent] == [
        "initialize",
        "initialized",
        "thread/start",
        "turn/start",
    ]
    assert sent[2]["params"] == {"cwd": "/work"}
    assert sent[3]["params"]["input"] == [{"type": "text", "text": "do it"}]
    assert sent[3]["params"]["threadId"] == THREAD
    assert mapper.session_id == THREAD


def test_resume_asks_for_the_stored_thread() -> None:
    session, _mapper, sent, _clock = make(resume=True)
    session.start()
    session.feed(line(id=1, result={}))
    assert sent[2]["method"] == "thread/resume"
    assert sent[2]["params"] == {"threadId": THREAD, "cwd": "/work"}


def test_resume_rejects_an_id_that_is_not_a_thread_id() -> None:
    with pytest.raises(ValueError):
        AppServerSession(
            AppServerMapper("m"), lambda _t: None, cwd="/", prompt="p", thread_id="x", resume=True
        )


def test_reasoning_tool_and_answer_entries_with_the_request_step() -> None:
    session, mapper, _sent, clock = make()
    handshake(session)
    session.feed(line(method="turn/started", params={}))
    reasoning = {"type": "reasoning", "id": "r1", "summary": [], "content": ["17*23 = 391."]}
    command = {
        "type": "commandExecution",
        "id": "c1",
        "command": "echo 391",
        "status": "completed",
        "exitCode": 0,
        "aggregatedOutput": "391\n",
    }
    clock.now += 1
    session.feed(line(method="item/started", params={"item": {**reasoning, "content": [""]}}))
    clock.now += 2
    entries = session.feed(line(method="item/completed", params={"item": reasoning}))
    assert entries == [{"kind": "thinking", "text": "17*23 = 391.", "step": 1}]
    clock.now += 1
    started = session.feed(
        line(method="item/started", params={"item": {**command, "status": "inProgress"}})
    )
    assert started[0]["kind"] == "tool_use" and started[0]["name"] == "Bash"
    done = session.feed(line(method="item/completed", params={"item": command}))
    assert done == [{"kind": "tool_result", "id": "c1", "is_error": False, "text": "391\n"}]
    steps = session.feed(
        usage(
            {
                "inputTokens": 1000,
                "cachedInputTokens": 900,
                "outputTokens": 40,
                "reasoningOutputTokens": 30,
            }
        )
    )
    assert steps[0]["step"] == 1
    assert steps[0]["input_tokens"] == 100 and steps[0]["cache_read_tokens"] == 900
    assert steps[0]["output_tokens"] == 40 and steps[0]["model"] == "qwen"
    assert steps[0]["approximate"] is True
    assert steps[0]["generation_ms"] == 4000 and steps[0]["tokens_per_s"] == 10.0
    message = {"type": "agentMessage", "id": "m1", "text": "391"}
    answer = session.feed(line(method="item/completed", params={"item": message}))
    assert answer[0] == {"kind": "text", "text": "391", "step": 2}
    session.feed(usage({"inputTokens": 1100, "cachedInputTokens": 1000, "outputTokens": 5}))
    session.feed(line(method="turn/completed", params={"turn": {"status": "completed"}}))
    assert session.done and mapper.final_message == "391"
    assert mapper.step_count == 2 and mapper.usage["cache_read_tokens"] == 1900


def test_user_message_items_are_not_shown() -> None:
    assert exec_item({"type": "userMessage", "id": "u"}) is None
    declined = exec_item({"type": "commandExecution", "status": "declined", "exitCode": None})
    assert declined is not None and declined["status"] == "failed"


def test_failed_turn_is_classified_from_its_error() -> None:
    session, mapper, _sent, _clock = make()
    handshake(session)
    error = {"message": "limit", "codexErrorInfo": "usageLimitExceeded"}
    session.feed(
        line(method="turn/completed", params={"turn": {"status": "failed", "error": error}})
    )
    assert session.done
    assert classify_failure(mapper.turn_failed, 0) == ("permanent", "usage_limit")


def test_request_error_fails_the_run_and_a_silent_end_too() -> None:
    session, mapper, _sent, _clock = make()
    session.start()
    session.feed(line(id=1, error={"message": "bad config"}))
    assert session.failed and mapper.turn_failed == {"error": {"message": "bad config"}}
    other, other_mapper, _sent2, _clock2 = make()
    handshake(other)
    other.server_ended()
    assert other.failed and "ended before the turn completed" in str(other_mapper.turn_failed)


def test_a_server_request_is_declined_and_noticed() -> None:
    session, _mapper, sent, _clock = make()
    handshake(session)
    entries = session.feed(line(id=9, method="item/commandExecution/requestApproval", params={}))
    assert sent[-1]["id"] == 9 and sent[-1]["error"]["code"] == -32601
    assert entries[0]["kind"] == "notice"


def test_usage_replayed_by_a_resumed_thread_is_not_a_step() -> None:
    session, mapper, _sent, _clock = make(resume=True)
    session.start()
    session.feed(line(id=1, result={}))
    session.feed(line(id=2, result={"thread": {"id": THREAD}}))
    assert (
        session.feed(usage({"inputTokens": 500, "cachedInputTokens": 400, "outputTokens": 9})) == []
    )
    assert mapper.step_count == 0


def test_deltas_feed_the_live_view_like_claude_stream_events() -> None:
    session, mapper, _sent, _clock = make()
    handshake(session)
    session.feed(line(method="item/started", params={"item": {"type": "reasoning", "id": "r"}}))
    session.feed(line(method="item/reasoning/textDelta", params={"delta": "think "}))
    session.feed(line(method="item/reasoning/textDelta", params={"delta": "more"}))
    session.feed(
        line(
            method="item/completed",
            params={"item": {"type": "reasoning", "id": "r", "content": ["x"]}},
        )
    )
    command = {"type": "commandExecution", "id": "c", "command": "ls", "status": "inProgress"}
    session.feed(line(method="item/started", params={"item": command}))
    kinds = [e["event"]["type"] for e in mapper.live_events]
    assert kinds == [
        "content_block_start",
        "content_block_delta",
        "content_block_delta",
        "content_block_stop",
        "content_block_start",
    ]
    assert mapper.live_events[0]["event"]["content_block"] == {"type": "thinking"}
    assert mapper.live_events[1]["event"]["delta"] == {"thinking": "think "}
    assert mapper.live_events[4]["event"]["content_block"] == {"type": "tool_use", "name": "Bash"}
