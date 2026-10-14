from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from claude_runner.store import Store
from claude_runner.think_switch import ThinkSwitch, parse_pulse, pulse_thinking, start_think, variant_for


def _iso(seconds_ago: float) -> str:
    return (datetime.now(UTC) - timedelta(seconds=seconds_ago)).isoformat(timespec="seconds")


def _run(store: Store, run_id: str, session: str, status: str, started: float, finished: float | None,
         role: str = "worker", provider: str = "local") -> None:
    store.create_run(
        run_id=run_id, kind="run", session_id=session, claude_session_id=session, role=role,
        model="m", provider=provider, cwd="/w", input="", status=status, created_at=_iso(started),
    )
    store.update_run(run_id, started_at=_iso(started), finished_at=_iso(finished) if finished is not None else None)


def test_switch_off_after_limit_and_back_on(tmp_path: Path) -> None:
    store = Store(tmp_path / "r.sqlite")
    flag = tmp_path / "runtime" / "local_thinking"
    switch = ThinkSwitch(store, flag, 3600)
    # a task that has run 40 minutes: nothing happens
    _run(store, "r1", "s1", "running", 2400, None)
    assert switch.tick() == ""
    assert not flag.exists()
    # an earlier run of the same session plus the running one add up to more than an hour
    _run(store, "r0", "s1", "failed", 7000, 5000)  # 2000 s
    assert switch.tick() == "off"
    assert flag.read_text() == "off"
    assert switch.tick() == ""  # already off: no repeat
    # the task's run ends: the flag stays (a retry of the same session must not flip the template)
    store.update_run("r1", status="completed", finished_at=_iso(0))
    assert switch.tick() == ""
    assert flag.read_text() == "off"
    # the next task (new session, short) starts: thinking comes back
    _run(store, "n1", "s2", "running", 30, None)
    assert switch.tick() == "on"
    assert flag.read_text() == "on"
    assert not switch.marker.exists()


def test_operator_off_is_never_touched(tmp_path: Path) -> None:
    store = Store(tmp_path / "r.sqlite")
    flag = tmp_path / "local_thinking"
    flag.write_text("off")
    switch = ThinkSwitch(store, flag, 3600)
    assert switch.tick() == ""  # no worker, no marker: leave the operator's off alone
    assert flag.read_text() == "off"


def test_other_roles_and_providers_do_not_count(tmp_path: Path) -> None:
    store = Store(tmp_path / "r.sqlite")
    flag = tmp_path / "local_thinking"
    switch = ThinkSwitch(store, flag, 100)
    _run(store, "a", "s1", "running", 5000, None, role="reviewer")
    _run(store, "b", "s2", "running", 5000, None, provider="anthropic")
    assert switch.tick() == ""
    assert not flag.exists()


def test_disabled_with_zero(tmp_path: Path) -> None:
    store = Store(tmp_path / "r.sqlite")
    _run(store, "a", "s1", "running", 99999, None)
    assert ThinkSwitch(store, tmp_path / "f", 0).tick() == ""


def _prompt_run(store: Store, run_id: str, session: str, status: str, started: float, finished: float | None, text: str) -> None:
    store.create_run(
        run_id=run_id, kind="run", session_id=session, claude_session_id=session, role="worker",
        model="m", provider="local", cwd="/w", input=text, status=status, created_at=_iso(started),
    )
    store.update_run(run_id, started_at=_iso(started), finished_at=_iso(finished) if finished is not None else None)


def test_policy_first_attempt_off_retry_on(tmp_path: Path) -> None:
    store = Store(tmp_path / "r.sqlite")
    flag = tmp_path / "local_thinking"
    flag.write_text("on")
    switch = ThinkSwitch(store, flag, 3600, policy=True, policy_from_task=10)
    # T009 is below the policy start: nothing happens
    _prompt_run(store, "a", "s9", "running", 60, None, "TASK T009: something")
    assert switch.tick() == ""
    assert flag.read_text() == "on"
    store.update_run("a", status="completed", finished_at=_iso(0))
    # T010, first attempt: off
    _prompt_run(store, "b", "s10", "running", 60, None, "TASK T010: something")
    assert switch.tick() == "off" and flag.read_text() == "off"
    assert switch.tick() == ""
    # a resume after a failed run is still the first attempt
    store.update_run("b", status="failed", finished_at=_iso(30))
    _prompt_run(store, "b2", "s10", "running", 20, None, "The previous attempt stopped.\n\nRESUME TASK T010: something")
    assert switch.tick() == ""  # still off
    # the run completes and goes to review; the retry has thinking on
    store.update_run("b2", status="completed", finished_at=_iso(10))
    _prompt_run(store, "c", "s10", "running", 5, None, "TASK T010: something (retry)")
    assert switch.tick() == "on" and flag.read_text() == "on"
    # a retry that runs longer than the hour goes off again
    store.update_run("c", status="completed", finished_at=_iso(1))
    flag.write_text("on")
    _prompt_run(store, "g", "s12", "completed", 9000, 5000, "TASK T012: x")
    _prompt_run(store, "h", "s12", "running", 4000, None, "TASK T012: x (retry)")
    assert switch.tick() == "off" and flag.read_text() == "off"
    # a compaction run never counts as a completed attempt
    _prompt_run(store, "e", "s11", "completed", 100, 90, "TASK T011: x")
    store.update_run("e", kind="compact")
    store.update_run("h", status="completed", finished_at=_iso(1))
    flag.write_text("on")
    _prompt_run(store, "f", "s11", "running", 5, None, "TASK T011: x")
    assert switch.tick() == "off"


def test_policy_off_without_task_id_is_ignored(tmp_path: Path) -> None:
    store = Store(tmp_path / "r.sqlite")
    flag = tmp_path / "local_thinking"
    switch = ThinkSwitch(store, flag, 3600, policy=True, policy_from_task=1)
    _prompt_run(store, "a", "s1", "running", 60, None, "just some prompt")
    assert switch.tick() == ""
    assert not flag.exists()


def test_run_settings_from_hermes_config_win_over_defaults(tmp_path: Path) -> None:
    store = Store(tmp_path / "r.sqlite")
    flag = tmp_path / "local_thinking"
    flag.write_text("on")
    switch = ThinkSwitch(store, flag, 3600)  # no policy in the defaults (the .env is empty)
    store.create_run(
        run_id="a", kind="run", session_id="s5", claude_session_id="s5", role="worker", model="m", provider="local",
        cwd="/w", input="TASK T005: x", status="running", created_at=_iso(60),
        think_policy="off_first", think_from=5, think_after=120,
    )
    store.update_run("a", started_at=_iso(60))
    assert switch.tick() == "off"  # the run asks for the policy from T5 on: first attempt without thinking
    store.update_run("a", status="completed", finished_at=_iso(10))
    flag.write_text("on")
    store.create_run(
        run_id="b", kind="run", session_id="s5", claude_session_id="s5", role="worker", model="m", provider="local",
        cwd="/w", input="TASK T005: x", status="running", created_at=_iso(5),
        think_policy="off_first", think_from=5, think_after=120,
    )
    store.update_run("b", started_at=_iso(5))
    assert switch.tick() == ""  # retry: thinking on, the 120 s limit is not reached yet
    assert flag.read_text() == "on"
    store.update_run("b", started_at=_iso(5))
    store.create_run(
        run_id="c", kind="run", session_id="s6", claude_session_id="s6", role="worker", model="m", provider="local",
        cwd="/w", input="TASK T006: y", status="running", created_at=_iso(5), think_policy="", think_from=0, think_after=-1,
    )
    store.update_run("c", started_at=_iso(5))
    store.update_run("b", status="completed", finished_at=_iso(1))
    switch.tick()  # T6 asks for nothing and the defaults have no policy: the old behaviour (marker cleared, thinking on)
    assert flag.read_text() == "on"


def test_thinking_is_off_during_a_compaction(tmp_path: Path) -> None:
    store = Store(tmp_path / "r.sqlite")
    flag = tmp_path / "local_thinking"
    flag.write_text("on")
    switch = ThinkSwitch(store, flag, 3600)
    _prompt_run(store, "w", "s1", "completed", 600, 400, "TASK T001: x")
    store.create_run(
        run_id="cmp", kind="compact", session_id="s1", claude_session_id="s1", role="worker", model="m", provider="local",
        cwd="/w", input="/compact", status="running", created_at=_iso(30),
    )
    store.update_run("cmp", started_at=_iso(30))
    assert switch.tick() == "off" and flag.read_text() == "off"
    assert switch.tick() == ""
    # the compaction ends and the Worker resumes: the old rule turns thinking back on (no policy here)
    store.update_run("cmp", status="completed", finished_at=_iso(1))
    _prompt_run(store, "w2", "s1", "running", 5, None, "RESUME TASK T001: x")
    assert switch.tick() == "on" and flag.read_text() == "on"


def test_task_number_is_read_from_the_whole_prompt(tmp_path: Path) -> None:
    from claude_runner.runs import task_number

    long_prompt = "You are a local coding Worker.\n" + "rules " * 1500 + "\nTASK T010: Patch script for main workflow\nObjective: x"
    assert task_number(long_prompt) == 10
    assert task_number("RESUME TASK T009: x") == 9
    assert task_number("no task here") == 0

    store = Store(tmp_path / "r.sqlite")
    flag = tmp_path / "local_thinking"
    flag.write_text("on")
    switch = ThinkSwitch(store, flag, 3600, policy=True, policy_from_task=10)
    store.create_run(
        run_id="a", kind="run", session_id="s10", claude_session_id="s10", role="worker", model="m", provider="local",
        cwd="/w", input=long_prompt, status="running", created_at=_iso(30), think_task=task_number(long_prompt),
    )
    store.update_run("a", started_at=_iso(30))
    # the head of the prompt (600 chars) does not name the task; the stored number does
    assert switch.tick() == "off" and flag.read_text() == "off"

class _Events:
    """Stand-in for EventLog: a directory with <run_id>.jsonl files and a recorder of the notes."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        directory.mkdir(parents=True, exist_ok=True)
        self.notes: list[tuple[str, dict]] = []

    def add(self, run_id: str, entry: dict) -> None:
        self.notes.append((run_id, entry))

    def steps(self, run_id: str, count: int, tool_events: int = 3) -> None:
        import json

        with (self.directory / f"{run_id}.jsonl").open("a", encoding="utf-8") as handle:
            for number in range(count):
                handle.write(json.dumps({"kind": "step", "step": number}) + chr(10))
            for _ in range(tool_events):
                handle.write(json.dumps({"kind": "tool_use", "name": "Bash"}) + chr(10))


def test_step_limit_counts_retry_steps_only(tmp_path: Path) -> None:
    store = Store(tmp_path / "r.sqlite")
    flag = tmp_path / "local_thinking"
    events = _Events(tmp_path / "events")
    switch = ThinkSwitch(store, flag, 0, events, policy=True, policy_from_task=1, after_steps=30)
    # the first attempt ran 80 steps (without thinking): they do not count against the retry
    _prompt_run(store, "a", "s3", "completed", 900, 600, "TASK T003: x")
    events.steps("a", 80)
    _prompt_run(store, "b", "s3", "running", 60, None, "TASK T003: x (retry)")
    events.steps("b", 20)
    assert switch.tick() == "" and not flag.exists()  # a retry: thinking stays on (the default)
    events.steps("b", 9)
    assert switch.tick() == ""  # 29 steps: still within the limit
    events.steps("b", 2)
    assert switch.tick() == "off" and flag.read_text() == "off"
    assert events.notes[-1][1]["reason"] == "retry over 30 steps"
    assert switch.tick() == ""  # already off


def test_step_limit_from_the_run_wins_and_zero_disables(tmp_path: Path) -> None:
    store = Store(tmp_path / "r.sqlite")
    flag = tmp_path / "local_thinking"
    events = _Events(tmp_path / "events")
    switch = ThinkSwitch(store, flag, 0, events, policy=True, policy_from_task=1, after_steps=1000)
    store.create_run(
        run_id="a", kind="run", session_id="s4", claude_session_id="s4", role="worker", model="m", provider="local",
        cwd="/w", input="TASK T004: x", status="completed", created_at=_iso(900), think_policy="off_first", think_from=1,
    )
    store.update_run("a", started_at=_iso(900), finished_at=_iso(600))
    store.create_run(
        run_id="b", kind="run", session_id="s4", claude_session_id="s4", role="worker", model="m", provider="local",
        cwd="/w", input="TASK T004: x", status="running", created_at=_iso(60), think_policy="off_first", think_from=1,
        think_after_steps=10,
    )
    store.update_run("b", started_at=_iso(60))
    events.steps("b", 11)
    assert switch.tick() == "off"  # the run's own limit (10) is used, not the default 1000
    # 0 in the run means "off", -1 means "use the default"; a default of 0 disables the limit
    store.update_run("b", think_after_steps=0)
    flag.write_text("on")
    assert switch.tick() == ""  # 0 = no step limit for this run
    assert flag.read_text() == "on"


def test_step_limit_without_policy(tmp_path: Path) -> None:
    store = Store(tmp_path / "r.sqlite")
    flag = tmp_path / "local_thinking"
    events = _Events(tmp_path / "events")
    switch = ThinkSwitch(store, flag, 0, events, after_steps=25)
    _run(store, "r1", "s7", "running", 60, None)
    events.steps("r1", 24)
    assert switch.tick() == ""
    events.steps("r1", 2)
    assert switch.tick() == "off" and flag.read_text() == "off"
    assert events.notes[-1][1]["limit_steps"] == 25


def test_pulse_parsing_and_cycle() -> None:
    assert parse_pulse("pulse:50:10:1") == (50, 10, 1)
    assert parse_pulse("pulse") == (25, 10, 1)
    assert parse_pulse("pulse:30,5,0") == (30, 5, 0)
    assert parse_pulse("pulse:x") is None and parse_pulse("off_first") is None and parse_pulse("pulse:10:0:1") is None
    assert [n for n in range(80) if pulse_thinking(n, 50, 10, 1)] == list(range(50, 60))  # one pulse: calls 51..60, then off for good
    assert [n for n in range(40) if pulse_thinking(n, 10, 5, 0)] == [10, 11, 12, 13, 14, 25, 26, 27, 28, 29, 35, 36, 37, 38, 39][:10]  # repeats for ever


def test_variants_alternate_by_task_number() -> None:
    policy = "pulse:50:10:1|off|off_first"
    assert [variant_for(policy, n) for n in (1, 2, 3, 4, 5)] == ["pulse:50:10:1", "off", "off_first", "pulse:50:10:1", "off"]
    assert variant_for("off_first", 7) == "off_first" and variant_for("", 3) == ""


def test_pulse_policy_switches_by_model_calls_of_the_attempt(tmp_path: Path) -> None:
    store = Store(tmp_path / "r.sqlite")
    flag = tmp_path / "local_thinking"
    events = _Events(tmp_path / "events")
    switch = ThinkSwitch(store, flag, 0, events, default_policy="pulse:5:2:1")
    _prompt_run(store, "a", "s9", "running", 60, None, "TASK T009: x")
    events.steps("a", 3)
    assert switch.tick() == "off" and flag.read_text() == "off"
    events.steps("a", 2)  # 5 calls made: the next one thinks
    assert switch.tick() == "on" and flag.read_text() == "on"
    assert "pulse" in events.notes[-1][1]["reason"]
    assert switch.tick() == ""
    events.steps("a", 2)  # 7 calls: the pulse is over, off for good
    assert switch.tick() == "off"
    events.steps("a", 20)
    assert switch.tick() == "" and flag.read_text() == "off"


def test_pulse_counts_each_attempt_from_the_last_review(tmp_path: Path) -> None:
    store = Store(tmp_path / "r.sqlite")
    flag = tmp_path / "local_thinking"
    events = _Events(tmp_path / "events")
    switch = ThinkSwitch(store, flag, 0, events, default_policy="pulse:5:2:1")
    _prompt_run(store, "a", "s9", "completed", 900, 600, "TASK T009: x")
    events.steps("a", 40)  # the first attempt made 40 calls: they do not count for the retry
    _prompt_run(store, "b", "s9", "running", 60, None, "TASK T009: x (retry)")
    events.steps("b", 1)
    assert switch.tick() == "off"
    events.steps("b", 4)
    assert switch.tick() == "on"  # 5 calls into the retry


def test_variant_off_never_thinks_and_variant_per_task(tmp_path: Path) -> None:
    store = Store(tmp_path / "r.sqlite")
    flag = tmp_path / "local_thinking"
    flag.write_text("on")
    events = _Events(tmp_path / "events")
    switch = ThinkSwitch(store, flag, 0, events, default_policy="pulse:5:2:1|off")
    _prompt_run(store, "a", "s2", "running", 60, None, "TASK T002: x")  # task 2 -> variant 'off'
    events.steps("a", 5)
    assert switch.tick() == "off" and flag.read_text() == "off"
    assert events.notes[-1][1]["reason"].startswith("policy:")


def test_retry_start_parsing() -> None:
    assert parse_pulse("pulse:25:10:1:r8") == (25, 10, 1) and start_think("pulse:25:10:1:r8", True) == 8 and start_think("pulse:25:10:1:r8", False) == 0
    assert start_think("pulse:25:10:1:f6:r8", False) == 6 and start_think("pulse:25:10:1:f6:r8", True) == 8
    assert start_think("pulse:25:10:1", True) == 0 and start_think("off", False) == 0 and parse_pulse("pulse:r5") == (25, 10, 1)
    assert parse_pulse("pulse:30:5:0:f8:r8") == (30, 5, 0)


def test_a_retry_thinks_through_the_review_first_then_follows_the_cycle(tmp_path: Path) -> None:
    store = Store(tmp_path / "r.sqlite")
    flag = tmp_path / "local_thinking"
    events = _Events(tmp_path / "events")
    switch = ThinkSwitch(store, flag, 0, events, default_policy="pulse:5:2:1:r3")
    _prompt_run(store, "a", "s9", "completed", 900, 600, "TASK T009: x")
    events.steps("a", 40)
    _prompt_run(store, "b", "s9", "running", 60, None, "TASK T009: x (retry)")
    flag.write_text("off")
    assert switch.tick() == "on" and flag.read_text() == "on"  # call 1 of the retry: thinks about the review
    assert "review analysis" in events.notes[-1][1]["reason"]
    events.steps("b", 3)  # 3 calls made: the analysis is over
    assert switch.tick() == "off"
    events.steps("b", 5)  # 8 calls = 3 + 5 without thinking: the pulse of the cycle starts
    assert switch.tick() == "on"
    events.steps("b", 2)
    assert switch.tick() == "off"


def test_a_first_attempt_plans_with_thinking_when_the_variant_asks(tmp_path: Path) -> None:
    store = Store(tmp_path / "r.sqlite")
    flag = tmp_path / "local_thinking"
    events = _Events(tmp_path / "events")
    switch = ThinkSwitch(store, flag, 0, events, default_policy="pulse:5:2:1:f3")
    _prompt_run(store, "a", "s8", "running", 60, None, "TASK T008: x")
    flag.write_text("off")
    assert switch.tick() == "on" and "planning" in events.notes[-1][1]["reason"]  # call 1: thinks about the plan
    events.steps("a", 3)
    assert switch.tick() == "off"  # the plan is made: without thinking, the cycle counts from call 4
    events.steps("a", 5)
    assert switch.tick() == "on"  # 8 calls: 3 + 5 without thinking, then the burst


def test_the_first_attempt_ignores_the_retry_only_start(tmp_path: Path) -> None:
    store = Store(tmp_path / "r.sqlite")
    flag = tmp_path / "local_thinking"
    events = _Events(tmp_path / "events")
    switch = ThinkSwitch(store, flag, 0, events, default_policy="pulse:5:2:1:r3")
    _prompt_run(store, "a", "s9", "running", 60, None, "TASK T009: x")
    assert switch.tick() == "off" and flag.read_text() == "off"
