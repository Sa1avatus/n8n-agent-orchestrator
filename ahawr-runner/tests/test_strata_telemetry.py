from __future__ import annotations

from typing import Any

from ahawr_runner.strata_telemetry import TelemetryTail, step_entry

ROW: dict[str, Any] = {
    "id": "a", "time": 100.0, "duration_s": 12.1, "prompt_tokens": 19237, "reused": 16803,
    "output_tokens": 472, "prompt_ms": 3929.3, "decode_ms": 8154.4, "first_engine_token_s": 3.957,
}


def test_step_entry_splits_cache_hits_and_measures_decode_speed() -> None:
    entry = step_entry(ROW, 1, "m")
    assert (entry["input_tokens"], entry["cache_read_tokens"]) == (2434, 16803)
    assert (entry["output_tokens"], entry["generation_ms"], entry["tokens_per_s"]) == (
        472, 8154, 57.9,
    )
    assert entry["ttft_ms"] == 3957


def test_tail_emits_each_request_once_and_skips_older_ones() -> None:
    tail = TelemetryTail(since=100.0, model="m")
    old = {**ROW, "id": "old", "time": 99.0}
    snapshot = {"recent": [ROW, old, {**ROW, "id": "b", "time": 105.0}]}
    first = tail.feed(snapshot)
    assert [e["step"] for e in first] == [1, 2]
    assert tail.feed(snapshot) == []
    assert tail.steps == 2


def test_exact_steps_replace_the_client_speed_with_the_engine_decode_time() -> None:
    from ahawr_runner.strata_telemetry import ExactSteps

    sent: list[dict[str, Any]] = []
    now = [1000.0]
    exact = ExactSteps("http://x", "", sent.append, clock=lambda: now[0])
    step = {
        "kind": "step", "step": 1, "model": "m", "started": 990.0, "duration_ms": 10000,
        "output_tokens": 100, "approximate": True, "generation_ms": 10000, "tokens_per_s": 10.0,
    }
    exact.offer(step)
    assert sent == []  # held until the engine row shows up
    row = {"id": "a", "time": 991.0, "output_tokens": 100, "decode_ms": 2000, "prompt_tokens": 5}
    exact.feed({"recent": [row]})
    assert sent[0]["tokens_per_s"] == 50.0 and sent[0]["generation_ms"] == 2000
    assert "approximate" not in sent[0]
    exact.offer({**step, "step": 2, "started": 1100.0})
    now[0] += ExactSteps.HOLD_SECONDS + 1
    exact.settle()
    assert sent[1]["approximate"] is True  # no row in time: the "~" figures stay
