"""The ChatGPT login of the Codex CLI: ``codex login`` keeps ``auth.json`` in one directory; each
run's Codex home gets a copy, and a token the CLI refreshed during the run is written back.

shortcut: the last run to finish wins when two runs refresh the token at once, one login per
runner; give each run its own login if refresh tokens turn out to rotate on every use.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import tempfile
from pathlib import Path

log = logging.getLogger("ahawr_runner.codex_auth")

AUTH_FILE_NAME = "auth.json"


def stage_auth(login_home: str, run_home: Path) -> bool:
    """Copy the login's auth.json into the run's Codex home; False when there is no usable login."""
    source = Path(login_home) / AUTH_FILE_NAME
    try:
        data = source.read_bytes()
        json.loads(data)
    except (OSError, ValueError) as exc:
        log.warning("codex login not usable at %s: %s", source, exc)
        return False
    _write_private(run_home / AUTH_FILE_NAME, data)
    return True


def sync_auth_back(login_home: str, run_home: Path) -> bool:
    """Write the run's auth.json back to the login directory when the CLI changed it."""
    current = run_home / AUTH_FILE_NAME
    target = Path(login_home) / AUTH_FILE_NAME
    try:
        data = current.read_bytes()
        json.loads(data)
        if target.exists() and target.read_bytes() == data:
            return False
    except (OSError, ValueError):
        return False
    _write_private(target, data)
    return True


def _write_private(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".auth.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise
