"""git_snapshot: a restore point of the tracked changes, and the Worker's denied git commands."""

import subprocess
from pathlib import Path

import pytest

from claude_runner import git_snapshot
from claude_runner.config import DEFAULT_PROFILES, Settings


def _git(repo: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    )
    return done.stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "qa@example.test")
    _git(tmp_path, "config", "user.name", "qa")
    (tmp_path / "a.txt").write_text("one\n", encoding="utf-8")
    _git(tmp_path, "add", "a.txt")
    _git(tmp_path, "commit", "-q", "-m", "first")
    return tmp_path


def test_snapshot_keeps_the_uncommitted_edit_and_leaves_the_tree_alone(repo: Path) -> None:
    (repo / "a.txt").write_text("two\n", encoding="utf-8")

    ref = git_snapshot.take_snapshot(repo, "run_1", keep=5)

    assert ref == "refs/ahawr/snapshots/run_1"
    assert (repo / "a.txt").read_text(encoding="utf-8") == "two\n"
    assert _git(repo, "stash", "list") == ""
    assert _git(repo, "show", f"{ref}:a.txt") == "two"


def test_snapshot_survives_a_wipe_of_the_file(repo: Path) -> None:
    (repo / "a.txt").write_text("two\n", encoding="utf-8")
    ref = git_snapshot.take_snapshot(repo, "run_1", keep=5)
    assert ref is not None

    _git(repo, "checkout", "--", "a.txt")
    assert (repo / "a.txt").read_text(encoding="utf-8") == "one\n"
    _git(repo, "checkout", ref, "--", "a.txt")

    assert (repo / "a.txt").read_text(encoding="utf-8") == "two\n"


def test_no_changes_means_no_snapshot(repo: Path) -> None:
    assert git_snapshot.take_snapshot(repo, "run_1", keep=5) is None


def test_the_oldest_refs_go_past_keep(repo: Path) -> None:
    for number in range(4):
        (repo / "a.txt").write_text(f"v{number}\n", encoding="utf-8")
        git_snapshot.take_snapshot(repo, f"run_{number}", keep=2)

    refs = _git(repo, "for-each-ref", "--format=%(refname)", git_snapshot.SNAPSHOT_REF_PREFIX)

    assert sorted(refs.splitlines()) == [
        "refs/ahawr/snapshots/run_2",
        "refs/ahawr/snapshots/run_3",
    ]


def test_a_folder_that_is_not_a_repository_gives_none_and_does_not_raise(tmp_path: Path) -> None:
    assert git_snapshot.take_snapshot(tmp_path, "run_1", keep=5) is None


@pytest.mark.parametrize(
    "rule",
    [
        "Bash(git stash)",
        "Bash(git stash *)",
        "Bash(git checkout *)",
        "Bash(git restore *)",
        "Bash(git reset *)",
        "Bash(git -C * stash *)",
        "Bash(git show *>*)",
        "Bash(git commit *)",
        "Bash(git push *)",
    ],
)
def test_the_worker_may_not_change_the_tree_with_git(rule: str) -> None:
    assert rule in DEFAULT_PROFILES["worker"].disallowed_tools


def test_the_worker_snapshot_flag_is_off_by_default() -> None:
    settings = Settings.from_env({})

    assert settings.worker_git_snapshot is False
    assert settings.git_snapshot_keep == 100
    assert Settings.from_env({"CLAUDE_RUNNER_WORKER_GIT_SNAPSHOT": "1"}).worker_git_snapshot
