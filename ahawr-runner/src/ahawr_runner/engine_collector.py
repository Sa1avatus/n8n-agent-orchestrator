"""Incremental engine log collector.

One watcher loop, following ``GuardSupervisor._run_guard`` in guards.py: it runs
while the stop event is unset, and every poll reads the engine log from the byte
offset kept in the collector's own state, so only new bytes are parsed. A file
whose size is below the stored offset (truncation or a rewritten log) restarts
the read from 0; an incomplete last line is kept until its newline arrives.
Lines are parsed by ``engine_log.parse_line``, the stored ``engine_run``
increments on every 'engine started:' line, and each batch of samples is
stamped with the collector's own UTC time (the engine log carries no
timestamps). Rows go through ``engine_samples.insert_sample`` (source
'sampled', idempotent on ``sample_key``). A database write is retried up to
``max_db_retries`` times with ``backoff_seconds`` between attempts; exhausting
the retries rewinds the read to the start of the batch and waits
``cooldown_seconds`` before the batch is read and written again (the rows are
idempotent on ``sample_key``), so a database outage never ends the collector.

Settings come from ``CollectorSettings`` (the log path is a setting, never a
literal in the loop), with safe defaults for the container's engine log;
``CollectorSettings.from_env`` reads the dashboard's DASHBOARD_ENGINE_* settings.
"""

from __future__ import annotations

import hashlib
import logging
import os
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from .engine_log import (
    DraftsAccepted,
    EngineRestart,
    ExpertCacheSlots,
    Generation,
    LowVramWarning,
    Sample,
    VramFree,
    parse_line,
)
from .engine_samples import insert_sample

logger = logging.getLogger("ahawr_runner.engine_collector")

DEFAULT_ENGINE_LOG_PATH = "/strata-data/state/strata/launch/docker.engine.log"
DEFAULT_POLL_SECONDS = 60.0
DEFAULT_MAX_BATCH_LINES = 200
DEFAULT_MAX_DB_RETRIES = 3
DEFAULT_BACKOFF_SECONDS = 5.0
DEFAULT_COOLDOWN_SECONDS = 300.0


def _positive_number(env: Mapping[str, str], name: str, default: float) -> float:
    """A positive number from the environment; anything else keeps the default,
    logged with the offending value so a bad setting is visible, not silent."""
    raw = (env.get(name) or "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        logger.warning("engine collector: %s=%r is not a number, using %s", name, raw, default)
        return default
    if value <= 0:
        logger.warning(
            "engine collector: %s=%r is not positive, using %s", name, raw, default
        )
        return default
    return value


@dataclass
class CollectorSettings:
    """Collector settings: where the engine log is, how often to poll, how many
    complete lines one poll may consume, and how often a failed database batch
    is retried before the collector gives up."""

    log_path: str = DEFAULT_ENGINE_LOG_PATH
    poll_seconds: float = DEFAULT_POLL_SECONDS
    max_batch_lines: int = DEFAULT_MAX_BATCH_LINES
    max_db_retries: int = DEFAULT_MAX_DB_RETRIES
    backoff_seconds: float = DEFAULT_BACKOFF_SECONDS
    cooldown_seconds: float = DEFAULT_COOLDOWN_SECONDS

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> CollectorSettings:
        """Settings from the dashboard environment: DASHBOARD_ENGINE_LOG_PATH,
        DASHBOARD_ENGINE_POLL_SECONDS, DASHBOARD_ENGINE_MAX_BATCH_LINES,
        DASHBOARD_ENGINE_MAX_DB_RETRIES, DASHBOARD_ENGINE_BACKOFF_SECONDS. A value
        that is not a positive number keeps the default and is logged."""
        import os

        env = dict(os.environ if env is None else env)
        return cls(
            log_path=env.get("DASHBOARD_ENGINE_LOG_PATH", "").strip() or DEFAULT_ENGINE_LOG_PATH,
            poll_seconds=_positive_number(
                env, "DASHBOARD_ENGINE_POLL_SECONDS", DEFAULT_POLL_SECONDS
            ),
            max_batch_lines=int(
                _positive_number(
                    env, "DASHBOARD_ENGINE_MAX_BATCH_LINES", DEFAULT_MAX_BATCH_LINES
                )
            ),
            max_db_retries=int(
                _positive_number(
                    env, "DASHBOARD_ENGINE_MAX_DB_RETRIES", DEFAULT_MAX_DB_RETRIES
                )
            ),
            backoff_seconds=_positive_number(
                env, "DASHBOARD_ENGINE_BACKOFF_SECONDS", DEFAULT_BACKOFF_SECONDS
            ),
        )


def _utc_now() -> datetime:
    """The collector's batch stamp when no clock is injected."""
    return datetime.now(UTC)


def _sample_fields(sample: Sample) -> dict[str, Any]:
    """The insert columns one parsed sample fills; every other column is None."""
    fields: dict[str, Any] = {
        "generated_tokens": None,
        "tok_per_s": None,
        "drafts_accepted": None,
        "drafts_total": None,
        "expert_slots": None,
        "vram_free_mib": None,
        "low_vram": False,
    }
    if isinstance(sample, Generation):
        fields["generated_tokens"] = sample.tokens
        fields["tok_per_s"] = sample.tok_s
    elif isinstance(sample, DraftsAccepted):
        fields["drafts_accepted"] = sample.accepted
        fields["drafts_total"] = sample.total
    elif isinstance(sample, ExpertCacheSlots):
        fields["expert_slots"] = sample.slots
    elif isinstance(sample, VramFree):
        fields["vram_free_mib"] = sample.free_mib
    elif isinstance(sample, LowVramWarning):
        fields["vram_free_mib"] = sample.free_mib
        fields["low_vram"] = True
    return fields  # EngineRestart fills only engine_run, which the row carries


class EngineCollector:
    """Incremental engine log watcher: reads from the stored byte offset,
    parses complete lines, writes samples to board.engine_samples."""

    def __init__(
        self,
        settings: CollectorSettings,
        conn: Any,
        *,
        clock: Callable[[], datetime] = _utc_now,
        sleep: Callable[[float], None] | None = None,
        stop_event: threading.Event | None = None,
    ) -> None:
        self.settings = settings
        self.conn = conn
        self.clock = clock
        self.stop_event = stop_event or threading.Event()
        # the default wait ends at once on the stop event: a cooldown never holds up the shutdown
        self.sleep = sleep or self._wait
        self.offset = 0
        self.engine_run = 0
        self.partial_line = ""

    def _wait(self, seconds: float) -> None:
        self.stop_event.wait(seconds)

    def run(self) -> None:
        """The watcher loop: exits on the stop event. When the database retries of
        one batch are spent, the read is rewound to the start of the batch and the
        loop waits ``cooldown_seconds`` before it reads the batch again."""
        while not self.stop_event.is_set():
            batch_offset, batch_partial = self.offset, self.partial_line
            lines = self._read_lines()
            if lines:
                try:
                    self._write_batch(lines, batch_offset, self.clock())
                except Exception as exc:
                    logger.error(
                        "engine collector: database write failed after %d retries: %s;"
                        " the batch is retried in %s s",
                        self.settings.max_db_retries,
                        exc,
                        self.settings.cooldown_seconds,
                    )
                    self.offset, self.partial_line = batch_offset, batch_partial
                    self.sleep(self.settings.cooldown_seconds)
                    continue
            if self.stop_event.is_set():
                break
            self.sleep(self.settings.poll_seconds)
        logger.info(
            "engine collector stopped: offset %d, engine_run %d",
            self.offset,
            self.engine_run,
        )

    def _read_lines(self) -> list[str]:
        """New complete lines since the stored offset, capped at max_batch_lines.

        Truncation (size below the offset) restarts from 0; a last line without
        its newline stays in partial_line until the next poll completes it. When
        the cap bites, the offset advances only past the consumed lines, so the
        rest of the file is read on the next poll."""
        log_path = self.settings.log_path
        try:
            size = os.path.getsize(log_path)
        except OSError as exc:
            logger.warning("engine collector: cannot stat %s: %s", log_path, exc)
            return []
        if size < self.offset:
            logger.info(
                "engine collector: %s shrank to %d bytes (offset %d), restarting from 0",
                log_path,
                size,
                self.offset,
            )
            self.offset = 0
            self.partial_line = ""
        try:
            with open(log_path, "rb") as handle:
                handle.seek(self.offset)
                chunk = handle.read()
        except OSError as exc:
            logger.warning("engine collector: cannot read %s: %s", log_path, exc)
            return []
        if not chunk:
            return []

        data = self.partial_line.encode("utf-8") + chunk
        parts = data.split(b"\n")
        complete = parts[:-1]
        cap = max(self.settings.max_batch_lines, 1)
        if len(complete) > cap:
            capped = complete[:cap]
            partial_bytes = len(self.partial_line.encode("utf-8"))
            self.offset += sum(len(line) + 1 for line in capped) - partial_bytes
            self.partial_line = ""
            return [line.decode("utf-8", errors="replace") for line in capped]

        self.offset += len(chunk)
        self.partial_line = parts[-1].decode("utf-8", errors="replace")
        return [line.decode("utf-8", errors="replace") for line in complete]

    def _write_batch(
        self, lines: list[str], batch_offset: int, sampled_at: datetime
    ) -> int:
        """Insert one batch, retrying the whole batch up to max_db_retries times
        with backoff_seconds between attempts; the sample_key makes a retried
        batch a no-op, so no row duplicates. Raises when the retries are spent."""
        retries_used = 0
        while True:
            try:
                return self._insert_lines(lines, batch_offset, sampled_at)
            except Exception as exc:
                retries_used += 1
                if retries_used > self.settings.max_db_retries:
                    raise
                logger.warning(
                    "engine collector: database write failed (%s), retry %d of %d",
                    exc,
                    retries_used,
                    self.settings.max_db_retries,
                )
                if self.stop_event.is_set():
                    raise
                self.sleep(self.settings.backoff_seconds)

    def _insert_lines(
        self, lines: list[str], batch_offset: int, sampled_at: datetime
    ) -> int:
        """One insert per sample of every line; the restart marker first bumps
        engine_run so its own row carries the new run number. engine_run is
        committed only after the whole batch succeeds, so a retried attempt
        does not count the same restart line twice."""
        written = 0
        run = self.engine_run
        for line_index, line in enumerate(lines):
            for sample_index, sample in enumerate(parse_line(line)):
                if isinstance(sample, EngineRestart):
                    run += 1
                sample_key = hashlib.sha1(
                    f"{batch_offset}:{line_index}:{sample_index}:{line}".encode()
                ).hexdigest()
                insert_sample(
                    self.conn,
                    sampled_at=sampled_at,
                    engine_run=run,
                    sample_key=sample_key,
                    **_sample_fields(sample),
                )
                written += 1
        self.engine_run = run
        return written
