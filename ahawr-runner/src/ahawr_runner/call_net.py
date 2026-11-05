"""Safety net for a tool call an engine returned as plain text.

A small quant sometimes writes the opener of a call wrong (``<function=Bash=``,
``<function=Bash=command>``, no ``>``), the engine's parser keeps the whole call as text, Claude
Code takes the text for the final answer, and the run ends with nothing done.  The think proxy
runs this net on the streamed reply of ``/v1/messages``:

* a text block that starts like a call and can be read (a declared tool, its parameters) becomes
  a ``tool_use`` block;
* one that cannot be read becomes a ``tool_use`` of the Bash tool that prints a request to send
  the call again (``feedback``), at most ``MAX_FEEDBACK_IN_ROW`` times in a row, so the model gets
  the answer in the same turn;
* any other text passes through unchanged, as does everything while the net is off.
"""

from __future__ import annotations

import json
import re
import uuid
from typing import Any

RAW_MARKS = ("<tool_call>", "<function=")
FEEDBACK_MARK = "[proxy] malformed tool call"
FEEDBACK_TEXT = (
    FEEDBACK_MARK
    + ": the previous reply wrote a tool call as plain text and it could not be read. "
    "Send the call again in the exact form: a tool_call tag, then function equals NAME closed by "
    "a greater-than sign, then parameter equals NAME closed by a greater-than sign, the value, "
    "and the closing tags."
)
MAX_FEEDBACK_IN_ROW = 2
FEEDBACK_LOOKBACK_MESSAGES = 6

_NAME = re.compile(r"<function=([A-Za-z0-9_.\-]+)")
_FOLDED = re.compile(r"=([A-Za-z0-9_.\-]*)>")
_PARAM = re.compile(r"<parameter=([A-Za-z0-9_.\-]+)>\n?(.*?)\n?</parameter>", re.S)

Schemas = dict[str, dict[str, Any]]
Call = tuple[str, dict[str, Any]]


def schemas_of(body: dict[str, Any]) -> Schemas:
    """The declared tools of a request: name -> input_schema ({} when a tool has none)."""
    tools = body.get("tools")
    out: Schemas = {}
    if isinstance(tools, list):
        for tool in tools:
            if isinstance(tool, dict) and isinstance(tool.get("name"), str):
                schema = tool.get("input_schema")
                out[tool["name"]] = schema if isinstance(schema, dict) else {}
    return out


def _typed(value: str, declared: Any) -> Any:
    if declared == "string" or declared is None:
        return value
    try:
        return json.loads(value)
    except ValueError:
        return value


def _one_call(chunk: str, schemas: Schemas) -> Call | None:
    found = _NAME.search(chunk)
    if found is None or found.group(1) not in schemas:
        return None
    name = found.group(1)
    props = schemas[name].get("properties") or {}
    rest = chunk[found.end() :]
    args: dict[str, Any] = {}
    header, _, after = rest.partition("\n")
    folded = _FOLDED.search(header)
    if folded and folded.group(1) in props and not after.lstrip().startswith("<"):
        value, sep, tail = after.partition("</parameter>")
        if sep:
            args[folded.group(1)] = _typed(
                value.strip("\n"), (props[folded.group(1)] or {}).get("type")
            )
            rest = tail
    for pname, value in _PARAM.findall(rest):
        args[pname] = _typed(value, (props.get(pname) or {}).get("type"))
    if any(key not in args for key in schemas[name].get("required") or []):
        return None
    return name, args


def parse_raw_calls(text: str, schemas: Schemas) -> list[Call] | None:
    """The calls written in ``text`` when all of them can be read, else None.  Text that does
    not start like a call is not touched (None)."""
    stripped = text.strip()
    if not stripped.startswith(RAW_MARKS):
        return None
    chunks = [c for c in re.split(r"<tool_call>", stripped) if c.strip()]
    calls = [_one_call(chunk, schemas) for chunk in chunks]
    if not calls or any(call is None for call in calls):
        return None
    return [call for call in calls if call is not None]


def feedback_call(schemas: Schemas) -> Call | None:
    if "command" not in (schemas.get("Bash", {}).get("properties") or {}):
        return None
    return "Bash", {
        "command": f"echo '{FEEDBACK_TEXT}'",
        "description": "proxy: malformed tool call",
    }


def feedback_streak(body: dict[str, Any]) -> int:
    """How many of the latest tool results of the request already carry the feedback text."""
    messages = body.get("messages")
    if not isinstance(messages, list):
        return 0
    return sum(1 for m in messages[-FEEDBACK_LOOKBACK_MESSAGES:] if FEEDBACK_MARK in json.dumps(m))


def _event(kind: str, payload: dict[str, Any]) -> bytes:
    return f"event: {kind}\ndata: {json.dumps(payload)}\n\n".encode()


def tool_use_events(index: int, call: Call) -> list[bytes]:
    name, args = call
    block = {"type": "tool_use", "id": "toolu_" + uuid.uuid4().hex[:24], "name": name, "input": {}}
    return [
        _event(
            "content_block_start",
            {"type": "content_block_start", "index": index, "content_block": block},
        ),
        _event(
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": index,
                "delta": {"type": "input_json_delta", "partial_json": json.dumps(args)},
            },
        ),
        _event("content_block_stop", {"type": "content_block_stop", "index": index}),
    ]


def _could_be_raw(text: str) -> bool:
    start = text.lstrip()
    return any(mark.startswith(start) or start.startswith(mark) for mark in RAW_MARKS)


class SseNet:
    """Rewrites a streamed Anthropic reply: feed raw chunks, send what ``feed`` returns."""

    def __init__(self, schemas: Schemas, allow_feedback: bool) -> None:
        self.schemas = schemas
        self.allow_feedback = allow_feedback
        self.rewrote = False
        self.rewritten_calls = 0
        self._buf = b""
        self._held: list[bytes] = []
        self._holding = False
        self._text = ""
        self._index = 0

    def feed(self, chunk: bytes) -> list[bytes]:
        self._buf += chunk.replace(b"\r\n", b"\n")
        out: list[bytes] = []
        while b"\n\n" in self._buf:
            raw, self._buf = self._buf.split(b"\n\n", 1)
            out.extend(self._event(raw + b"\n\n"))
        return out

    def finish(self) -> list[bytes]:
        rest = [self._buf] if self._buf else []
        self._buf = b""
        held, self._held, self._holding = self._held, [], False
        return held + rest

    def _event(self, raw: bytes) -> list[bytes]:
        data = next((ln[5:].strip() for ln in raw.split(b"\n") if ln.startswith(b"data:")), b"")
        try:
            payload = json.loads(data) if data else {}
        except ValueError:
            return self._pass(raw)
        kind = payload.get("type") if isinstance(payload, dict) else None
        if kind == "ping":
            return [raw]
        if (
            kind == "content_block_start"
            and (payload.get("content_block") or {}).get("type") == "text"
        ):
            self._holding, self._held, self._index = True, [raw], int(payload.get("index", 0))
            self._text = str((payload.get("content_block") or {}).get("text") or "")
            return []
        if self._holding and kind == "content_block_delta":
            self._text += str((payload.get("delta") or {}).get("text") or "")
            self._held.append(raw)
            return self._release() if self._text.strip() and not _could_be_raw(self._text) else []
        if self._holding and kind == "content_block_stop":
            return self._close(raw)
        if kind == "message_delta" and self.rewrote:
            payload.setdefault("delta", {})["stop_reason"] = "tool_use"
            return [_event("message_delta", payload)]
        return self._pass(raw)

    def _pass(self, raw: bytes) -> list[bytes]:
        return self._release() + [raw] if self._holding else [raw]

    def _release(self) -> list[bytes]:
        held, self._held, self._holding = self._held, [], False
        return held

    def _close(self, stop: bytes) -> list[bytes]:
        calls = parse_raw_calls(self._text, self.schemas)
        if calls is None and self.allow_feedback and self._text.strip().startswith(RAW_MARKS):
            feedback = feedback_call(self.schemas)
            calls = [feedback] if feedback else None
        if calls is None:
            return self._release() + [stop]
        self._held, self._holding, self.rewrote = [], False, True
        self.rewritten_calls += len(calls)
        return [e for i, call in enumerate(calls) for e in tool_use_events(self._index + i, call)]
