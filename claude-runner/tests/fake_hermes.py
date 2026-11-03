#!/usr/bin/env python3
"""Stand-in for the Hermes gateway in tests: a stdlib http.server on 127.0.0.1, port 0.

The scripted responses are {"method", "path", "status", "body"} entries, consumed in order per
path: DEFAULT_SCRIPT is the success run (POST /v1/runs
starts RUN_1, two GET /v1/runs/RUN_1 polls take the run from running to completed). Once the
script for a path is exhausted, the last entry of that path is served again. The server binds
127.0.0.1 only and makes no outside connection; every request is recorded in server.requests
(method, path, headers, body) so the tests assert what the client sent.
"""

from __future__ import annotations

import json
import threading
from collections import deque
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

DEFAULT_SCRIPT: list[dict[str, Any]] = [
    {
        "method": "POST",
        "path": "/v1/runs",
        "status": 200,
        "body": {"run_id": "RUN_1", "session_id": "SESSION_1"},
    },
    {"method": "GET", "path": "/v1/runs/RUN_1", "status": 200, "body": {"status": "running"}},
    {
        "method": "GET",
        "path": "/v1/runs/RUN_1",
        "status": 200,
        "body": {
            "status": "completed",
            "output": "fake hermes answer",
            "finished_at": "2026-11-03T12:00:00+00:00",
        },
    },
]


class _Handler(BaseHTTPRequestHandler):
    """Answers with the next scripted entry and records the request on the fake server."""

    def do_GET(self) -> None:
        self._respond("GET")

    def do_POST(self) -> None:
        self._respond("POST")

    def _respond(self, method: str) -> None:
        fake: FakeHermesServer = self.server.fake
        # shortcut: read only the declared body length; a plain read() would block on a GET
        # with no body (keep-alive), upgrade when the gateway sends chunked bodies
        length = int(self.headers.get("Content-Length", 0) or 0)
        body_text = self.rfile.read(length).decode("utf-8", "replace")
        entry = fake.next_entry(method, self.path)
        fake.requests.append(
            {
                "method": method,
                "path": self.path,
                "headers": dict(self.headers),
                "body": json.loads(body_text) if body_text else None,
            }
        )
        payload = json.dumps(entry["body"]).encode("utf-8")
        self.send_response(entry["status"])
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, fmt: str, *args: Any) -> None:
        pass  # quiet fixture: the recorded requests are the evidence tests assert on


class _Server(HTTPServer):
    fake: FakeHermesServer


class FakeHermesServer:
    """Serves the script from 127.0.0.1 on a free port (port 0), started on its own thread.

    shortcut: the script matches exact paths; upgrade if the gateway needs query strings too.
    """

    def __init__(self, script: list[dict[str, Any]] | None = None) -> None:
        self.requests: list[dict[str, Any]] = []
        self._pending: dict[tuple[str, str], deque[dict[str, Any]]] = {}
        self._last: dict[tuple[str, str], dict[str, Any]] = {}
        entries = DEFAULT_SCRIPT if script is None else script
        for entry in entries:
            self._pending.setdefault((entry["method"], entry["path"]), deque()).append(entry)
        self._server = _Server(("127.0.0.1", 0), _Handler)
        self._server.fake = self
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    @property
    def host(self) -> str:
        return str(self._server.server_address[0])

    def base_url(self) -> str:
        return f"http://{self.host}:{self._server.server_port}"

    def shutdown(self) -> None:
        self._server.shutdown()
        self._thread.join(timeout=2)

    def next_entry(self, method: str, path: str) -> dict[str, Any]:
        queue = self._pending.get((method, path))
        if queue:
            entry = queue.popleft()
            self._last[(method, path)] = entry
            return entry
        return self._last.get((method, path), {"status": 404, "body": {"error": "run_not_found"}})
