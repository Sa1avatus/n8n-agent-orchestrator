"""redact, normalise, trim and build_candidate of board_lessons_extract, with made-up data."""

from __future__ import annotations

import socket
from typing import Any

import pytest

from claude_runner.board_lessons import (
    LESSON_MAX_CHARS,
    SITUATION_MAX_CHARS,
    Lesson,
    LessonCandidate,
    LessonKind,
    LessonStatus,
)
from claude_runner.board_lessons_extract import (
    BLOCK_HEADING,
    LESSONS_BLOCK_MAX_CHARS,
    LESSONS_DEDUP_RATIO,
    LESSONS_K,
    build_candidate,
    decide,
    find_duplicate,
    normalise,
    redact,
    render_lessons_block,
    repeats_mission_rule,
    select_lessons,
    trim,
)

MAX_ATTEMPTS = 3
MISSION_TEXT = "Objective: keep the parser stable. Keep the empty lines in the parser."
UNRELATED_MISSION = "Objective: ship the release notes."


def _review(
    attempt: int,
    status: str,
    reason: str,
    score: int | None = None,
    next_task: str = "",
    paths: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "event_type": "review_parsed",
        "task_id": "T004",
        "task_index": 4,
        "task_attempt": attempt,
        "review_status": status,
        "review_score": score,
        "review_reason": reason,
        "review_next_task": next_task,
        "touched_paths": paths or [],
    }


def _build(events: list[dict[str, Any]]) -> LessonCandidate | None:
    return build_candidate(events, "fake-mission", MAX_ATTEMPTS)


@pytest.mark.parametrize(
    "secret",
    [
        "postgresql://fake_user:FakePass123@db.example.test:5432/fakedb",
        "Authorization: Bearer FAKEbearerTOKEN0123456789",
        "sk-FAKEtest0123456789abcdef",
        "ghp_FAKEFAKEFAKEFAKE1234",
        "xoxb-1234-FAKEFAKEFAKE",
    ],
)
def test_redact_removes_made_up_tokens_and_dsns(secret: str) -> None:
    out = redact(f"step failed with {secret} in the worker")

    assert secret not in out
    assert "FAKE" not in out
    assert "step failed with" in out


def test_redact_keeps_assignment_label_and_drops_value() -> None:
    out = redact("DATABASE_PASSWORD=FakeHunter2 started")

    assert "FakeHunter2" not in out
    assert "DATABASE_PASSWORD=" in out
    assert "started" in out


@pytest.mark.parametrize(
    ("path", "kept_tail"),
    [
        ("C:\\Users\\fakeuser\\proj\\a.py", "proj\\a.py"),
        ("c:/Users/fakeuser/proj/a.py", "proj/a.py"),
        ("/home/fakeuser/work/x.py", "work/x.py"),
        ("/d/Users/fakeuser/repo/y.py", "repo/y.py"),
    ],
)
def test_redact_removes_personal_home_prefix(path: str, kept_tail: str) -> None:
    out = redact(f"error in {path} line 3")

    assert "fakeuser" not in out
    assert f"error in {kept_tail} line 3" in out


def test_redact_keeps_repository_relative_paths() -> None:
    text = "see claude-runner/src/claude_runner/board_lessons.py and docs/home/readme.md"

    assert redact(text) == text


def test_redact_keeps_plain_log_text() -> None:
    text = "pytest tests/test_board_lessons.py passed in 2.1 s"

    assert redact(text) == text


def test_normalise_lowercases_drops_punctuation_and_collapses_whitespace() -> None:
    assert normalise("  Hello,   WORLD!  It's\tfine. ") == "hello world it s fine"


def test_normalise_makes_equal_texts_equal() -> None:
    assert normalise("Retry the build.") == normalise("retry   THE build")


def test_trim_keeps_text_within_limit() -> None:
    assert trim("alpha beta", 10) == "alpha beta"


def test_trim_cuts_on_word_boundary_with_ellipsis() -> None:
    assert trim("alpha beta gamma delta", 14) == "alpha beta..."
    assert trim("alpha beta gamma delta", 12) == "alpha..."


def test_trim_hard_cuts_a_single_long_word() -> None:
    assert trim("abcdefghijkl", 6) == "abc..."


def test_trim_respects_limit_for_every_size() -> None:
    text = "alpha beta gamma delta epsilon"
    for limit in range(0, len(text) + 2):
        out = trim(text, limit)
        assert len(out) <= limit
        if out.endswith("..."):
            stem = out[:-3]
            assert text.startswith(stem)
            if " " in stem:
                assert text[len(stem)].isspace()


def test_trim_below_ellipsis_length_is_a_plain_cut() -> None:
    assert trim("abcdef", 2) == "ab"


def test_retry_pair_gives_fixed_after_retry_with_both_reasons_as_evidence() -> None:
    events = [
        _review(1, "needs_changes", "the parser drops empty lines", 40,
                next_task="keep empty lines in the parser", paths=["src/parser.py"]),
        _review(2, "pass", "parser keeps empty lines now", 92, paths=["src/parser.py"]),
    ]

    candidate = _build(events)

    assert candidate is not None
    assert candidate.kind == "fixed-after-retry"
    assert candidate.task_index == 4
    assert candidate.task_id == "T004"
    assert candidate.lesson == "keep empty lines in the parser"
    assert candidate.situation == "the parser drops empty lines"
    assert candidate.evidence == {
        "failed_review_reason": "the parser drops empty lines",
        "accepted_review_reason": "parser keeps empty lines now",
        "failed_score": 40,
        "accepted_score": 92,
        "failed_attempt": 1,
        "accepted_attempt": 2,
        "touched_paths": ["src/parser.py"],
    }


def test_first_attempt_pass_returns_none() -> None:
    assert _build([_review(1, "pass", "all good", 95)]) is None


def test_exhausted_attempts_without_acceptance_give_gave_up() -> None:
    events = [
        _review(1, "needs_changes", "first failure", 30, next_task="fix A"),
        _review(2, "needs_changes", "second failure", 35, next_task="fix B"),
        _review(3, "needs_changes", "last failure", 20, next_task="fix C"),
    ]

    candidate = _build(events)

    assert candidate is not None
    assert candidate.kind == "gave-up"
    assert candidate.lesson == "fix C"
    assert candidate.evidence["failed_attempt"] == 3
    assert candidate.evidence["accepted_attempt"] is None
    assert candidate.evidence["accepted_review_reason"] == "last failure"


def test_task_still_running_below_the_cap_returns_none() -> None:
    assert _build([_review(1, "needs_changes", "failure", 30, next_task="fix")]) is None


def test_non_review_rows_are_not_attempts() -> None:
    events = [{"event_type": "worker_started", "task_attempt": 1, "task_index": 4}]

    assert _build(events) is None


def test_secrets_in_review_reason_are_redacted() -> None:
    secret = "postgresql://fake_user:FakePass123@db.example.test:5432/fakedb"
    events = [
        _review(1, "needs_changes", f"the worker printed {secret}", 30, next_task="fix it"),
        _review(2, "pass", "fixed", 90),
    ]

    candidate = _build(events)

    assert candidate is not None
    assert "FakePass123" not in candidate.situation
    assert "FakePass123" not in str(candidate.evidence)


def test_over_long_reasons_are_trimmed_to_the_limits() -> None:
    long_text = "word " * 200
    paths = [f"src/{index}/" + "a" * 200 + ".py" for index in range(7)]
    events = [
        _review(1, "needs_changes", long_text, 30, next_task=long_text),
        _review(2, "pass", long_text, 90, paths=paths),
    ]

    candidate = _build(events)

    assert candidate is not None
    assert len(candidate.situation) <= SITUATION_MAX_CHARS
    assert len(candidate.lesson) <= LESSON_MAX_CHARS
    assert len(candidate.evidence["failed_review_reason"]) <= 200
    assert len(candidate.evidence["accepted_review_reason"]) <= 200
    assert len(candidate.evidence["touched_paths"]) == 5
    assert all(len(path) <= 120 for path in candidate.evidence["touched_paths"])


def test_default_path_makes_no_network_call(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("network call on the default path")

    monkeypatch.setattr(socket, "socket", refuse)
    events = [
        _review(1, "needs_changes", "failure", 30, next_task="fix"),
        _review(2, "pass", "fixed", 90),
    ]

    assert _build(events) is not None


def test_rewrite_flag_is_off_by_default_and_not_built() -> None:
    events = [_review(1, "needs_changes", "failure", 30, next_task="fix")]

    with pytest.raises(NotImplementedError):
        build_candidate(events, "fake-mission", 1, rewrite_enabled=True)


def _candidate(situation: str, lesson: str) -> LessonCandidate:
    return LessonCandidate(
        mission="fake-mission",
        task_id="T004",
        task_index=4,
        kind="fixed-after-retry",
        situation=situation,
        lesson=lesson,
        evidence={},
    )


def _active(lesson_id: int, situation: str, lesson: str, status: LessonStatus = "active") -> Lesson:
    stamp = "2026-01-01T00:00:00Z"
    return Lesson(
        id=lesson_id,
        mission="fake-mission",
        task_id="T001",
        task_index=1,
        kind="fixed-after-retry",
        situation=situation,
        lesson=lesson,
        evidence={},
        created_at=stamp,
        status=status,
        times_shown=0,
        times_first_attempt_passed_after=0,
        times_seen=1,
        version=1,
        updated_at=stamp,
    )


EXISTING = _active(7, "the parser drops empty lines", "keep empty lines in the parser")


def test_near_identical_lesson_is_bumped_to_the_existing_id() -> None:
    candidate = _candidate("the parser drops the empty lines", "keep empty lines in the parser")

    assert decide(candidate, [EXISTING], UNRELATED_MISSION) == ("bump", 7)


def test_different_lesson_is_added() -> None:
    candidate = _candidate(
        "the docker build is slow", "rebuild the image after editing the dockerfile"
    )

    assert decide(candidate, [EXISTING], UNRELATED_MISSION) == "add"


def test_lesson_repeating_a_mission_rule_is_dropped() -> None:
    candidate = _candidate("the parser drops empty lines", "keep empty lines in the parser")

    assert repeats_mission_rule(candidate, MISSION_TEXT, LESSONS_DEDUP_RATIO)
    assert decide(candidate, [], MISSION_TEXT) == "drop"


def test_retired_lesson_does_not_count_as_duplicate() -> None:
    retired = _active(9, EXISTING.situation, EXISTING.lesson, status="retired")
    candidate = _candidate("the parser drops the empty lines", "keep empty lines in the parser")

    assert find_duplicate(candidate, [retired], LESSONS_DEDUP_RATIO) is None
    assert decide(candidate, [retired], UNRELATED_MISSION) == "add"


def _lesson(
    lesson_id: int,
    situation: str,
    *,
    created_at: str = "2026-01-01T00:00:00Z",
    kind: LessonKind = "fixed-after-retry",
    paths: list[str] | None = None,
    status: LessonStatus = "active",
    lesson: str = "keep the empty lines",
) -> Lesson:
    return Lesson(
        id=lesson_id,
        mission="fake-mission",
        task_id=f"T{lesson_id:03d}",
        task_index=1,
        kind=kind,
        situation=situation,
        lesson=lesson,
        evidence={"touched_paths": paths or []},
        created_at=created_at,
        status=status,
        times_shown=0,
        times_first_attempt_passed_after=0,
        times_seen=1,
        version=1,
        updated_at=created_at,
    )


def test_select_picks_at_most_k_lessons() -> None:
    lessons = [_lesson(i, "parser empty lines task") for i in range(1, 9)]

    picked = select_lessons(lessons, "parser empty lines", "", [], [], k=LESSONS_K)

    assert len(picked) == LESSONS_K


def test_select_keeps_block_within_max_chars() -> None:
    long_situation = "parser empty lines " * 20
    lessons = [_lesson(i, long_situation, lesson="x" * 300) for i in range(1, 9)]

    picked = select_lessons(lessons, "parser empty lines", "", [], [])
    block = render_lessons_block(picked)

    assert picked
    assert len(block) <= LESSONS_BLOCK_MAX_CHARS


def test_select_skips_lesson_that_does_not_fit_and_keeps_later_ones() -> None:
    big = _lesson(1, "parser empty lines " * 10, created_at="2026-02-01T00:00:00Z")
    small = _lesson(2, "parser empty lines", created_at="2026-01-01T00:00:00Z")
    limit = len(render_lessons_block([small]))

    picked = select_lessons([big, small], "parser empty lines", "", [], [], max_chars=limit)

    assert [lesson.id for lesson in picked] == [2]


def test_path_overlap_ranks_above_word_only_overlap() -> None:
    word_only = _lesson(1, "parser empty lines", created_at="2026-03-01T00:00:00Z")
    by_path = _lesson(2, "unrelated wording", paths=["src/parser.py"])

    picked = select_lessons(
        [word_only, by_path],
        "parser empty lines",
        "",
        [],
        ["src/parser.py"],
        k=1,
    )

    assert [lesson.id for lesson in picked] == [2]


def test_ties_go_newest_first() -> None:
    older = _lesson(1, "parser empty lines", created_at="2026-01-01T00:00:00Z")
    newer = _lesson(2, "parser empty lines", created_at="2026-02-01T00:00:00Z")

    picked = select_lessons([older, newer], "parser empty lines", "", [], [], k=1)

    assert [lesson.id for lesson in picked] == [2]


def test_retired_lessons_are_excluded() -> None:
    retired = _lesson(1, "parser empty lines", status="retired")

    assert select_lessons([retired], "parser empty lines", "", [], []) == []


def test_lesson_without_any_overlap_is_not_selected() -> None:
    lesson = _lesson(1, "docker build is slow", paths=["Dockerfile"])

    assert select_lessons([lesson], "parser empty lines", "", ["src/x.py"], ["src/x.py"]) == []


def test_empty_input_gives_empty_block() -> None:
    assert select_lessons([], "parser empty lines", "", [], []) == []
    assert render_lessons_block([]) == ""


def test_block_starts_with_exact_heading_and_lists_selected_lesson() -> None:
    lesson = _lesson(4, "parser empty lines", kind="gave-up", lesson="keep them")

    block = render_lessons_block([lesson])

    assert block.splitlines()[0] == (
        "LESSONS FROM EARLIER TASKS OF THIS MISSION "
        "(checked against reviews, verify against the code)"
    )
    assert block.splitlines()[1] == "- [gave-up] parser empty lines -> keep them (T004)"
    assert "do not override" in block


def test_flag_off_or_no_lessons_leaves_input_byte_for_byte() -> None:
    base = "Task: parser empty lines\n"
    block = render_lessons_block(select_lessons([], "parser empty lines", "", [], []))

    assert base + block == base
    assert BLOCK_HEADING in render_lessons_block([_lesson(1, "parser empty lines")])
