from __future__ import annotations

import fnmatch
import os
from pathlib import Path

from claude_runner.config import DEFAULT_RISK_TIER_PATTERNS, Settings
from claude_runner.risk_tiers import CORE, SUPPORTING, UNMATCHED_TIER, group_by_tier, tier_of

REPO_ROOT = Path(__file__).resolve().parents[2]
SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", ".venv-dev", ".qa-venv"}


def _repo_paths() -> list[str]:
    paths: list[str] = []
    for dirpath, dirnames, filenames in os.walk(REPO_ROOT):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            paths.append(Path(dirpath, name).relative_to(REPO_ROOT).as_posix())
    return paths


def test_every_default_core_pattern_matches_a_real_file() -> None:
    paths = _repo_paths()
    for pattern, tier in DEFAULT_RISK_TIER_PATTERNS:
        if tier != CORE or pattern.startswith("data-tables/ops/"):
            continue  # the operator scripts folder is not in git
        matches = [p for p in paths if fnmatch.fnmatch(p, pattern)]
        assert matches, f"core pattern {pattern!r} matches no file in the repo tree"


def test_known_core_paths_are_core() -> None:
    for path in (
        "data-tables/ops/adaptive_split_patch.py",
        "claude-runner/src/claude_runner/dashboard_auth_limit.py",
        "claude-runner/src/claude_runner/roles_config.py",
        "claude-runner/src/claude_runner/board_engine.js",
        "claude-runner/src/claude_runner/runs.py",
        "claude-runner/src/claude_runner/board_ops_livestate.py",
    ):
        assert tier_of(path, DEFAULT_RISK_TIER_PATTERNS) == CORE, path


def test_supporting_paths_are_supporting() -> None:
    for path in (
        "docs/naming.md",
        "claude-runner/docs/roles-config.md",
        "claude-runner/src/claude_runner/board_i18n_ru.json",
        "claude-runner/tests/test_runs.py",
    ):
        assert tier_of(path, DEFAULT_RISK_TIER_PATTERNS) == SUPPORTING, path


def test_windows_separators_match_the_same_patterns() -> None:
    assert tier_of("data-tables\\ops\\adaptive_split_patch.py", DEFAULT_RISK_TIER_PATTERNS) == CORE
    assert (
        tier_of("claude-runner\\src\\claude_runner\\dashboard_auth.py", DEFAULT_RISK_TIER_PATTERNS)
        == CORE
    )
    assert tier_of("claude-runner\\docs\\naming.md", DEFAULT_RISK_TIER_PATTERNS) == SUPPORTING


def test_unmatched_path_gets_the_stated_default_core() -> None:
    assert UNMATCHED_TIER == CORE
    assert tier_of("claude-runner/src/claude_runner/api.py", DEFAULT_RISK_TIER_PATTERNS) == CORE


def test_first_matching_pattern_wins() -> None:
    patterns = (("*", "supporting"), ("notes*", "core"))
    assert tier_of("notes.txt", patterns) == SUPPORTING
    patterns = (("a*", "core"), ("a*", "supporting"))
    assert tier_of("abc.txt", patterns) == CORE


def test_unknown_tier_in_pattern_raises() -> None:
    try:
        tier_of("abc", (("abc", "dangerous"),))
        raise AssertionError("tier_of accepted an unknown tier")
    except ValueError as exc:
        assert "unknown tier" in str(exc)


def test_risk_tiers_flag_off_by_default() -> None:
    settings = Settings.from_env({})
    assert settings.risk_tiers_enabled is False
    assert settings.risk_tier_patterns == DEFAULT_RISK_TIER_PATTERNS


def test_group_by_tier_buckets_in_first_seen_order_and_collapses_duplicates() -> None:
    grouped = group_by_tier(
        [
            "docs/naming.md",
            "claude-runner/src/claude_runner/runs.py",
            "docs/naming.md",
            "claude-runner/src/claude_runner/api.py",
            "data-tables/ops/split_patch.py",
        ],
        DEFAULT_RISK_TIER_PATTERNS,
    )
    assert grouped[CORE] == [
        "claude-runner/src/claude_runner/runs.py",
        "claude-runner/src/claude_runner/api.py",
        "data-tables/ops/split_patch.py",
    ]
    assert grouped[SUPPORTING] == ["docs/naming.md"]
