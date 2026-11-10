"""Live request figures of a Strata engine: GET ``/v1/telemetry`` (the same snapshot as
``strata monitor``). Its ``recent`` list holds one row per finished request: prompt tokens,
tokens reused from the cache, output tokens and the prompt/decode times. The Hermes gateway
reports none of this while a run is going, so the run's ``step`` entries come from here.

Stdlib only; the key value is read from the profile at call time and never logged.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any

log = logging.getLogger(__name__)

TELEMETRY_PATH = "/v1/telemetry"
TIMEOUT_SECONDS = 3.0
POLL_SECONDS = 2.0  # recent holds 12 requests and a request takes >= 1 s: nothing is missed
MS_PER_S = 1000
ROWS_KEPT = 64  # engine rows not yet matched to a step


def fetch_snapshot(base_url: str, key: str) -> dict[str, Any]:
    """The telemetry snapshot, or {} when the engine does not answer (a failed call only
    loses live detail, the run itself never depends on it)."""
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}{TELEMETRY_PATH}",
        headers={"Authorization": f"Bearer {key}"} if key else {},
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as resp:
            body = json.loads(resp.read().decode("utf-8", "replace"))
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
        log.debug("Strata telemetry unavailable: %r", exc)
        return {}
    return body if isinstance(body, dict) else {}


def step_entry(row: dict[str, Any], step: int, model: str) -> dict[str, Any]:
    """One runner ``step`` entry for one finished engine request."""
    prompt = int(row.get("prompt_tokens") or 0)
    reused = min(int(row.get("reused") or 0), prompt)
    output = int(row.get("output_tokens") or 0)
    decode_ms = int(float(row.get("decode_ms") or 0))
    entry: dict[str, Any] = {
        "kind": "step",
        "step": step,
        "model": model,
        "started": round(float(row.get("time") or 0), 3),
        "input_tokens": prompt - reused,
        "cache_read_tokens": reused,
        "cache_write_tokens": 0,
        "output_tokens": output,
        "duration_ms": round(float(row.get("duration_s") or 0) * MS_PER_S),
        "ttft_ms": round(float(row.get("first_engine_token_s") or 0) * MS_PER_S),
    }
    if decode_ms > 0 and output:
        entry["generation_ms"] = decode_ms
        entry["tokens_per_s"] = round(output / (decode_ms / MS_PER_S), 1)
    return entry


class TelemetryTail:
    """Turns the snapshots seen while one run lives into step entries, once per request.

    ``since`` is the engine clock (epoch seconds) when the run started: older requests belong
    to other runs. One engine slot serves one request at a time, so the requests after
    ``since`` are this run's while the bench runs one Worker."""

    def __init__(self, since: float, model: str) -> None:
        self.since = since
        self.model = model
        self.steps = 0
        self._seen: set[str] = set()

    def feed(self, snapshot: dict[str, Any]) -> list[dict[str, Any]]:
        rows = [
            r for r in snapshot.get("recent") or []
            if isinstance(r, dict) and r.get("id") not in self._seen
            and float(r.get("time") or 0) >= self.since
        ]
        entries = []
        for row in sorted(rows, key=lambda r: float(r.get("time") or 0)):
            self._seen.add(str(row.get("id")))
            self.steps += 1
            entries.append(step_entry(row, self.steps, self.model))
        return entries


class ExactSteps:
    """Replaces the client-side speed of ``approximate`` step entries (Codex app-server, Hermes
    session) with the engine's own decode time, when the engine answers ``/v1/telemetry``.

    A step is held until the engine row of its request shows up (``HOLD_SECONDS`` at most), then
    emitted once, so no consumer sees two entries for one request. A row belongs to a step when it
    started inside the step's span and produced the same number of output tokens."""

    HOLD_SECONDS = 6.0
    SLACK_SECONDS = 1.0

    def __init__(
        self,
        base_url: str,
        key: str,
        emit: Callable[[dict[str, Any]], None],
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._base_url = base_url
        self._key = key
        self._emit = emit
        self._clock = clock
        self._lock = threading.Lock()
        self._rows: list[dict[str, Any]] = []
        self._pending: list[tuple[float, dict[str, Any]]] = []  # (deadline, entry)

    def offer(self, entry: dict[str, Any]) -> None:
        if entry.get("kind") != "step" or not entry.get("approximate"):
            self._emit(entry)
            return
        with self._lock:
            self._pending.append((self._clock() + self.HOLD_SECONDS, entry))
        self.settle()

    def feed(self, snapshot: dict[str, Any]) -> None:
        known = {r.get("id") for r in self._rows}
        with self._lock:
            self._rows += [
                r for r in snapshot.get("recent") or []
                if isinstance(r, dict) and r.get("id") not in known
            ]
            self._rows = self._rows[-ROWS_KEPT:]
        self.settle()

    def settle(self, final: bool = False) -> None:
        """Emit the steps that have their row or waited long enough (all of them if final)."""
        ready: list[dict[str, Any]] = []
        with self._lock:
            waiting: list[tuple[float, dict[str, Any]]] = []
            for deadline, entry in self._pending:
                row = self._row_of(entry)
                if row is not None:
                    self._rows.remove(row)
                    ready.append(self._merge(entry, row))
                elif final or deadline <= self._clock():
                    ready.append(entry)
                else:
                    waiting.append((deadline, entry))
            self._pending = waiting
        for entry in ready:
            self._emit(entry)

    def run(self, stop: threading.Event) -> None:
        """Poll the engine until ``stop`` is set, then settle everything that is left."""
        while not stop.is_set():
            self.feed(fetch_snapshot(self._base_url, self._key))
            stop.wait(POLL_SECONDS)
        self.feed(fetch_snapshot(self._base_url, self._key))
        self.settle(final=True)

    def _row_of(self, entry: dict[str, Any]) -> dict[str, Any] | None:
        start = float(entry.get("started") or 0) - self.SLACK_SECONDS
        end = start + float(entry.get("duration_ms") or 0) / MS_PER_S + 2 * self.SLACK_SECONDS
        output = int(entry.get("output_tokens") or 0)
        for row in self._rows:
            if start <= float(row.get("time") or 0) <= end and int(
                row.get("output_tokens") or 0
            ) == output:
                return row
        return None

    @staticmethod
    def _merge(entry: dict[str, Any], row: dict[str, Any]) -> dict[str, Any]:
        exact = step_entry(row, int(entry.get("step") or 0), str(entry.get("model") or ""))
        merged = {k: v for k, v in entry.items() if k != "approximate"}
        for field in ("generation_ms", "tokens_per_s", "ttft_ms"):
            if field in exact:
                merged[field] = exact[field]
            else:
                merged.pop(field, None)
        return merged


def watch(
    base_url: str,
    key: str,
    tail: TelemetryTail,
    on_entry: Callable[[dict[str, Any]], None],
    stop: threading.Event,
) -> None:
    """Poll until ``stop`` is set, then once more, so the last request is not lost."""
    while True:
        stopped = stop.is_set()
        for entry in tail.feed(fetch_snapshot(base_url, key)):
            on_entry(entry)
        if stopped:
            return
        stop.wait(POLL_SECONDS)
