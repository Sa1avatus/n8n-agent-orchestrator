"""Export of runs and sessions: the dashboard's activity entries as Markdown or JSON.

Markdown reads like the dashboard's ledger: turns, steps with timings and tokens, thinking,
answers, tool calls with their results, the retrieval (RAG) context the prompt carried, retries
and the outcome. JSON keeps every entry as the dashboard received it.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import Any

_RAG_START = re.compile(
    r"^=== RETRIEVED CONTEXT profile=(?P<profile>\S+) request=(?P<request>\S+) "
    r"chunks=(?P<chunks>\d+) ===$",
    re.MULTILINE,
)
_RAG_END = "=== END RETRIEVED CONTEXT ==="


def rag_blocks(text: str) -> list[dict[str, Any]]:
    """Retrieval context blocks (ahawr-retrieval ``render_context``) found in a prompt."""
    blocks = []
    for match in _RAG_START.finditer(text or ""):
        end = text.find(_RAG_END, match.end())
        body = text[match.end() : end if end >= 0 else len(text)]
        chunks = []
        for line in body.splitlines():
            if not line.startswith("--- ["):
                continue
            parts = [part.strip() for part in line[4:].split(" | ")]
            chunk: dict[str, Any] = {"authority": parts[0].split("] ", 1)[-1]}
            for part in parts[1:]:
                key, _, value = part.partition("=")
                if key in ("path", "lines", "symbol", "section", "freshness", "score", "chunk"):
                    chunk[key] = value
            chunks.append(chunk)
        blocks.append(
            {
                "profile": match.group("profile"),
                "request_id": match.group("request"),
                "chunks": chunks,
                "chars": len(body),
            }
        )
    return blocks


def _fence(text: str, lang: str = "") -> str:
    longest = max((len(m) for m in re.findall(r"`{3,}", text)), default=2)
    ticks = "`" * (longest + 1)
    return f"{ticks}{lang}\n{text}\n{ticks}"


def _ms(value: Any) -> str:
    if not isinstance(value, int | float):
        return ""
    if value < 1000:
        return f"{round(value)} ms"
    seconds = value / 1000
    return (
        f"{seconds:.2f} s" if seconds < 60 else f"{int(seconds // 60)}m{round(seconds % 60):02d}s"
    )


def _tokens(value: Any) -> str:
    if not isinstance(value, int | float):
        return "–"
    return f"{value / 1000:.1f}K" if value >= 10_000 else str(round(value))


def _clock(stamp: Any) -> str:
    if not isinstance(stamp, int | float):
        return ""
    return datetime.fromtimestamp(stamp, UTC).strftime("%H:%M:%S")


def _details(summary: str, body: str) -> str:
    return f"<details><summary>{summary}</summary>\n\n{body}\n\n</details>"


def _step_line(step: dict[str, Any]) -> str:
    bits = [f"Step {step.get('step')}"]
    if step.get("model"):
        bits.append(str(step["model"]))
    if step.get("duration_ms") is not None:
        bits.append(("≈" if step.get("approximate") else "") + _ms(step["duration_ms"]))
    if step.get("ttft_ms") is not None:
        bits.append(f"TTFT {_ms(step['ttft_ms'])}")
    if step.get("generation_ms") is not None:
        bits.append(f"gen {_ms(step['generation_ms'])}")
    total_in = sum(
        int(step.get(k) or 0) for k in ("input_tokens", "cache_read_tokens", "cache_write_tokens")
    )
    if total_in:
        cached = int(step.get("cache_read_tokens") or 0)
        bits.append(f"in {_tokens(total_in)}" + (f" (cache {_tokens(cached)})" if cached else ""))
    if step.get("output_tokens"):
        bits.append(f"out {_tokens(step['output_tokens'])}")
    if step.get("tokens_per_s"):
        bits.append(f"{step['tokens_per_s']} tok/s")
    if step.get("finish_reason"):
        bits.append(str(step["finish_reason"]))
    return "#### " + " · ".join(bits)


def run_markdown(run: dict[str, Any], entries: list[dict[str, Any]], level: int = 1) -> str:
    """One run (or Hermes session) as Markdown."""
    h = "#" * level
    source = "Hermes" if run.get("source") == "hermes" else "Claude Code"
    out = [f"{h} {run.get('title') or run.get('run_id')}", ""]
    meta = [
        ("Source", source),
        ("Role", run.get("role")),
        ("Status", run.get("status")),
        ("Model", run.get("model")),
        ("Provider", run.get("provider")),
        ("Session", run.get("session_id")),
        ("Run", run.get("run_id")),
        ("Working directory", run.get("cwd")),
        ("Started", run.get("started_at") or run.get("created_at")),
        ("Finished", run.get("finished_at")),
        ("Turns", run.get("num_turns")),
        (
            "Cost (USD)",
            f"{run['cost_usd']:.4f}" if isinstance(run.get("cost_usd"), float) else None,
        ),
    ]
    out += [f"- **{k}:** {v}" for k, v in meta if v not in (None, "")]
    if run.get("error"):
        out.append(f"- **Error:** {run['error'].get('code')}: {run['error'].get('message')}")
    out.append("")

    steps = {e["step"]: e for e in entries if e.get("kind") == "step"}
    results = {e.get("id"): e for e in entries if e.get("kind") == "tool_result"}
    uses = {e.get("id") for e in entries if e.get("kind") == "tool_use"}
    turn, last_step = 0, None
    for entry in entries:
        kind = entry.get("kind")
        if kind == "prompt":
            turn += 1
            text = str(entry.get("text") or "")
            out += [f"{h}## Turn {turn}", ""]
            out.append(_details(f"Prompt ({len(text)} chars)", _fence(text, "text")))
            for block in rag_blocks(text):
                listing = "\n".join(
                    f"- `{c.get('path', '?')}:{c.get('lines', '')}` {c.get('authority', '')}"
                    + (f" · {c['symbol']}" if c.get("symbol") else "")
                    + (f" · score {c['score']}" if c.get("score") else "")
                    for c in block["chunks"]
                )
                header = (
                    f"**RAG context:** {len(block['chunks'])} chunks "
                    f"(profile {block['profile']}, request `{block['request_id']}`)"
                )
                out += ["", header, listing]
            out.append("")
            continue
        step_no = entry.get("step")
        if step_no is not None and not entry.get("agent") and step_no != last_step:
            out += [_step_line(steps.get(step_no, {"step": step_no})), ""]
            last_step = step_no
        prefix = "> ↳ subagent\n" if entry.get("agent") else ""
        if kind == "thinking":
            quoted = "\n".join("> " + line for line in str(entry.get("text") or "").splitlines())
            out += [prefix + "> 💭 *thinking*\n" + quoted, ""]
        elif kind == "text":
            out += [prefix + str(entry.get("text") or ""), ""]
        elif kind == "tool_use":
            result = results.get(entry.get("id"))
            took = ""
            if (
                result
                and isinstance(result.get("t"), int | float)
                and isinstance(entry.get("t"), int | float)
            ):
                took = f" — {_ms((result['t'] - entry['t']) * 1000)}"
            status = " ✕ error" if result and result.get("is_error") else ""
            out.append(f"{prefix}**Tool `{entry.get('name')}`**{took}{status}")
            out.append(_fence(json.dumps(entry.get("input"), ensure_ascii=False, indent=2), "json"))
            if result:
                text = str(result.get("text") or "")
                out.append(_details(f"Result ({len(text.splitlines())} lines)", _fence(text)))
            else:
                out.append("*(no result recorded)*")
            out.append("")
        elif kind == "tool_result" and entry.get("id") not in uses:
            out += [_details("Tool result", _fence(str(entry.get("text") or ""))), ""]
        elif kind == "init":
            out += [
                f"*Claude Code {entry.get('version')} · {entry.get('model')} · "
                f"{len(entry.get('tools') or [])} tools · {entry.get('permission_mode')} · "
                f"{entry.get('cwd')}*",
                "",
            ]
        elif kind == "retry":
            out += [
                f"⚠️ API retry {entry.get('attempt')}/{entry.get('max_retries')}: "
                f"{entry.get('status') or ''} {entry.get('error') or ''}",
                "",
            ]
        elif kind == "compact":
            out += [
                f"🗜 Context compacted ({entry.get('trigger')}): "
                f"{_tokens(entry.get('pre_tokens'))} → {_tokens(entry.get('post_tokens'))} tokens"
                + (f" in {entry['duration_ms'] / 1000:.0f} s" if entry.get("duration_ms") else ""),
                "",
            ]
        elif kind == "result":
            bits = [str(entry.get("subtype") or "")]
            if entry.get("num_turns") is not None:
                bits.append(f"{entry['num_turns']} turns")
            if entry.get("duration_ms"):
                bits.append(_ms(entry["duration_ms"]))
            if entry.get("cost_usd") is not None:
                bits.append(f"${entry['cost_usd']:.4f}")
            usage = entry.get("usage") or {}
            if usage:
                bits.append(
                    f"tokens in {_tokens(usage.get('input_tokens'))} / cache read "
                    f"{_tokens(usage.get('cache_read_input_tokens'))} / out "
                    f"{_tokens(usage.get('output_tokens'))}"
                )
            out += [f"**Result:** {' · '.join(b for b in bits if b)}", ""]
            if entry.get("is_error") and entry.get("text"):
                out += [_fence(str(entry["text"])), ""]
        elif kind == "end":
            line = f"**Run {entry.get('status')}**"
            if entry.get("error"):
                line += f": {entry.get('error_code')}: {entry.get('error')}"
            out += [line, ""]
            if entry.get("stderr_tail") and entry.get("status") != "completed":
                out += [_details("stderr", _fence(str(entry["stderr_tail"]))), ""]
    return "\n".join(out).rstrip() + "\n"


def session_markdown(session_id: str, runs: list[dict[str, Any]]) -> str:
    """Several runs of one session, oldest first."""
    if len(runs) == 1:
        return run_markdown(runs[0]["run"], runs[0]["events"])
    head = [f"# Session {session_id}", "", f"{len(runs)} runs, oldest first.", ""]
    parts = [run_markdown(item["run"], item["events"], level=2) for item in runs]
    return "\n".join(head) + "\n\n---\n\n".join(parts)


def filename(run: dict[str, Any], suffix: str, prefix: str = "") -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", str(run.get("title") or "")).strip("-")[:48]
    rid = str(run.get("run_id") or run.get("session_id") or "run").replace(":", "-")[-12:]
    return f"ahawr-{prefix}{run.get('role') or 'run'}-{slug or 'untitled'}-{rid}.{suffix}"
