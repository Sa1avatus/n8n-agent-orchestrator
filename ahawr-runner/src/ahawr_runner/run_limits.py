"""Per-role run time limit an operator can set without recreating the runner.

The runner's own limit is `max_run_seconds` (environment, one value for every run).
To test the time-limit path (error code `timeout`, adaptive split) without a
one-hour wait, `<data_dir>/run_limit_override.json` may hold `{"worker": 60}`: a run
of that role gets the smaller limit. The file is read at every run start, so it takes
effect and is removed without a restart; a missing or invalid file changes nothing,
and a value above `max_run_seconds` is ignored (the override only shortens).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

log = logging.getLogger("ahawr_runner.run_limits")

OVERRIDE_FILE_NAME = "run_limit_override.json"


def effective_run_limit_seconds(data_dir: Path, role: str, max_run_seconds: int) -> int:
    """The limit of a run of `role`: the file override when it is a shorter positive integer."""
    path = data_dir / OVERRIDE_FILE_NAME
    if not path.is_file():
        return max_run_seconds
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        log.warning("run limit override %s ignored: %s", path, error)
        return max_run_seconds
    value = data.get(role) if isinstance(data, dict) else None
    if isinstance(value, bool) or not isinstance(value, int) or not 0 < value < max_run_seconds:
        return max_run_seconds
    return value
