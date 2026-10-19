"""Parsers for the watcher files: the T005 fixtures, malformed lines,
empty input and redaction."""

from __future__ import annotations

from claude_runner.board_ops_parsers import (
    parse_mem_guard_events,
    parse_queue_log,
    parse_queue_watch_state,
    parse_vm_csv,
    parse_watchdog_csv,
)

NOW = "2026-10-17T07:08:00+00:00"

DOCKER_WATCHDOG_CSV = """\
time,engine,engine_ms,n8n,dashboard,llama,free_ram_gb
2026-10-17 07:00:00,1,84,200,200,200,4.1
2026-10-17 07:01:40,0,12000,0,0,0,3.9
2026-10-17 07:02:00,0,12000,0,0,0,3.9
2026-10-17 07:03:00,1,81,200,200,200,4.4
2026-10-17 07:03:40,1,88,200,503,200,4.3
2026-10-17 07:05:00,1,101,200,200,200,4.1
"""

DOCKER_VM_CSV = """\
time,avail_mb,swap_used_mb,mem_some10,mem_full10,io_some10,io_full10
2026-10-17 07:00:00,11286,0,0,0,0.03,0.02
2026-10-17 07:01:40,10402,128,0,0,0,0
2026-10-17 07:08:00,381,1760,8.2,12.4,31.7,40.2
"""

QUEUE_WATCH_STATE = '{"q": 108253, "d": 15135}'

REAL_QUEUE_LOG = """\
2026-10-11 21:00:11 === real queue: start (bench finished)
2026-10-11 21:01:58 started ahawr-telegram-bot-completion (run 1, attempts per task: 3)
2026-10-11 23:12:40 ended ahawr-telegram-bot-completion: active
2026-10-12 06:43:52 ended ahawr-telegram-bot-completion: completed
2026-10-12 06:50:10 started llamacpp-speed-tuning (run 1, attempts per task: 3)
2026-10-12 11:20:19 ended llamacpp-speed-tuning: completed
2026-10-12 11:26:41 started llamacpp-fa-longctx-decode (run 1, attempts per task: 3)
2026-10-12 13:37:30 ALERT: new missions were not inserted
2026-10-12 15:05:31 REAL QUEUE STOPPED: two failures in a row
2026-10-12 15:10:44 === real queue: start (bench finished)
2026-10-12 15:11:04   llamacpp-fa-longctx-decode: row enabled=1 state=active
2026-10-12 15:12:44 === real queue done
"""

MEM_GUARD_LOG = """\
memory guard killed pid 200 (python3): holds 5000 MB of memory (limit 3072 MB per process)
{"kind": "mem_guard", "pid": 201, "process": "python3", "memory_mb": 8192, "reason": "the container uses 5600 of 6144 MB; this is the biggest process (8192 MB)", "command": "python3 big.py --data"}
not an event line at all
"""


def test_parse_watchdog_csv_fixture():
    result = parse_watchdog_csv(DOCKER_WATCHDOG_CSV)
    assert result["heartbeat"] == "2026-10-17 07:05:00"
    # the engine came back on 07:03:00, a probe before the last one: that is
    # the last action.
    assert result["last_action"] == "engine back after a failed stretch"
    assert result["restarts_last_24h"] == 0


def test_parse_watchdog_csv_restart_and_incidents():
    text = (
        "time,engine,engine_ms,n8n,dashboard,llama,free_ram_gb\n"
        "2026-10-17 07:00:00,1,84,200,200,200,4.1\n"
        "2026-10-17 07:01:40,0,12000,0,0,0,3.9\n"
        "2026-10-17 07:02:00,0,12000,0,0,0,3.9\n"
        "2026-10-17 07:03:00,1,81,200,200,200,4.4\n"
        "2026-10-17 07:03:40,1,88,200,503,200,4.3\n"
    )
    result = parse_watchdog_csv(text)
    assert result["heartbeat"] == "2026-10-17 07:03:40"
    assert result["last_action"] == "engine back after a failed stretch"
    assert result["restarts_last_24h"] == 0
    text = text + "2026-10-17 07:04:00,1,90,200,200,200,4.3\n"
    text += "2026-10-17 07:04:20,1,85,200,200,200,4.2\n"
    text += "2026-10-17 07:04:40,1,83,200,200,200,4.2\n"
    # an incident-style line in the same CSV carries the action column;
    # it is the newest event, so it is the last action (and a restart).
    text += "2026-10-17 07:05:00,0,12000,0,0,0,4.1,docker-desktop-restart\n"
    result = parse_watchdog_csv(text)
    assert result["last_action"] == "docker-desktop-restart"
    assert result["restarts_last_24h"] == 1
    # a probe after the restart: the engine was never down between the
    # restart probe and this one, so there is no "engine back" event — the
    # restart incident stays the last action (still counted as a restart).
    text += "2026-10-17 07:05:20,1,89,200,200,200,4.3\n"
    result = parse_watchdog_csv(text)
    assert result["last_action"] == "docker-desktop-restart"
    assert result["restarts_last_24h"] == 1


def test_parse_vm_csv_fixture():
    points = parse_vm_csv(DOCKER_VM_CSV)
    assert len(points) == 3
    assert points[0] == {
        "time": "2026-10-17 07:00:00",
        "avail_mb": 11286.0,
        "swap_used_mb": 0.0,
        "mem_some10": 0.0,
        "mem_full10": 0.0,
        "io_some10": 0.03,
        "io_full10": 0.02,
    }
    assert points[-1]["avail_mb"] == 381.0
    assert points[-1]["mem_full10"] == 12.4


def test_parse_queue_watch_state_fixture():
    result = parse_queue_watch_state(QUEUE_WATCH_STATE)
    assert result["q"] == 108253
    assert result["d"] == 15135
    assert result["notices_per_hour"] == 0
    assert result["last_notifications"] == []
    result = parse_queue_watch_state(
        QUEUE_WATCH_STATE, queue_log_text=REAL_QUEUE_LOG, now=NOW
    )
    # within the last hour at 07:08: no dated marker lines in the fixture
    assert result["notices_per_hour"] == 0
    # the notice-worthy lines (newest last), text scrubbed and capped
    kinds = [item["kind"] for item in result["last_notifications"]]
    assert kinds == ["ended", "ended", "ended", "alert", "stopped", "done"]
    assert result["last_notifications"][0] == {
        "time": "2026-10-11 23:12:40",
        "kind": "ended",
        "text": "2026-10-11 23:12:40 ended ahawr-telegram-bot-completion: active",
    }
    assert len(result["last_notifications"]) == 6


def test_parse_queue_log_fixture():
    result = parse_queue_log(REAL_QUEUE_LOG, now=NOW)
    assert result["mission"] is None
    assert result["task"] is None
    assert result["last_event_at"] == "2026-10-12 15:12:44"
    assert result["runs_without_progress"] == 0
    # the stop is reset by the queue restart and 'real queue done'
    assert result["consecutive_failures"] == 0
    # the newest started line, still running
    text = (
        "2026-10-12 11:26:41 started llamacpp-fa-longctx-decode "
        "(run 1, attempts per task: 3)\n"
        "2026-10-17 06:50:10 started llamacpp-fa-longctx-decode "
        "(run 2, attempts per task: 3)\n"
    )
    result = parse_queue_log(text, now=NOW)
    assert result["mission"] == "llamacpp-fa-longctx-decode"
    assert result["task"] == 2
    assert result["last_event_age_s"] == 17 * 60 + 50
    # the no-progress protection counter
    text += "mission m: no progress in 4 runs, giving up\n"
    result = parse_queue_log(text, now=NOW)
    assert result["runs_without_progress"] == 4


def test_parse_queue_log_consecutive_failures():
    # the last two ends are non-completed: the trailing failed stretch is 2
    text = (
        "2026-10-12 11:20:19 ended a: completed\n"
        "2026-10-12 12:00:00 ended b: active\n"
        "2026-10-12 13:00:00 ended c: active\n"
    )
    assert parse_queue_log(text, now=NOW)["consecutive_failures"] == 2
    # a completed end resets the stretch
    text += "2026-10-12 14:00:00 ended d: completed\n"
    assert parse_queue_log(text, now=NOW)["consecutive_failures"] == 0
    # a queue restart resets the stretch
    text += "2026-10-12 14:01:00 === real queue: start (bench finished)\n"
    assert parse_queue_log(text, now=NOW)["consecutive_failures"] == 0
    # a REAL QUEUE STOPPED line counts as one failure; the queue restart
    # after it resets the count (a stop is not a mission end)
    text = "2026-10-12 15:05:31 REAL QUEUE STOPPED: db window failed for m\n"
    assert parse_queue_log(text, now=NOW)["consecutive_failures"] == 1
    text += "2026-10-12 15:10:44 === real queue: start (bench finished)\n"
    assert parse_queue_log(text, now=NOW)["consecutive_failures"] == 0


def test_parsers_tolerate_malformed_lines():
    assert parse_watchdog_csv("time,engine\nnot a row") == {
        "heartbeat": None,
        "last_action": None,
        "restarts_last_24h": 0,
    }
    assert parse_vm_csv("time,avail_mb\n11286\nbroken, line, here") == []
    assert parse_queue_watch_state("not json at all") == {
        "q": None,
        "d": None,
        "notices_per_hour": 0,
        "last_notifications": [],
    }
    assert parse_queue_log("no timestamps here\nstarted broken") == {
        "mission": None,
        "task": None,
        "last_event_at": None,
        "last_event_age_s": None,
        "runs_without_progress": 0,
        "consecutive_failures": 0,
    }
    assert parse_mem_guard_events("garbage line without braces") == []


def test_parsers_empty_input():
    assert parse_watchdog_csv("") == {
        "heartbeat": None,
        "last_action": None,
        "restarts_last_24h": 0,
    }
    assert parse_vm_csv("") == []
    assert parse_queue_watch_state("") == {
        "q": None,
        "d": None,
        "notices_per_hour": 0,
        "last_notifications": [],
    }
    assert parse_queue_log("") == {
        "mission": None,
        "task": None,
        "last_event_at": None,
        "last_event_age_s": None,
        "runs_without_progress": 0,
        "consecutive_failures": 0,
    }
    assert parse_mem_guard_events("") == []


def test_parse_mem_guard_events_fixture():
    events = parse_mem_guard_events(MEM_GUARD_LOG)
    assert len(events) == 2
    assert events[0] == {
        "kind": "mem_guard",
        "pid": 200,
        "process": "python3",
        "memory_mb": None,
        "reason": "holds 5000 MB of memory (limit 3072 MB per process)",
        "command": None,
    }
    assert events[1]["pid"] == 201
    assert events[1]["memory_mb"] == 8192


def test_parse_queue_watch_state_drops_secret_fields():
    state = '{"q": 1, "d": 2, "token": "sk-abcdef1234567890abcd1234", "tg": "TELEGRAM_BOT_TOKEN=abc1234567890123456789", "chat_id": "123456789"}'
    result = parse_queue_watch_state(state)
    # only q / d / notices_per_hour / last_notifications survive
    assert set(result) == {"q", "d", "notices_per_hour", "last_notifications"}
    # and no secret survives anywhere in the parsed result
    for value in result.values():
        text = value if isinstance(value, str) else ""
        assert "sk-" not in text
        assert "TELEGRAM_BOT_TOKEN" not in text
        assert "123456789" not in text


def test_parse_queue_watch_state_scrubs_notifications():
    log = (
        "2026-10-17 07:00:00 ended m: completed token sk-abcdefghijklmnopqrstuvwx\n"
        "2026-10-17 07:01:00 ALERT: TELEGRAM_TOKEN=123:abc leaked\n"
    )
    result = parse_queue_watch_state('{"q": 1, "d": 2}', queue_log_text=log)
    notifications = result["last_notifications"]
    assert [item["kind"] for item in notifications] == ["ended", "alert"]
    for item in notifications:
        assert "sk-abcdefghijklmnopqrstuvwx" not in item["text"]
        assert "123:abc" not in item["text"]
    assert "***" in notifications[0]["text"]
    assert "***" in notifications[1]["text"]
