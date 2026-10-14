"""What a local run would have cost on a cloud model, by whether the local model was thinking.

The local model costs nothing per token; the dashboard still shows a price so that runs can be compared with cloud runs. Claude Code prices a
model it does not know with one flat rate table (``managed-settings.json``), whatever the thinking mode. Here the price is computed by the
runner from the token counts of the run's own steps (the ``step`` entries of its event log) with the rate table of a cloud model chosen
per thinking mode: for example the steps made without thinking at Haiku rates and the steps made with thinking at Sonnet rates.
The mode of a step is the last ``thinking_state`` / ``thinking_switch`` entry before the step started. Every run also gets the whole run at
each configured profile (``by_profile``), so Sonnet and Haiku equivalents can be compared whatever the mapping is.

Rates are USD per million tokens: input, output, cache read, cache write.
Settings (environment): CLAUDE_RUNNER_LOCAL_PRICE_<PROFILE>="in,out,cache_read,cache_write" (PROFILE = SONNET, HAIKU, ...),
CLAUDE_RUNNER_LOCAL_PRICE_THINKING_OFF=<profile name>, CLAUDE_RUNNER_LOCAL_PRICE_THINKING_ON=<profile name>. Both mode names unset =
the feature is off and the Claude Code figure is kept.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

FIELDS = ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens")
PREFIX = "CLAUDE_RUNNER_LOCAL_PRICE_"


def parse_rates(text: str) -> tuple[float, float, float, float] | None:
    """'2,10,0.2,4' -> (2.0, 10.0, 0.2, 4.0); anything else -> None."""
    parts = [p for p in re.split(r"[,\s;]+", (text or "").strip()) if p]
    if len(parts) != 4:
        return None
    try:
        values = tuple(float(p) for p in parts)
    except ValueError:
        return None
    if any(v < 0 for v in values):
        return None
    return values  # type: ignore[return-value]


class LocalPricing:
    def __init__(self, profiles: Mapping[str, tuple[float, float, float, float]], mode_profile: Mapping[str, str]) -> None:
        self.profiles = dict(profiles)
        self.mode_profile = {m: p for m, p in mode_profile.items() if p in self.profiles}

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> LocalPricing:
        profiles: dict[str, tuple[float, float, float, float]] = {}
        for key, value in env.items():
            if key.startswith(PREFIX) and not key.endswith(("THINKING_OFF", "THINKING_ON")):
                rates = parse_rates(value)
                if rates is not None:
                    profiles[key[len(PREFIX):].lower()] = rates
        modes = {
            "off": (env.get(PREFIX + "THINKING_OFF") or "").strip().lower(),
            "on": (env.get(PREFIX + "THINKING_ON") or "").strip().lower(),
        }
        # one mode named: the other one is priced the same
        if modes["off"] and not modes["on"]:
            modes["on"] = modes["off"]
        if modes["on"] and not modes["off"]:
            modes["off"] = modes["on"]
        return cls(profiles, modes)

    @property
    def enabled(self) -> bool:
        return len(self.mode_profile) == 2

    def cost_from_events(self, path: Path, default_mode: str = "on") -> dict[str, Any] | None:
        """Cost of the run from its event log, or None when the log has no steps. ``default_mode`` is used before the first
        thinking_state / thinking_switch entry (a run of an older runner version has none)."""
        if not self.enabled:
            return None
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return None
        switches: list[tuple[float, str]] = []
        steps: list[dict[str, Any]] = []
        initial = default_mode
        for line in lines:
            if '"thinking_' not in line and '"step"' not in line:
                continue
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            kind = entry.get("kind")
            value = str(entry.get("thinking") or "").lower()
            if kind == "thinking_state" and value in ("on", "off") and not switches:
                initial = value
            elif kind in ("thinking_state", "thinking_switch") and value in ("on", "off"):
                switches.append((float(entry.get("t") or 0), value))
            elif kind == "step":
                steps.append(entry)
        if not steps:
            return None
        switches.sort()
        total = 0.0
        by_profile = {name: 0.0 for name in self.profiles}
        per_mode = {"on": {"steps": 0, "usd": 0.0}, "off": {"steps": 0, "usd": 0.0}}
        for step in steps:
            started = float(step.get("started") or step.get("t") or 0)
            mode = initial
            for t, value in switches:
                if t <= started:
                    mode = value
                else:
                    break
            tokens = [int(step.get(field) or 0) for field in FIELDS]
            for name, rates in self.profiles.items():
                by_profile[name] += sum(n * r for n, r in zip(tokens, rates, strict=True)) / 1_000_000
            rates = self.profiles[self.mode_profile[mode]]
            usd = sum(n * r for n, r in zip(tokens, rates, strict=True)) / 1_000_000
            total += usd
            per_mode[mode]["steps"] += 1
            per_mode[mode]["usd"] += usd
        return {
            "cost_usd": round(total, 6),
            "by_profile": {k: round(v, 6) for k, v in by_profile.items()},
            "by_mode": {k: {"steps": v["steps"], "usd": round(v["usd"], 6), "profile": self.mode_profile[k]} for k, v in per_mode.items()},
        }
