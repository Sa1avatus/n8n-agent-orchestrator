"""Run-based bench figures (ahawr_runner/stats_figures.py): the numbers of the bench
table (closed tasks, scores, extra attempts, hours, steps, tokens, tok/s, cache, cost)
for a mission, a task, an attempt and a project, computed with
``mission_stats.stats_for_runs`` and the ``board_stats`` run linkage."""

from datetime import datetime, timedelta
from typing import Any

import pytest

from ahawr_runner import stats_figures as stats_figures_module
from ahawr_runner.board import Mission
from ahawr_runner.board_stats import _mission_project
from ahawr_runner.config import Settings
from ahawr_runner.mission_stats import stats_for_runs
from ahawr_runner.runs import RunManager
from ahawr_runner.stats_figures import (
    SONNET_PRICE_USD_PER_MILLION_TOKENS,
    mission_figures,
    mission_reviews,
    project_figures,
    run_figures,
    sonnet_rates_usd,
    task_figures,
)
from ahawr_runner.store import Store
from tests.test_mission_stats import PRICING_ENV, add_run, event, mission_board, write_events


@pytest.fixture(autouse=True)
def clear_figures_cache() -> None:
    stats_figures_module._FIGURES_CACHE.clear()


@pytest.fixture
def bench_manager(settings: Settings) -> RunManager:
    """A RunManager priced with the Sonnet and Haiku profiles of the bench fixture."""
    s = Settings.from_env(
        {
            "CLAUDE_RUNNER_DATA_DIR": str(settings.data_dir),
            "CLAUDE_RUNNER_CLAUDE_BIN": settings.claude_bin,
            "CLAUDE_RUNNER_WORKSPACE": str(settings.workspace),
            "CLAUDE_RUNNER_PATH_MAP": ";".join(
                f"{src_path}={dst_path}" for src_path, dst_path in settings.path_map
            ),
            "CLAUDE_RUNNER_MAX_CONCURRENT": "2",
            "CLAUDE_RUNNER_COMPACT_MIN_TOKENS": "2500",
            **PRICING_ENV,
        }
    )
    return RunManager(s, Store(settings.data_dir / "test.sqlite"))


def seeded_board(manager: RunManager) -> Any:
    """The mission fixture: two tasks, three attempts, four runs.

    run_w1: worker, 10 min, 2 steps (20 s + 10 s generation), 1 compaction entry;
    run_w2: worker, 5 min, 1 step (10 s generation);
    run_r1: reviewer, 5 min; run_a1: architect, 3 min.
    T001: attempt 0 needs_changes 55, attempt 1 pass 90; T002: attempt 0 pass 80.
    """
    board = mission_board(manager, manager.settings)
    store = manager.store
    base = datetime(2026, 1, 1, 10, 0)
    add_run(store, "run_w1", "s1", "worker", base, base + timedelta(minutes=10), 2.0,
           provider="local")
    add_run(store, "run_w2", "s1", "worker", base + timedelta(minutes=20),
           base + timedelta(minutes=25), 1.0, provider="local")
    add_run(store, "run_r1", "s2", "reviewer", base + timedelta(minutes=30),
           base + timedelta(minutes=35), 0.5)
    add_run(store, "run_a1", "s3", "architect", base, base + timedelta(minutes=3), 0.25)
    write_events(manager.events, "run_w1", [
        {"kind": "step", "step": 1, "started": 100, "input_tokens": 1_000_000,
         "output_tokens": 100_000, "cache_read_tokens": 200_000,
         "cache_write_tokens": 0, "generation_ms": 20_000},
        {"kind": "step", "step": 2, "started": 200, "input_tokens": 1_000_000,
         "output_tokens": 100_000, "cache_read_tokens": 300_000,
         "cache_write_tokens": 0, "generation_ms": 10_000},
        {"kind": "compact", "t": 300},
    ])
    write_events(manager.events, "run_w2", [
        {"kind": "step", "step": 1, "started": 100, "input_tokens": 500_000,
         "output_tokens": 50_000, "cache_read_tokens": 0,
         "cache_write_tokens": 0, "generation_ms": 10_000},
    ])
    board.append_event(
        "sk1",
        event("sk1", task_id="T001", task_index=0, task_attempt=0,
              event_type="review_parsed", worker_run_id="run_w1",
              review_status="needs_changes", review_score=55.0,
              review_reason="not enough", reviewer_run_id="run_r1",
              reviewer_status="completed"),
    )
    board.append_event(
        "sk1",
        event("sk1", task_id="T001", task_index=0, task_attempt=1,
              event_type="review_parsed", worker_run_id="run_w2",
              review_status="pass", review_score=90.0, review_reason="ok",
              reviewer_run_id="run_r1", reviewer_status="completed"),
    )
    board.append_event(
        "sk1",
        event("sk1", task_id="T002", task_index=1, task_attempt=0,
              event_type="review_parsed", worker_run_id="run_w1",
              review_status="pass", review_score=80.0, review_reason="ok",
              reviewer_run_id="run_r1", reviewer_status="completed"),
    )
    board.upsert_live_state(
        "sk1", None, plan={"tasks": [{"id": "T001"}, {"id": "T002"}]},
        task_index=1, task_attempt=0, status="active",
        worker_run_id="run_w2", worker_status="completed",
        reviewer_run_id="run_r1", reviewer_status="completed",
        architect_run_id="run_a1", architect_status="completed",
        last_review_score=90.0, last_review_reason="ok",
    )
    return board


def test_mission_figures_match_stats_for_runs(bench_manager: RunManager) -> None:
    """The run-based time, speed, cache and compaction figures are the ones
    mission_stats gives today, unchanged, on the same fixture."""
    board = seeded_board(bench_manager)
    stats = stats_of_fixture_runs(bench_manager)
    figures = mission_figures(bench_manager, board, "sk1")
    assert figures["worker_hours"] == stats["worker_hours"] == 0.25
    assert figures["reviewer_minutes"] == stats["reviewer_minutes"] == 5.0
    assert figures["tok_per_s"] == stats["tok_per_s"] == 6250.0
    assert figures["compactions"] == stats["compactions"] == 1
    assert figures["cache_share_percent"] == round(100 * stats["cache_ratio"], 1) == 16.7


def stats_of_fixture_runs(manager: RunManager) -> dict[str, Any]:
    """``stats_for_runs`` on exactly the mission fixture's runs."""
    return stats_for_runs(manager, ["run_w1", "run_w2", "run_r1", "run_a1"])


def test_mission_figures_full_table(bench_manager: RunManager) -> None:
    board = seeded_board(bench_manager)
    figures = mission_figures(bench_manager, board, "sk1")
    assert figures["closed_tasks"] == 2
    assert figures["planned_tasks"] == 2
    assert figures["avg_final_review_score"] == 85.0
    assert figures["avg_first_review_score"] == 67.5
    assert figures["extra_attempts"] == 1
    assert figures["minutes_per_task"] == 7.5
    assert figures["steps"] == 3
    assert figures["steps_per_task"] == 1.5
    assert figures["output_tokens"] == 250_000
    assert figures["tokens_per_step"] == 83_333.3
    assert figures["generation_share_percent"] == 4.4
    # the Sonnet rates of the fixture's pricing env: 2,10,0.2,4 USD per 1M tokens, on
    # 2.5M input, 0.25M output, 0.5M cache read
    assert figures["cost_usd"] == round(2.5 * 2 + 0.25 * 10 + 0.5 * 0.2, 2) == 7.6


def test_mission_figures_per_task_rows(bench_manager: RunManager) -> None:
    board = seeded_board(bench_manager)
    rows = mission_figures(bench_manager, board, "sk1")["tasks"]
    assert rows == [
        {"task_id": "T001", "attempts": 2, "first_review_score": 55.0,
         "final_review_score": 90.0, "passed": True},
        {"task_id": "T002", "attempts": 1, "first_review_score": 80.0,
         "final_review_score": 80.0, "passed": True},
    ]


def test_task_figures_with_per_attempt_rows(bench_manager: RunManager) -> None:
    board = seeded_board(bench_manager)
    figures = task_figures(bench_manager, board, "sk1", "T001")
    assert figures["worker_hours"] == 0.25
    assert figures["closed_tasks"] == 1
    assert figures["avg_final_review_score"] == 90.0
    attempts = figures["attempts"]
    assert [row["attempt"] for row in attempts] == [0, 1]
    assert attempts[0]["runs"] == ["run_r1", "run_w1"]
    assert attempts[1]["runs"] == ["run_w2"]
    first = attempts[0]["figures"]
    assert first["worker_hours"] == 0.17
    assert first["steps"] == 2
    assert first["output_tokens"] == 200_000
    assert first["tok_per_s"] == 6666.7
    assert first["closed_tasks"] == 0
    second = attempts[1]["figures"]
    assert second["steps"] == 1
    assert second["output_tokens"] == 50_000
    assert second["closed_tasks"] == 1


def test_uncomputable_ratios_are_none_with_reasons(bench_manager: RunManager) -> None:
    figures = run_figures(bench_manager, [], [])
    assert figures["tok_per_s"] is None
    assert figures["minutes_per_task"] is None
    assert figures["steps_per_task"] is None
    assert figures["tokens_per_step"] is None
    assert figures["cache_share_percent"] is None
    assert figures["generation_share_percent"] is None
    assert figures["avg_final_review_score"] is None
    assert figures["extra_attempts"] is None
    assert figures["planned_tasks"] is None
    assert figures["reasons"]["tok_per_s"] == "no Worker steps with measured generation time"
    assert figures["reasons"]["cache_share_percent"] == "no step input tokens"
    assert figures["reasons"]["avg_final_review_score"] == "no review scores"
    assert figures["reasons"]["planned_tasks"] == "no plan and no review rows"
    # sums over an empty scope are a real 0, not an uncomputable value
    assert figures["steps"] == 0
    assert figures["output_tokens"] == 0
    assert figures["cost_usd"] == 0.0
    assert figures["closed_tasks"] == 0


def test_missing_review_score_is_no_score_not_zero(bench_manager: RunManager) -> None:
    board = seeded_board(bench_manager)
    board.append_event(
        "sk1",
        event("sk1", task_id="T003", task_index=2, task_attempt=0,
              event_type="review_parsed", worker_run_id="run_w2",
              review_status="needs_changes", reviewer_run_id="run_r1"),
    )
    reviews = [row for row in mission_reviews(board, "sk1") if row.get("task_id") == "T003"]
    figures = run_figures(bench_manager, ["run_w2"], reviews, planned_tasks=1)
    assert figures["closed_tasks"] == 0
    assert figures["avg_first_review_score"] is None
    assert figures["reasons"]["avg_first_review_score"] == "no review scores"


# ------------------------------------------------------------------------ projects

MODEL_BENCH_PATTERN = "D:/ClaudeProjects/model-bench/*"


def project_board(manager: RunManager) -> Any:
    """The seeded mission (sk1, m1) plus two more missions and one stored project rule.

    m2 (sk2) is in the project the rule matches: run_w3 worker 9 min with one step
    (10 s generation, 60k output, 400k input, 100k cache read), run_r2 reviewer 2 min;
    T001 attempt 0 pass 70, T002 attempt 0 needs_changes 30. m3 (sk3) matches no rule,
    so its project is the default one, named after the last folder of the working
    directory ("app"): run_w4 worker 3 min, one step (5 s generation, 10k output),
    T001 attempt 0 pass 100.
    """
    board = seeded_board(manager)
    board.put_project_rules(
        [{"name": "model-bench", "pattern": MODEL_BENCH_PATTERN, "position": 1}], None,
    )
    board._missions["m1"] = board._missions["m1"].model_copy(
        update={"working_directory": "D:/ClaudeProjects/model-bench/run1/rag-platform"}
    )
    board._missions["m2"] = Mission(
        mission_id="m2", state_namespace="sk2", name="Bench B",
        working_directory="D:/ClaudeProjects/model-bench/run2/rag-platform",
    )
    board._missions["m3"] = Mission(
        mission_id="m3", state_namespace="sk3", name="Legacy app",
        working_directory="D:/ClaudeProjects/legacy/app",
    )
    store = manager.store
    base = datetime(2026, 1, 1, 12, 0)
    add_run(store, "run_w3", "s4", "worker", base, base + timedelta(minutes=9), 1.5,
           provider="local")
    add_run(store, "run_r2", "s5", "reviewer", base + timedelta(minutes=10),
           base + timedelta(minutes=12), 0.3)
    add_run(store, "run_w4", "s6", "worker", base + timedelta(minutes=15),
           base + timedelta(minutes=18), 0.5, provider="local")
    write_events(manager.events, "run_w3", [
        {"kind": "step", "step": 1, "started": 100, "input_tokens": 400_000,
         "output_tokens": 60_000, "cache_read_tokens": 100_000,
         "cache_write_tokens": 0, "generation_ms": 10_000},
    ])
    write_events(manager.events, "run_w4", [
        {"kind": "step", "step": 1, "started": 100, "input_tokens": 100_000,
         "output_tokens": 10_000, "cache_read_tokens": 0,
         "cache_write_tokens": 0, "generation_ms": 5_000},
    ])
    board.append_event(
        "sk2",
        event("sk2", task_id="T001", task_index=0, task_attempt=0,
              event_type="review_parsed", worker_run_id="run_w3",
              review_status="pass", review_score=70.0, review_reason="ok",
              reviewer_run_id="run_r2", reviewer_status="completed"),
    )
    board.append_event(
        "sk2",
        event("sk2", task_id="T002", task_index=1, task_attempt=0,
              event_type="review_parsed", worker_run_id="run_w3",
              review_status="needs_changes", review_score=30.0, review_reason="weak",
              reviewer_run_id="run_r2", reviewer_status="completed"),
    )
    board.append_event(
        "sk3",
        event("sk3", task_id="T001", task_index=0, task_attempt=0,
              event_type="review_parsed", worker_run_id="run_w4",
              review_status="pass", review_score=100.0, review_reason="ok"),
    )
    board.upsert_live_state(
        "sk2", None, plan={"tasks": [{"id": "T001"}, {"id": "T002"}]},
        task_index=1, task_attempt=0, status="active",
        worker_run_id="run_w3", worker_status="completed",
        reviewer_run_id="run_r2", reviewer_status="completed",
    )
    board.upsert_live_state(
        "sk3", None, plan={"tasks": [{"id": "T001"}]},
        task_index=0, task_attempt=0, status="active",
        worker_run_id="run_w4", worker_status="completed",
    )
    return board


def test_project_figures_of_two_missions(bench_manager: RunManager) -> None:
    """The project figures are the same bench metrics over every run of the project."""
    board = project_board(bench_manager)
    figures = project_figures(bench_manager, board, "model-bench")
    assert figures["closed_tasks"] == 3
    assert figures["planned_tasks"] == 4
    assert figures["avg_final_review_score"] == 67.5
    assert figures["avg_first_review_score"] == 58.8
    assert figures["extra_attempts"] == 1
    # Worker hours are the sum of the runs (10 + 5 + 9 min), not a mean of means
    assert figures["worker_hours"] == 0.4
    assert figures["minutes_per_task"] == 6.0
    assert figures["steps"] == 4
    assert figures["steps_per_task"] == 1.0
    assert figures["output_tokens"] == 310_000
    assert figures["tokens_per_step"] == 77_500.0
    assert figures["tok_per_s"] == 6200.0
    assert figures["cache_share_percent"] == 17.1
    assert figures["generation_share_percent"] == 3.5
    assert figures["compactions"] == 1
    assert figures["reviewer_minutes"] == 7.0
    assert figures["cost_usd"] == 9.02


def test_project_figures_group_by_the_stored_rule(bench_manager: RunManager) -> None:
    """The project's missions are exactly those _mission_project puts in that project."""
    board = project_board(bench_manager)
    rules, _ = board.get_project_rules()
    expected = [
        mission.mission_id
        for mission in board.list_missions()
        if _mission_project(mission.working_directory, rules)["key"] == "model-bench"
    ]
    figures = project_figures(bench_manager, board, "model-bench")
    assert [row["mission_id"] for row in figures["missions"]] == expected
    assert [row["state_key"] for row in figures["missions"]] == ["sk1", "sk2"]


def test_project_figures_aggregate_from_runs_not_from_mission_averages(
    bench_manager: RunManager,
) -> None:
    """Project totals come from the project's runs: the mean of the two missions'
    figures is a different number, so the run-based path is the one taken."""
    board = project_board(bench_manager)
    figures = project_figures(bench_manager, board, "model-bench")
    per_mission = [row["figures"] for row in figures["missions"]]
    mission_tok_s = (per_mission[0]["tok_per_s"], per_mission[1]["tok_per_s"])
    mission_cache = (per_mission[0]["cache_share_percent"], per_mission[1]["cache_share_percent"])
    assert mission_tok_s == (6250.0, 6000.0)
    assert figures["tok_per_s"] != (mission_tok_s[0] + mission_tok_s[1]) / 2
    assert mission_cache == (16.7, 20.0)
    assert figures["cache_share_percent"] != (mission_cache[0] + mission_cache[1]) / 2


def test_project_figures_per_mission_breakdown(bench_manager: RunManager) -> None:
    board = project_board(bench_manager)
    breakdown = project_figures(bench_manager, board, "model-bench")["missions"]
    assert breakdown[0]["figures"] == mission_figures(bench_manager, board, "sk1")
    second = breakdown[1]["figures"]
    assert second["worker_hours"] == 0.15
    assert second["closed_tasks"] == 1
    assert second["planned_tasks"] == 2
    assert second["steps"] == 1
    assert second["output_tokens"] == 60_000
    assert second["cost_usd"] == 1.42
    assert second["tasks"] == [
        {"task_id": "T001", "attempts": 1, "first_review_score": 70.0,
         "final_review_score": 70.0, "passed": True},
        {"task_id": "T002", "attempts": 1, "first_review_score": 30.0,
         "final_review_score": 30.0, "passed": False},
    ]


def test_project_figures_of_a_mission_outside_the_project_are_excluded(
    bench_manager: RunManager,
) -> None:
    """sk3 shares task ids with the project's missions and its run_w4 would inflate
    the totals if the project key matched more than the rule allows."""
    board = project_board(bench_manager)
    figures = project_figures(bench_manager, board, "model-bench")
    assert figures["steps"] == 4
    assert figures["output_tokens"] == 310_000
    assert figures["closed_tasks"] == 3
    assert figures["avg_final_review_score"] == 67.5
    assert [row["state_key"] for row in figures["missions"]] == ["sk1", "sk2"]


def test_project_figures_of_a_single_mission_project(bench_manager: RunManager) -> None:
    """The default project (no rule matched) is keyed by the last folder of the
    working directory, which is how the Projects view names it."""
    board = project_board(bench_manager)
    figures = project_figures(bench_manager, board, "app")
    assert [row["mission_id"] for row in figures["missions"]] == ["m3"]
    assert figures["worker_hours"] == 0.05
    assert figures["closed_tasks"] == 1
    assert figures["planned_tasks"] == 1
    assert figures["steps"] == 1
    assert figures["tok_per_s"] == 2000.0
    assert figures["missions"][0]["figures"] == mission_figures(bench_manager, board, "sk3")


def test_project_figures_of_an_empty_project(bench_manager: RunManager) -> None:
    board = project_board(bench_manager)
    figures = project_figures(bench_manager, board, "no-such-project")
    assert figures["missions"] == []
    assert figures["closed_tasks"] == 0
    assert figures["steps"] == 0
    assert figures["output_tokens"] == 0
    assert figures["cost_usd"] == 0.0
    assert figures["worker_hours"] == 0.0
    assert figures["planned_tasks"] is None
    assert figures["tok_per_s"] is None
    assert figures["cache_share_percent"] is None
    assert figures["avg_final_review_score"] is None
    assert figures["reasons"]["planned_tasks"] == "no plan and no review rows"
    assert figures["reasons"]["tok_per_s"] == "no Worker steps with measured generation time"

def test_sonnet_rates_fall_back_to_the_constant_for_a_stub_pricing() -> None:
    class StubPricing:
        enabled = False

    class StubManager:
        local_pricing = StubPricing()

    manager: Any = StubManager()
    rates = sonnet_rates_usd(manager)
    assert rates == SONNET_PRICE_USD_PER_MILLION_TOKENS


def test_project_breakdown_reads_each_mission_once_within_the_ttl(
    bench_manager: RunManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    board = project_board(bench_manager)
    calls: list[str] = []
    real = stats_figures_module.mission_figures

    def counting(manager: RunManager, store: Any, state_key: str) -> dict[str, Any]:
        calls.append(state_key)
        return real(manager, store, state_key)

    monkeypatch.setattr(stats_figures_module, "mission_figures", counting)
    first = project_figures(bench_manager, board, "model-bench")
    reads = len(calls)
    second = project_figures(bench_manager, board, "model-bench")

    assert reads > 0
    assert len(calls) == reads  # the second open is answered from the cache
    assert second == first
