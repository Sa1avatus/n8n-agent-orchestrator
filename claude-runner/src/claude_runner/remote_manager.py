"""A read-only stand-in for ``RunManager`` that reads the runner over HTTP.

The dashboard is a separate process without the runner's database and event files, but the board's statistics
(``board_stats``, ``mission_stats``) read runs through ``manager.store.get_run / list_runs``, ``manager.events.read``
and ``manager.local_pricing``.  This class offers exactly those calls on top of ``GET /v1/runs``, ``/v1/runs/{id}``
and ``/v1/runs/{id}/events`` of claude-runner.  The local price of a run is not recomputed here (``local_pricing`` is
off: the cost of a local run comes from the ``cost_usd`` the runner already stored, or is 0).

A finished run never changes, so its record and events are cached (bounded); a running run is read again every time.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any

import httpx

FINISHED = ("completed", "failed", "cancelled", "interrupted", "timeout")
CACHE_RUNS = 400
LIST_TTL = 15.0     # seconds the run list used for the missing fields is kept


def guess_provider(model: str) -> str:
    """The provider of a run that is not in the list any more: cloud models are named after the vendor's families."""
    name = model.lower()
    return "anthropic" if name.startswith("claude") or name in ("haiku", "sonnet", "opus") else "local"


class _NoPricing:
    enabled = False

    def cost_from_events(self, path: Any) -> None:
        return None


class _Cache:
    def __init__(self, size: int) -> None:
        self.size = size
        self.data: OrderedDict[str, Any] = OrderedDict()
        self.lock = threading.Lock()

    def get(self, key: str) -> Any | None:
        with self.lock:
            value = self.data.get(key)
            if value is not None:
                self.data.move_to_end(key)
            return value

    def put(self, key: str, value: Any) -> None:
        with self.lock:
            self.data[key] = value
            self.data.move_to_end(key)
            while len(self.data) > self.size:
                self.data.popitem(last=False)


class _RemoteStore:
    def __init__(self, owner: RemoteRunManager) -> None:
        self.owner = owner

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        cached = self.owner.runs.get(run_id)
        if cached is not None:
            return cached  # type: ignore[no-any-return]
        body = self.owner.get_json(f"/v1/runs/{run_id}")
        if not body:
            return None
        body = self._with_list_fields(run_id, body)
        if body.get("status") in FINISHED:
            self.owner.runs.put(run_id, body)
        return body

    def _with_list_fields(self, run_id: str, body: dict[str, Any]) -> dict[str, Any]:
        """``GET /v1/runs/{id}`` has no ``provider``, ``kind``, ``title`` or ``cwd`` (the list rows have): the statistics need
        the provider to tell a local run from a cloud one (speed, real cost). The row comes from the list (read at most every
        ``LIST_TTL`` seconds); a run older than the list falls back to a guess from the model name."""
        if body.get("provider"):
            return body
        row = self.owner.list_index().get(run_id)
        if row:
            return {**row, **{k: v for k, v in body.items() if v is not None}}
        merged = dict(body)
        merged["provider"] = guess_provider(str(body.get("model") or ""))
        return merged

    def list_runs(self, limit: int, session_id: str | None = None) -> list[dict[str, Any]]:
        params: dict[str, str] = {"limit": str(min(int(limit), 500))}
        if session_id:
            params["session_id"] = session_id
        body = self.owner.get_json("/v1/runs", params) or {"runs": []}
        return [r for r in body.get("runs", []) if r]


class _RemoteEvents:
    directory = Path("/nonexistent-runner-events")

    def __init__(self, owner: RemoteRunManager) -> None:
        self.owner = owner

    def read(self, run_id: str, after: int = 0) -> tuple[list[dict[str, Any]], int]:
        cached = self.owner.events_cache.get(run_id) if after == 0 else None
        if cached is not None:
            return cached  # type: ignore[no-any-return]
        entries: list[dict[str, Any]] = []
        cursor = after
        finished = False
        for _ in range(200):  # at most 200 pages: a run's log is never that long for a statistic
            body = self.owner.get_json(f"/v1/runs/{run_id}/events", {"after": str(cursor)})
            if not body:
                break
            page = body.get("events") or []
            run = body.get("run") or {}
            finished = run.get("status") in FINISHED
            if not page:
                break
            entries.extend(page)
            nxt = int(body.get("next") or cursor)
            if nxt <= cursor:
                break
            cursor = nxt
        if after == 0 and finished:
            self.owner.events_cache.put(run_id, (entries, cursor))
        return entries, cursor


class RemoteRunManager:
    """The subset of ``RunManager`` the board statistics use, over HTTP."""

    def __init__(
        self, url: str, api_key: str = "", timeout: float = 20.0, transport: httpx.BaseTransport | None = None
    ) -> None:
        self.url = url.rstrip("/")
        self.headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self.client = httpx.Client(timeout=timeout, transport=transport)
        self.runs = _Cache(CACHE_RUNS)
        self.events_cache = _Cache(CACHE_RUNS)
        self.store = _RemoteStore(self)
        self.events = _RemoteEvents(self)
        self.local_pricing = _NoPricing()
        self._index: dict[str, dict[str, Any]] = {}
        self._index_at = 0.0
        self._index_lock = threading.Lock()

    def list_index(self) -> dict[str, dict[str, Any]]:
        """run id -> list row, from one ``GET /v1/runs?limit=500`` kept for ``LIST_TTL`` seconds."""
        with self._index_lock:
            if time.monotonic() - self._index_at > LIST_TTL:
                body = self.get_json("/v1/runs", {"limit": "500"}) or {}
                self._index = {r["run_id"]: r for r in body.get("runs", []) if r and r.get("run_id")}
                self._index_at = time.monotonic()
            return self._index

    def get_json(self, path: str, params: dict[str, str] | None = None) -> dict[str, Any] | None:
        try:
            response = self.client.get(self.url + path, params=params, headers=self.headers)
        except httpx.HTTPError:
            return None
        if response.status_code != 200:
            return None
        try:
            body = response.json()
        except ValueError:
            return None
        return body if isinstance(body, dict) else None

    def list_processes(self) -> list[dict[str, Any]]:
        """The runner's own CLI processes from ``GET /v1/processes``: pid, age, the run's
        session id, the run id and the run's status (no environment values, no command line)."""
        try:
            response = self.client.get(self.url + "/v1/processes", headers=self.headers)
        except httpx.HTTPError:
            return []
        if response.status_code != 200:
            return []
        try:
            body = response.json()
        except ValueError:
            return []
        if isinstance(body, list):
            return [p for p in body if isinstance(p, dict)]
        if isinstance(body, dict):
            return [p for p in (body.get("processes") or []) if isinstance(p, dict)]
        return []

    def terminate_process(self, pid: int) -> dict[str, Any] | None:
        """Terminate one of the runner's CLI processes by pid via
        ``POST /v1/processes/{pid}/terminate``; None when the runner does not answer
        with 200 (not found, foreign to the runner, or the run is still active)."""
        try:
            response = self.client.post(
                f"{self.url}/v1/processes/{int(pid)}/terminate", headers=self.headers
            )
        except httpx.HTTPError:
            return None
        if response.status_code != 200:
            return None
        try:
            body = response.json()
        except ValueError:
            return None
        return body if isinstance(body, dict) else None
