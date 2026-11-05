"""Registry of board.ops settings keys: validation, diff and application.

``REGISTRY`` names every tunable key with its type, allowed range (or value
set), default and description.  ``validate(key, value)`` rejects unknown keys
and values outside their type or range with readable messages; ``diff(old,
new)`` lists the keys whose value changed; ``apply`` writes the changed keys
through the store's settings layer (optimistic locking on each row) and
records one ``board.changelog`` entry per changed key, in the first-mission
format (``board.ChangelogEntry``: the previous row as ``before_json``, the new
row as ``after_json``, the acting user as ``who``) — the same audit
convention the board's own writes use.
"""

from __future__ import annotations

from typing import Any

from .board import ChangelogEntry, ConflictError
from .board_ops_store import OpsStore

__all__ = [
    "REGISTRY",
    "diff",
    "apply",
    "defaults",
    "validate",
]

REGISTRY: dict[str, dict[str, Any]] = {
    # ---- loop protection -----------------------------------------------------
    "no_progress_retries": {
        "type": int,
        "min": 1,
        "max": 10,
        "default": 3,
        "description": "Consecutive runs without new events before the mission "
                        "is given up (the no-progress retry limit).",
    },
    "max_mission_runs": {
        "type": int,
        "min": 1,
        "max": 50,
        "default": 15,
        "description": "Maximum runs of one mission before giving up.",
    },
    "consecutive_failures_stop": {
        "type": int,
        "min": 1,
        "max": 10,
        "default": 2,
        "description": "Consecutive mission failures that stop the queue.",
    },
    "docker_wait_minutes": {
        "type": int,
        "min": 5,
        "max": 480,
        "default": 120,
        "description": "Minutes to wait for Docker to come back before giving up.",
    },
    "max_failed_launches": {
        "type": int,
        "min": 1,
        "max": 30,
        "default": 6,
        "description": "Maximum failed launch tries for one mission.",
    },
    # ---- notifications ---------------------------------------------------------
    "notify_mission_done": {
        "type": bool,
        "default": True,
        "description": "Notify when a mission is completed.",
    },
    "notify_mission_failed": {
        "type": bool,
        "default": True,
        "description": "Notify when a mission is not completed.",
    },
    "notify_alert": {
        "type": bool,
        "default": True,
        "description": "Notify on queue alerts.",
    },
    "notify_docker": {
        "type": bool,
        "default": True,
        "description": "Notify on Docker hang / restart notices.",
    },
    "only_failure_or_done": {
        "type": bool,
        "default": True,
        "description": "Limit notices to mission failure and mission done.",
    },
    # ---- models ------------------------------------------------------------------
    "verified_model_pairs": {
        "type": "model_pair_list",
        "default": [],
        "description": "Verified model pairs: "
                       "[{id, worker, reviewer, architect, providers}, ...].",
    },
    # ---- limits -------------------------------------------------------------------
    "ops_rate_limit_per_min": {
        "type": int,
        "min": 1,
        "max": 1000,
        "default": 6,
        "description": "Maximum command rows the dashboard may create per minute.",
    },
    "command_timeout_s": {
        "type": int,
        "min": 1,
        "max": 86400,
        "default": 600,
        "description": "Seconds a command may stay running before it is marked stale.",
    },
}


def _pair_fields(pair: Any, index: int) -> list[str]:
    """One error per problem of one model pair (readable, per-field)."""
    errors: list[str] = []
    where = f"pair {index}"
    if not isinstance(pair, dict):
        errors.append(f"{where}: each pair must be a dict, got {type(pair).__name__}")
        return errors
    for field in ("id", "worker", "reviewer", "architect"):
        value = pair.get(field)
        if not isinstance(value, str) or not value:
            errors.append(f"{where}: '{field}' must be a non-empty string")
    providers = pair.get("providers")
    if (
        not isinstance(providers, list)
        or not providers
        or not all(isinstance(p, str) and p for p in providers)
    ):
        errors.append(
            f"{where}: 'providers' must be a non-empty list of non-empty strings"
        )
    return errors


def _validate_value(key: str, value: Any, spec: dict[str, Any]) -> list[str]:
    expected = spec["type"]
    if expected is int:
        if not isinstance(value, int) or isinstance(value, bool):
            return [f"{key}: expected an integer, got {type(value).__name__}"]
        lo, hi = spec.get("min"), spec.get("max")
        if lo is not None and value < lo:
            return [f"{key}: {value} is below the allowed range {lo}-{hi}"]
        if hi is not None and value > hi:
            return [f"{key}: {value} is above the allowed range {lo}-{hi}"]
        return []
    if expected is bool:
        if not isinstance(value, bool):
            return [f"{key}: expected a boolean, got {type(value).__name__}"]
        return []
    if expected == "model_pair_list":
        if not isinstance(value, list):
            return [f"{key}: expected a list, got {type(value).__name__}"]
        errors: list[str] = []
        for index, pair in enumerate(value, 1):
            errors.extend(_pair_fields(pair, index))
        return errors
    raise AssertionError(f"unknown key type {expected!r}")


def validate(key: str, value: Any) -> list[str]:
    """The error messages for ``key``/``value``; empty when the value is valid.

    Unknown keys are rejected as well as values outside their type or range."""
    spec = REGISTRY.get(key)
    if spec is None:
        known = ", ".join(sorted(REGISTRY))
        return [f"unknown setting key {key!r} (known keys: {known})"]
    return _validate_value(key, value, spec)


def defaults() -> dict[str, Any]:
    """The default value of every registered key."""
    return {key: dict(spec)["default"] for key, spec in REGISTRY.items()}


def diff(old: dict[str, Any], new: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """The keys whose value changed: ``{key: {"old": ..., "new": ...}}``.

    Keys absent from ``old`` (not yet in the store) count as ``None``."""
    changed: dict[str, dict[str, Any]] = {}
    for key, new_value in new.items():
        if old.get(key) != new_value:
            changed[key] = {"old": old.get(key), "new": new_value}
    return changed


def apply(
    store: OpsStore,
    changelog: list[ChangelogEntry],
    user: str,
    changes: dict[str, Any],
    versions: dict[str, str | None] | None = None,
) -> list[ChangelogEntry]:
    """Apply the changed settings keys through the store, one changelog entry
    per changed key (the first-mission format).

    ``changelog`` is the store's changelog read before the write (newest
    first); ``versions`` carries each key's stored ``updated_at`` for the
    optimistic lock (``None`` disables the check), read at the same moment as
    ``changelog``.  Every value is validated before any write
    (``ValueError`` joining the messages).  Keys whose stored value already
    equals the new value are skipped.  If a version check fails, no key is
    written and :class:`ConflictError` is raised.  Returns the written rows
    in the changed keys' order — one stored ``board.changelog`` row per
    changed key, with distinct ids."""
    if versions is None:
        versions = {}

    # 1. Validate before any write.
    errors: list[str] = []
    for key, value in changes.items():
        errors.extend(validate(key, value))
    if errors:
        raise ValueError("; ".join(errors))

    # 2. Read pass: skip unchanged keys, check versions before any write.
    to_write: dict[str, Any] = {}
    for key, new_value in changes.items():
        previous = store.get_setting(key)
        if previous is not None and previous.value_json == new_value:
            continue  # unchanged: nothing to write
        expected = versions.get(key)
        if expected is not None and expected != (previous.updated_at if previous else None):
            raise ConflictError(
                f"setting {key!r}: expected version {expected}, "
                f"got {previous.updated_at if previous else 'none'}"
            )
        to_write[key] = new_value

    # 3. Write pass: put each remaining changed key.
    for key, new_value in to_write.items():
        store.put_setting(key, new_value, versions.get(key), who=user)

    # 4. Return the real stored changelog rows, one per key we wrote, in the
    #    order written.  The rows are newest-first; match by mission_id.
    rows = store.changelog()
    by_key: dict[str, ChangelogEntry] = {}
    for entry in rows:
        if entry.action == "setting put" and entry.mission_id is not None:
            by_key.setdefault(entry.mission_id, entry)
    written: list[ChangelogEntry] = []
    for key in to_write:
        entry = by_key.get(key)
        if entry is not None:
            written.append(entry)
    return written
