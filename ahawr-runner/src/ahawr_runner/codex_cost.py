"""Estimated USD cost of a Codex run from the provider's price table.

Rates are USD per 1,000,000 tokens: input, output, cache read, cache write, as parsed from
``PRICE_USD_PER_MILLION_TOKENS``. ``usage`` is ``CodexEventMapper.usage``: its ``input_tokens``
already excludes the cached input. Reasoning tokens are billed inside ``output_tokens``, so they
have no rate of their own.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping

log = logging.getLogger(__name__)

TOKENS_PER_PRICE_UNIT = 1_000_000


def estimate_cost_usd(
    model: str,
    usage: Mapping[str, int],
    prices: Mapping[str, tuple[float, float, float, float]],
) -> float | None:
    rates = prices.get(model)
    if rates is None:
        log.warning("no price for Codex model %r; the run has no cost estimate", model)
        return None
    price_input, price_output, price_cache_read, price_cache_write = rates
    cost = (
        usage["input_tokens"] * price_input
        + usage["cache_read_tokens"] * price_cache_read
        + usage["cache_write_tokens"] * price_cache_write
        + usage["output_tokens"] * price_output
    )
    return cost / TOKENS_PER_PRICE_UNIT
