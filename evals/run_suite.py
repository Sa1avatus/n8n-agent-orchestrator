"""Run the worker suite against a ahawr-runner: copy, launch, poll and check each task."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypedDict

import httpx
from ahawr_runner.redact import redact

EVALS_DIR = Path(__file__).resolve().parent
SUITE_FILE = EVALS_DIR / "worker-suite" / "suite.json"
DEFAULT_OUTPUT_DIR = EVALS_DIR / "results"
RESULTS_GLOB = "results-*.json"
REASON_COLUMN_CHARS = 60
TIMED_OUT_REASON = "run did not finish within the task timeout; cancelled"
CHECK_NAME = "check.py"
TASK_TEXT_NAME = "task.md"
TEMP_ROOT = "/tmp"
PROVIDER = "local"
ROLE = "worker"
ACTIVE_STATUSES = ("queued", "running")
MAX_POLLS = 720
POLL_SECONDS = 5.0
HTTP_TIMEOUT_SECONDS = 30.0
CHECK_TIMEOUT_SECONDS = 120.0
SECONDS_PER_MINUTE = 60.0
BASE_URL_ENV = "RUNNER_BASE_URL"
TOKEN_ENV = "CLAUDE_RUNNER_API_KEY"


class SuiteTask(TypedDict):
    id: str
    dir: str
    timeout_minutes: float


@dataclass(frozen=True)
class TaskResult:
    task_id: str
    passed: bool
    run_status: str
    steps: int
    minutes: float
    tokens: int
    reason: str


class PollTimeoutError(Exception):
    pass


def load_suite(suite_file: Path = SUITE_FILE) -> list[SuiteTask]:
    suite: dict[str, Any] = json.loads(suite_file.read_text(encoding="utf-8"))
    tasks: list[SuiteTask] = suite["tasks"]
    return tasks


def copy_workspace(task_dir: Path) -> Path:
    # check.py and task.md stay out of the workspace the Worker sees
    workdir = Path(tempfile.mkdtemp(prefix=f"suite-{task_dir.name}-", dir=TEMP_ROOT))
    shutil.copytree(
        task_dir,
        workdir,
        ignore=shutil.ignore_patterns(CHECK_NAME, TASK_TEXT_NAME),
        dirs_exist_ok=True,
    )
    return workdir


def start_run(client: httpx.Client, task_text: str, workdir: Path) -> dict[str, Any]:
    response = client.post(
        "/v1/runs",
        json={
            "input": task_text,
            "provider": PROVIDER,
            "role": ROLE,
            "working_directory": str(workdir),
        },
    )
    response.raise_for_status()
    run: dict[str, Any] = response.json()
    return run


def get_run(client: httpx.Client, run_id: str) -> dict[str, Any]:
    response = client.get(f"/v1/runs/{run_id}")
    response.raise_for_status()
    run: dict[str, Any] = response.json()
    return run


def cancel_run(client: httpx.Client, run_id: str) -> dict[str, Any]:
    response = client.post(f"/v1/runs/{run_id}/cancel")
    response.raise_for_status()
    run: dict[str, Any] = response.json()
    return run


def poll_run(
    client: httpx.Client,
    run_id: str,
    *,
    max_polls: int,
    poll_seconds: float,
    timeout_seconds: float,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    for _ in range(max_polls):
        run = get_run(client, run_id)
        if run.get("status") not in ACTIVE_STATUSES:
            return run
        if time.monotonic() >= deadline:
            break
        time.sleep(poll_seconds)
    raise PollTimeoutError(f"run {run_id} still active after {timeout_seconds} s")


def run_check(task_dir: Path, workdir: Path) -> tuple[bool, str]:
    shutil.copy2(task_dir / CHECK_NAME, workdir / CHECK_NAME)
    try:
        completed = subprocess.run(
            [sys.executable, str(workdir / CHECK_NAME)],
            cwd=workdir,
            capture_output=True,
            text=True,
            timeout=CHECK_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return False, f"check timed out after {CHECK_TIMEOUT_SECONDS} s"
    return completed.returncode == 0, (completed.stdout + completed.stderr).strip()


def minutes_between(started_at: str | None, finished_at: str | None) -> float:
    if not started_at or not finished_at:
        return 0.0
    elapsed = datetime.fromisoformat(finished_at) - datetime.fromisoformat(started_at)
    return elapsed.total_seconds() / SECONDS_PER_MINUTE


def run_task(
    client: httpx.Client,
    task: SuiteTask,
    suite_dir: Path,
    *,
    max_polls: int,
    poll_seconds: float,
) -> TaskResult:
    task_dir = suite_dir / task["dir"]
    task_text = (task_dir / TASK_TEXT_NAME).read_text(encoding="utf-8")
    workdir = copy_workspace(task_dir)
    run = start_run(client, task_text, workdir)
    run_id = str(run["run_id"])
    try:
        final = poll_run(
            client,
            run_id,
            max_polls=max_polls,
            poll_seconds=poll_seconds,
            timeout_seconds=task["timeout_minutes"] * SECONDS_PER_MINUTE,
        )
    except PollTimeoutError:
        final = cancel_run(client, run_id)
        passed, reason = False, TIMED_OUT_REASON
    else:
        passed, reason = run_check(task_dir, workdir)
    return TaskResult(
        task_id=task["id"],
        passed=passed,
        run_status=str(final.get("status", "")),
        steps=int(final.get("num_turns") or 0),
        minutes=minutes_between(final.get("started_at"), final.get("finished_at")),
        tokens=int(final.get("context_tokens") or 0),
        reason=reason,
    )


def report_row(result: TaskResult) -> dict[str, Any]:
    return {
        "id": result.task_id,
        "passed": result.passed,
        "steps": result.steps,
        "minutes": result.minutes,
        "tokens": result.tokens,
        "reason": redact(result.reason),
    }


def latest_results(output_dir: Path) -> Path | None:
    files = sorted(output_dir.glob(RESULTS_GLOB), key=lambda path: path.stat().st_mtime)
    return files[-1] if files else None


def load_previous(results_file: Path) -> dict[str, bool]:
    data: dict[str, Any] = json.loads(results_file.read_text(encoding="utf-8"))
    try:
        return {str(row["id"]): bool(row["passed"]) for row in data["tasks"]}
    except (KeyError, TypeError) as error:
        raise ValueError(f"{results_file} is not a results file") from error


def find_regressions(rows: list[dict[str, Any]], previous: dict[str, bool]) -> list[str]:
    return [row["id"] for row in rows if previous.get(row["id"]) and not row["passed"]]


def render_table(rows: list[dict[str, Any]], regressions: list[str]) -> str:
    id_width = max((len(row["id"]) for row in rows), default=len("id"))
    lines = [f"{'id':<{id_width}}  passed  steps  minutes  tokens  reason"]
    for row in rows:
        reason = " ".join(row["reason"].split())[:REASON_COLUMN_CHARS]
        lines.append(
            f"{row['id']:<{id_width}}  {row['passed']!s:<6}  {row['steps']:>5}"
            f"  {row['minutes']:>7.2f}  {row['tokens']:>6}  {reason}"
        )
    lines.append(f"regressions: {', '.join(regressions) if regressions else 'none'}")
    return "\n".join(lines) + "\n"


def write_report(output_dir: Path, rows: list[dict[str, Any]], regressions: list[str]) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    json_path = output_dir / f"results-{stamp}.json"
    payload = {"tasks": rows, "regressions": regressions}
    json_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    (output_dir / f"results-{stamp}.txt").write_text(
        render_table(rows, regressions), encoding="utf-8"
    )
    return json_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=os.environ.get(BASE_URL_ENV, ""))
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument(
        "--previous",
        type=Path,
        help="results JSON to compare with; default: the latest one in --output-dir",
    )
    args = parser.parse_args(argv)
    if not args.base_url:
        parser.error(f"pass --base-url or set {BASE_URL_ENV}")
    headers = (
        {"Authorization": f"Bearer {os.environ[TOKEN_ENV]}"}
        if os.environ.get(TOKEN_ENV)
        else {}
    )
    suite_dir = SUITE_FILE.parent
    rows: list[dict[str, Any]] = []
    with httpx.Client(
        base_url=args.base_url, headers=headers, timeout=HTTP_TIMEOUT_SECONDS
    ) as client:
        for task in load_suite():
            rows.append(
                report_row(
                    run_task(
                        client,
                        task,
                        suite_dir,
                        max_polls=MAX_POLLS,
                        poll_seconds=POLL_SECONDS,
                    )
                )
            )
            print(json.dumps(rows[-1]))
    previous_file = args.previous or latest_results(args.output_dir)
    previous = load_previous(previous_file) if previous_file else {}
    regressions = find_regressions(rows, previous)
    json_path = write_report(args.output_dir, rows, regressions)
    print(render_table(rows, regressions), end="")
    print(f"wrote {json_path}")
    return 1 if regressions else 0


if __name__ == "__main__":
    sys.exit(main())
