"""A restore point of the tracked working-tree changes, taken before a Worker run starts.

A Worker that compares its files with HEAD can wipe the uncommitted edits (``git stash`` plus a
dropped stash, or ``git show HEAD:<file> > <file>``). ``git stash create`` writes a commit of the
tracked changes without touching the tree, the index or the stash list; the commit is kept under
``refs/ahawr/snapshots/<run_id>`` so ``git gc`` does not collect it, and the oldest refs go once
there are more than ``keep``. Restore with ``git checkout <ref> -- <path>`` or
``git stash apply <ref>``.

shortcut: tracked files only (untracked files are not touched by the wipe commands), no remote copy;
upgrade when a wipe of untracked files is seen.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

logger = logging.getLogger("claude_runner.git_snapshot")

SNAPSHOT_REF_PREFIX = "refs/ahawr/snapshots/"
GIT_TIMEOUT_SECONDS = 20


def _git(cwd: Path, *args: str) -> str:
    done = subprocess.run(  # noqa: S603 - fixed argument list, no shell
        ["git", "-C", str(cwd), *args],
        capture_output=True,
        text=True,
        timeout=GIT_TIMEOUT_SECONDS,
        check=True,
    )
    return done.stdout.strip()


def take_snapshot(cwd: Path, run_id: str, keep: int) -> str | None:
    """Keep the tracked changes of the repository at ``cwd`` under a ref named after the run.

    Returns the ref, or None when there is nothing to keep or git failed (logged, never raised:
    a missing restore point must not stop the run).
    """
    try:
        commit = _git(cwd, "stash", "create", f"ahawr snapshot before {run_id}")
        if not commit:
            return None
        ref = SNAPSHOT_REF_PREFIX + run_id
        _git(cwd, "update-ref", ref, commit)
        _prune(cwd, keep)
        return ref
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("git snapshot for %s failed: %s", run_id, exc)
        return None


def _prune(cwd: Path, keep: int) -> None:
    refs = _git(
        cwd, "for-each-ref", "--sort=creatordate", "--format=%(refname)", SNAPSHOT_REF_PREFIX
    ).splitlines()
    for ref in refs[: max(0, len(refs) - keep)]:
        _git(cwd, "update-ref", "-d", ref)
