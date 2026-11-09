"""Launcher for project web apps in the QA ui layer.

app_up starts the `ui` block of a project profile as a real process on a free 127.0.0.1
port with a throwaway data directory under the system temp folder, waits for health_path
with a bounded poll and returns a handle; app_down kills the whole process tree and removes
the directory. No Docker: the child is the argument list of the profile, and it gets a
scrubbed environment (only the profile env plus the OS basics).

Failure paths (missing binary, never-healthy app) raise QaStackError, which the QA runner
maps to a blocked verdict, and clean up the process tree and the data directory.
"""

from __future__ import annotations

import logging
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from ahawr_runner.qa_regression import Profile, UiConfig
from ahawr_runner.qa_stack import (
    HEALTH_POLL_SECONDS,
    HEALTH_REQUEST_SECONDS,
    QA_LOOPBACK_HOST,
    QA_PREFIX,
    QaStackError,
    QaTargetError,
    _free_port,
    _kill_tree,
    _wait_until,
)

log = logging.getLogger("ahawr_runner.qa_app")
# a project app binds only on loopback: the launcher never opens a non-local address
ALLOWED_APP_HOSTS = frozenset({QA_LOOPBACK_HOST, "localhost"})
# the names the child keeps from the host process; everything else is scrubbed
OS_BASIC_ENV_NAMES = frozenset(
    {"PATH", "PATHEXT", "SystemRoot", "COMSPEC", "TEMP", "TMP", "HOME", "USERPROFILE"}
)
APP_DATA_PREFIX = f"{QA_PREFIX}app-"
APP_RUN_ID_BYTES = 4


@dataclass
class AppHandle:
    base_url: str
    port: int
    data_dir: Path
    process: subprocess.Popen[bytes]


def app_up(profile: Profile, host: str = QA_LOOPBACK_HOST) -> AppHandle:
    """Start the app of a profile on a free loopback port and return its handle.

    Raises QaStackError (blocked in the QA verdict) when the binary is missing or the app
    never answers health_path; the process tree and the data directory are cleaned up then.
    """
    ui = profile.ui
    if ui is None:
        raise ValueError(f"project {profile.name!r} has no ui block to start an app")
    _assert_app_host(host)
    port = _free_port()
    run_id = secrets.token_hex(APP_RUN_ID_BYTES)
    data_dir = Path(tempfile.gettempdir()) / f"{APP_DATA_PREFIX}{run_id}"
    data_dir.mkdir(parents=True, exist_ok=True)
    command = [_substitute(arg, port, data_dir) for arg in ui.start_command]
    try:
        process = subprocess.Popen(
            command,
            cwd=profile.working_directory,
            env=_scrubbed_env(ui, port, data_dir),
            start_new_session=sys.platform != "win32",
        )
    except FileNotFoundError as exc:
        shutil.rmtree(data_dir, ignore_errors=True)
        raise QaStackError(f"app binary {command[0]!r} is not installed: {exc}") from exc
    except OSError as exc:
        shutil.rmtree(data_dir, ignore_errors=True)
        raise QaStackError(f"app start command {command[0]!r} could not run: {exc}") from exc

    handle = AppHandle(
        base_url=f"http://{host}:{port}",
        port=port,
        data_dir=data_dir,
        process=process,
    )
    log.info("qa app %s up on %s (data dir %s)", profile.name, handle.base_url, data_dir)
    ready = False
    try:
        _wait_until(
            lambda: _app_healthy(handle.base_url, ui.health_path),
            f"app {profile.name!r} at {handle.base_url}{ui.health_path}",
            max_polls=_ready_polls(ui.ready_timeout_seconds),
        )
        ready = True
    finally:
        if not ready:
            app_down(handle)
    return handle


def app_down(handle: AppHandle) -> None:
    """Kill the whole process tree of the app and remove its throwaway data directory."""
    _kill_tree(handle.process)
    shutil.rmtree(handle.data_dir, ignore_errors=True)
    log.info("qa app down: %s", handle.base_url)


def _assert_app_host(host: str) -> None:
    """Loopback-only host guard for project apps.

    shortcut: assert_qa_target in qa_stack.py rejects 127.0.0.1/localhost because it is a
    Postgres guard, so this guard reuses QaTargetError with its own allowed host set.
    """
    if host not in ALLOWED_APP_HOSTS:
        allowed = ", ".join(sorted(ALLOWED_APP_HOSTS))
        raise QaTargetError(f"app host {host!r} is not loopback; allowed hosts: {allowed}")


def _ready_polls(ready_timeout_seconds: float) -> int:
    """The poll bound: delay HEALTH_POLL_SECONDS, enough polls to cover ready_timeout_seconds."""
    return max(1, int(ready_timeout_seconds // HEALTH_POLL_SECONDS))


def _scrubbed_env(ui: UiConfig, port: int, data_dir: Path) -> dict[str, str]:
    env = {name: value for name, value in os.environ.items() if name in OS_BASIC_ENV_NAMES}
    for name, raw_value in ui.env.items():
        env[name] = _substitute(raw_value, port, data_dir)
    env[ui.port_env] = str(port)
    return env


def _substitute(value: str, port: int, data_dir: Path) -> str:
    return value.replace("{port}", str(port)).replace("{data_dir}", str(data_dir))


def _app_healthy(base_url: str, health_path: str) -> bool:
    try:
        health_url = base_url + health_path
        with urllib.request.urlopen(health_url, timeout=HEALTH_REQUEST_SECONDS) as response:
            return bool(response.status == 200)
    except (urllib.error.URLError, OSError):
        return False
