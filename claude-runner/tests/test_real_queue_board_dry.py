"""DRY golden test: real_queue_board.sh with the flag off prints the same lines as real_queue.sh.

Only DRY=1 runs are made: the shells then read the database through n8nq.sh and check the
environment, and change nothing. No dashboard route is called: the board runs stay read-only
(`queue_board.py next` is a GET; the flag-off run makes no board call at all), and with the flag
on the DRY branch previews the board pick order against a fake loopback board from the helper
test (its data shape, through a real HTTP server on a free port), never against the live
dashboard.
"""

from __future__ import annotations

import http.server
import json
import os
import re
import shutil
import socket
import subprocess
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

OPS_DIR = Path(__file__).resolve().parents[1].parent / "data-tables" / "ops"
STATIC_SHELL = OPS_DIR / "real_queue.sh"
BOARD_SHELL = OPS_DIR / "real_queue_board.sh"
BASH = shutil.which("bash")
TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} ", re.MULTILINE)
DRY_RUN_TIMEOUT_SECONDS = 90

pytestmark = pytest.mark.skipif(BASH is None, reason="bash is not installed")


class _BoardHandler(http.server.BaseHTTPRequestHandler):
    """Serves the fake board payloads; records the method and path of every request."""

    payload: dict[str, Any]
    seen: list[tuple[str, str]] = []

    def do_GET(self) -> None:
        self.seen.append(("GET", self.path))
        body = json.dumps(self.payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:
        pass


def _free_port() -> int:
    # same helper as qa_stack._free_port (a bind on 127.0.0.1 with port 0)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@contextmanager
def _fake_board_url(
    payload: dict[str, Any], seen: list[tuple[str, str]]
) -> Iterator[str]:
    handler: type[_BoardHandler] = type(
        "BoardHandler",
        (_BoardHandler,),
        {"payload": payload, "seen": seen},
    )
    server = http.server.HTTPServer(("127.0.0.1", _free_port()), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        thread.join(timeout=5)


def _dry_output(script: Path, env: dict[str, str]) -> str:
    assert BASH is not None
    result = subprocess.run(
        [BASH, str(script)],
        env=env,
        capture_output=True,
        text=True,
        timeout=DRY_RUN_TIMEOUT_SECONDS,
        check=False,
    )
    return TIMESTAMP.sub("", result.stdout + result.stderr)


def test_flag_off_board_shell_prints_the_same_decision_lines_as_the_static_shell() -> None:
    env = {
        "PATH": os.environ.get("PATH", ""),
        "DRY": "1",
        "QUEUE_FROM_BOARD": "0",
    }
    static_lines = _dry_output(STATIC_SHELL, env)
    board_lines = _dry_output(BOARD_SHELL, env)
    assert "DRY RUN: nothing is changed" in static_lines
    assert "ahawr-ops-control-ui: row enabled=" in static_lines
    assert board_lines == static_lines


def test_flag_on_dry_run_prints_the_board_order_and_only_reads() -> None:
    seen: list[tuple[str, str]] = []
    payload = {
        "queue": [
            {"position": 1, "mission_id": "m_b", "enabled": True, "retry_failed": False},
            {"position": 2, "mission_id": "m_a", "enabled": True, "retry_failed": False},
        ],
        "version": "v7",
        "live_states": [
            {"state_key": "m_a", "status": "queued"},
            {"state_key": "m_b", "status": "queued"},
        ],
    }
    with _fake_board_url(payload, seen) as url:
        env = {
            "PATH": os.environ.get("PATH", ""),
            "DRY": "1",
            "QUEUE_BOARD_URL": url,
        }
        board_lines = _dry_output(BOARD_SHELL, env)
    assert "DRY RUN: board pick m_b" in board_lines
    assert "DRY RUN: board pick m_a" in board_lines
    assert board_lines.index("DRY RUN: board pick m_b") < board_lines.index(
        "DRY RUN: board pick m_a"
    )
    assert all(method == "GET" for method, _ in seen), seen


def test_flag_on_dry_run_alerts_once_and_keeps_the_static_order_when_the_board_is_down() -> None:
    port = _free_port()  # nothing listens on it
    env = {
        "PATH": os.environ.get("PATH", ""),
        "DRY": "1",
        "QUEUE_BOARD_URL": f"http://127.0.0.1:{port}",
    }
    board_lines = _dry_output(BOARD_SHELL, env)
    alerts = [line for line in board_lines.splitlines() if "ALERT" in line]
    assert len(alerts) == 1
    assert "board queue was not used" in alerts[0]
    assert "ahawr-ops-control-ui: row enabled=" in board_lines
    assert "DRY RUN: board pick" not in board_lines
