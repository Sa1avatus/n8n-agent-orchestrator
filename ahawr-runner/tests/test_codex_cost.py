from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ahawr_runner.codex_cost import estimate_cost_usd
from ahawr_runner.codex_events import CodexEventMapper
from ahawr_runner.local_pricing import parse_rates

FIXTURES = Path(__file__).parent / "fixtures" / "codex"


def _clock(*ticks: float) -> Callable[[], float]:
    values = iter(ticks)
    return lambda: next(values)


def test_cost_is_the_price_table_formula_for_a_known_model() -> None:
    rates = parse_rates("2,10,0.5,4")
    assert rates is not None
    usage = {
        "input_tokens": 1000,
        "cache_read_tokens": 3000,
        "cache_write_tokens": 500,
        "output_tokens": 200,
        "reasoning_output_tokens": 50,
    }

    cost = estimate_cost_usd("gpt-test", usage, {"gpt-test": rates})

    assert cost == (1000 * 2 + 3000 * 0.5 + 500 * 4 + 200 * 10) / 1_000_000


def test_cost_of_the_success_fixture_uses_the_run_usage_totals() -> None:
    mapper = CodexEventMapper(clock=_clock(100.0, 102.5))
    for line in (FIXTURES / "success.jsonl").read_text(encoding="utf-8").splitlines():
        if not line.startswith("#"):
            mapper.feed(line)
    rates = parse_rates("2,10,0.5,4")
    assert rates is not None

    cost = estimate_cost_usd("gpt-test", mapper.usage, {"gpt-test": rates})

    assert cost == (400 * 2 + 800 * 0.5 + 0 * 4 + 90 * 10) / 1_000_000


def test_unknown_model_has_no_cost_and_logs_a_warning(caplog: Any) -> None:
    usage = {
        "input_tokens": 1000,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "output_tokens": 0,
        "reasoning_output_tokens": 0,
    }

    with caplog.at_level(logging.WARNING, logger="ahawr_runner.codex_cost"):
        cost = estimate_cost_usd("unpriced-model", usage, {})

    assert cost is None
    assert any("unpriced-model" in record.getMessage() for record in caplog.records)
