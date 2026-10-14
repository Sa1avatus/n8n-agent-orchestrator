"""Switch the local model's thinking off for a task that has run too long.

A Worker session is one task. When the runs of a local Worker session add up to more than
``CLAUDE_RUNNER_THINK_OFF_AFTER_SECONDS`` (default 3600; 0 = off) and the task is still running, the
runner writes ``off`` to the LiteLLM flag file (``litellm/runtime/local_thinking``). LiteLLM reads it on
every request, so the switch applies to the next model call of the same run, with no restart.
The same holds for a step limit (``CLAUDE_RUNNER_THINK_OFF_AFTER_STEPS``, 0 = off): model calls of the session, counted from the
run event logs; whichever limit is reached first switches thinking off.
It writes ``on`` back only when a local Worker run of another, shorter session is running (the next task),
and only if it was the one that wrote ``off``: an ``off`` set by the operator (set_local_thinking.sh) is
never touched. A marker file next to the flag survives restarts.

Why not "on" as soon as the run ends: the chat template renders the history differently with thinking on
and off (a system preface at the very start), so every flip inside one session makes llama.cpp
re-evaluate the whole context (52k tokens = 215 s). Between two attempts at the same task the flag stays.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
from pathlib import Path
from typing import Any

from .store import Store

log = logging.getLogger("claude_runner.think_switch")

MARKER_SUFFIX = ".auto"

DEFAULT_PULSE = (25, 10, 1)


def variants(policy: str) -> list[str]:
    """A policy string may hold several variants separated by '|': task number n uses variant (n - 1) % count, so one mission compares them."""
    return [part.strip() for part in policy.split("|") if part.strip()] or [""]


def variant_for(policy: str, task_number: int) -> str:
    parts = variants(policy)
    return parts[(max(task_number, 1) - 1) % len(parts)]


def parse_pulse(variant: str) -> tuple[int, int, int] | None:
    """'pulse:50:10:1' -> (steps without thinking, steps with thinking, number of pulses; 0 = repeat for ever). 'pulse' alone = 25:10:1."""
    if not variant.startswith("pulse"):
        return None
    numbers = [part for part in re.split(r"[:,; ]+", variant[5:]) if part and not part.startswith("r")]
    try:
        values = [int(part) for part in numbers]
    except ValueError:
        return None
    off, think, pulses = (values + list(DEFAULT_PULSE[len(values):]))[:3] if values else DEFAULT_PULSE
    if off < 0 or think < 1 or pulses < 0:
        return None
    return off, think, pulses


def retry_think(variant: str) -> int:
    """'pulse:25:10:1:r8' -> 8: a retry (an attempt after a review) thinks for its first 8 model calls to work through the review, then follows the
    cycle counted from call 9. 0 = no such start."""
    match = re.search(r"[:,; ]r(\d+)", variant)
    return int(match.group(1)) if match else 0


def pulse_thinking(steps: int, off: int, think: int, pulses: int) -> bool:
    """True when the model call number ``steps`` + 1 should think: ``off`` calls without thinking, ``think`` calls with it, then without
    again (``pulses`` times; 0 = the cycle repeats for ever)."""
    cycle = off + think
    if pulses and steps >= pulses * cycle:
        return False
    return steps % cycle >= off


class ThinkSwitch:
    def __init__(
        self, store: Store, flag_file: Path, after_seconds: int, events: Any | None = None,
        policy: bool = False, policy_from_task: int = 1, after_steps: int = 0, default_policy: str = "",
    ) -> None:
        self.default_policy = default_policy
        self.after_steps = after_steps
        self._steps_seen: dict[str, tuple[int, int]] = {}  # run id -> (bytes read, steps counted)
        self.policy = policy
        self.policy_from_task = policy_from_task
        self.store = store
        self.flag_file = flag_file
        self.marker = flag_file.with_name(flag_file.name + MARKER_SUFFIX)
        self.after_seconds = after_seconds
        self.events = events

    def flag(self) -> str:
        """The current value of the flag file ('on' / 'off'); 'on' when it cannot be read."""
        return self._flag()

    def _flag(self) -> str:
        try:
            return self.flag_file.read_text(encoding="utf-8").strip().lower()
        except OSError:
            return "on"

    def _write(self, value: str) -> None:
        self.flag_file.parent.mkdir(parents=True, exist_ok=True)
        self.flag_file.write_text(value, encoding="utf-8")

    def _effective(self, run: dict[str, Any]) -> tuple[str, int, int, int]:
        """The run's own settings (sent by the workflow from hermes_config) over the CLAUDE_RUNNER_THINK_* defaults."""
        policy = (run.get("think_policy") or "") or self.default_policy or ("off_first" if self.policy else "")
        from_task = int(run.get("think_from") or 0) or self.policy_from_task
        after = int(run["think_after"]) if int(run.get("think_after", -1)) >= 0 else self.after_seconds
        steps = int(run["think_after_steps"]) if int(run.get("think_after_steps", -1)) >= 0 else self.after_steps
        return policy, from_task, after, steps

    def _steps(self, run_ids: list[str]) -> int:
        """Steps (model calls) the runs made, from their event logs; a file is read once, later only its new whole lines."""
        if self.events is None:
            return 0
        total = 0
        for run_id in run_ids:
            offset, count = self._steps_seen.get(run_id, (0, 0))
            try:
                with (self.events.directory / f"{run_id}.jsonl").open("rb") as handle:
                    handle.seek(offset)
                    data = handle.read()
            except OSError:
                total += count
                continue
            whole = data.rfind(b"\n") + 1
            for line in data[:whole].splitlines():
                if b'"step"' not in line:
                    continue
                try:
                    if json.loads(line).get("kind") == "step":
                        count += 1
                except ValueError:
                    continue
            self._steps_seen[run_id] = (offset + whole, count)
            total += count
        return total

    def _policy_tick(self) -> str | None:
        """Policy 'off first': the first attempt at a task runs without thinking, a retry after a review runs with it
        (and the hour limit counts the retries only). Returns None when no running Worker run falls under the policy."""
        for run in self.store.running_local_workers():
            policy, from_task, after, step_limit = self._effective(run)
            if not policy:
                continue
            # the task number is stored with the run (from the whole prompt); runs made before that are read from the head of the prompt
            found = re.search(r"TASK T0*(\d+)", run["head"] or "")
            number = int(run.get("think_task") or 0) or (int(found.group(1)) if found else 0)
            if number == 0 or number < from_task:
                continue
            variant = variant_for(policy, number)
            if variant == "off_first":
                pass
            elif variant == "off":
                return self._set("off", run, "policy: this task variant never thinks")
            elif (pulse := parse_pulse(variant)) is not None:
                # per attempt: the model calls made since the last review (a resume after a failed run belongs to the same attempt)
                boundary = self.store.last_completed_worker_finish(run["session_id"], run["run_id"])
                steps = self._steps(self.store.session_run_ids(run["session_id"], boundary))
                start = retry_think(variant) if boundary is not None else 0  # a retry first thinks through the review
                think = steps < start or pulse_thinking(steps - start, *pulse)
                return self._set("on" if think else "off", run, f"pulse {variant}: call {steps + 1}" + (" (review analysis)" if steps < start else ""))
            else:
                continue
            retry_from = self.store.first_completed_worker_finish(run["session_id"], run["run_id"])
            if retry_from is None:
                desired, why = "off", "first attempt"
            else:
                seconds = self.store.session_seconds_since(run["session_id"], retry_from)
                over = after > 0 and seconds > after
                steps = self._steps(self.store.session_run_ids(run["session_id"], retry_from)) if step_limit > 0 else 0
                over_steps = step_limit > 0 and steps > step_limit
                if over:
                    desired, why = "off", "retry over the time limit"
                elif over_steps:
                    desired, why = "off", f"retry over {step_limit} steps"
                else:
                    desired, why = "on", "retry after a review"
            if self._flag() != desired:
                self._write(desired)
                with contextlib.suppress(OSError):
                    self.marker.write_text("auto", encoding="utf-8")
                if self.events is not None:
                    self.events.add(run["run_id"], {"kind": "thinking_switch", "thinking": desired, "reason": why})
                log.warning("local thinking %s (%s)", desired, why)
                return desired
            return ""
        return None

    def _set(self, desired: str, run: dict[str, Any], why: str) -> str:
        """Writes the flag when it differs; returns what changed ('off' / 'on') or '' (the run's policy is handled, nothing to do)."""
        if self._flag() == desired:
            return ""
        self._write(desired)
        with contextlib.suppress(OSError):
            self.marker.write_text("auto", encoding="utf-8")
        if self.events is not None:
            self.events.add(run["run_id"], {"kind": "thinking_switch", "thinking": desired, "reason": why})
        log.warning("local thinking %s (%s)", desired, why)
        return desired

    def tick(self) -> str:
        """One check. Returns what happened: 'off', 'on' or '' (nothing changed)."""
        # A compaction only writes a summary: thinking would make it several times slower (23 min seen). Off while it runs; the policy or
        # the old rule below turns it back on for the Worker run that follows.
        compacting = self.store.local_compaction_running()
        if compacting:
            if self._flag() != "off":
                self._write("off")
                with contextlib.suppress(OSError):
                    self.marker.write_text("auto", encoding="utf-8")
                if self.events is not None:
                    self.events.add(compacting[0], {"kind": "thinking_switch", "thinking": "off", "reason": "compaction"})
                log.warning("local thinking off (compaction)")
                return "off"
            return ""
        handled = self._policy_tick()
        if handled is not None:
            return handled
        running = self.store.running_local_workers()
        after = next((int(r["think_after"]) for r in running if int(r.get("think_after", -1)) >= 0), self.after_seconds)
        over = self.store.local_worker_sessions_over(after)
        for r in running:
            limit = int(r["think_after_steps"]) if int(r.get("think_after_steps", -1)) >= 0 else self.after_steps
            if limit > 0 and not any(o["run_id"] == r["run_id"] for o in over):
                steps = self._steps(self.store.session_run_ids(r["session_id"]))
                if steps > limit:
                    over.append({"run_id": r["run_id"], "session_id": r["session_id"], "seconds": 0, "steps": steps, "limit_steps": limit})
        if over:
            if self._flag() != "off":
                self._write("off")
                self.marker.write_text("auto", encoding="utf-8")
                self._note(over, "off")
                log.warning("local thinking switched off: task session over %s s", self.after_seconds)
                return "off"
            return ""
        if self.marker.exists() and self.store.local_worker_running():
            if self._flag() == "off":
                self._write("on")
            with contextlib.suppress(OSError):
                self.marker.unlink()
            log.warning("local thinking switched back on")
            return "on"
        return ""

    def _note(self, over: list[dict[str, Any]], value: str) -> None:
        if self.events is None:
            return
        for item in over:
            self.events.add(
                item["run_id"],
                {
                    "kind": "thinking_switch",
                    "thinking": value,
                    "session_seconds": item["seconds"],
                    "limit_seconds": self.after_seconds,
                    **({"steps": item["steps"], "limit_steps": item["limit_steps"]} if "steps" in item else {}),
                },
            )

    async def run(self, every: float = 20.0) -> None:
        while True:
            try:
                await asyncio.to_thread(self.tick)
            except Exception:  # never let the watcher die
                log.exception("think switch tick failed")
            await asyncio.sleep(every)
