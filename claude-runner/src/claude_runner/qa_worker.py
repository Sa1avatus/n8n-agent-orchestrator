"""Host-side QA worker client: claims one QA request, runs the QA and reports the verdicts.

A mission whose working folder has no changed UI file gets verdict ``skipped``; a missing
folder or a folder that is not a git repository gets verdict ``blocked``.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import logging
import os
import re
import shlex
import signal
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import FrameType
from typing import Any

from claude_runner.config import Settings, map_host_path
from claude_runner.guard_heartbeat import TIME_FORMAT
from claude_runner.qa_requests import QA_UI_FILE_PATTERN
from claude_runner.qa_runner import build_verdict
from claude_runner.qa_stack import QaStackError, stack_down, stack_from_id

logger = logging.getLogger("claude_runner.qa_worker")

RUNNER_URL_ENV = "CLAUDE_RUNNER_URL"
DEFAULT_RUNNER_URL = "http://claude-runner:8700"
BEARER_ENV = "CLAUDE_RUNNER_API_KEY"
HTTP_TIMEOUT_SECONDS = 15
POLL_SECONDS = 60
BACKOFF_MAX_SECONDS = 900
MAX_CONSECUTIVE_ERRORS = 10
MAX_POLL_CYCLES = 100_000
QA_RUN_TIMEOUT_MINUTES = 20
RUN_TIMEOUT_SECONDS = QA_RUN_TIMEOUT_MINUTES * 60
REGRESSION_TIMEOUT_MINUTES = 25
API_TIMEOUT_MINUTES = 5
UI_TIMEOUT_MINUTES = 20
LAYER_TIMEOUT_SECONDS = {
    "regression": REGRESSION_TIMEOUT_MINUTES * 60,
    "api": API_TIMEOUT_MINUTES * 60,
    "ui": UI_TIMEOUT_MINUTES * 60,
}
SMOKE_MODULE = "claude_runner.qa_smoke"
KILL_TIMEOUT_SECONDS = 30
HEARTBEAT_HOST_PATH_ENV = "QA_WORKER_HEARTBEAT_PATH"
DEFAULT_HEARTBEAT_HOST_PATH = r"D:\n8n\data-tables\ops\heartbeat\qa_worker.json"
GIT_TIMEOUT_SECONDS = 30
GIT_DIFF_ARGS = ("git", "diff", "--name-only", "HEAD")
GIT_UNTRACKED_ARGS = ("git", "ls-files", "--others", "--exclude-standard")
MISSING_FOLDER_REASON = "working directory does not exist"
GIT_FAILED_REASON = (
    "git could not list the changed files: not a git repository or no commit yet"
)
QA_SCENARIO = "qa"
UI_FILE_RE = re.compile(QA_UI_FILE_PATTERN)
STACK_ID_RE = re.compile(r"stack id: ([0-9a-f]{8})")


class ChangeDetectionError(Exception):
    pass


class RunnerError(Exception):
    pass


def _runner_url() -> str:
    return os.environ.get(RUNNER_URL_ENV, DEFAULT_RUNNER_URL)


def _headers() -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    bearer = os.environ.get(BEARER_ENV, "")
    # an empty CLAUDE_RUNNER_API_KEY means the runner has no bearer check: send none
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"
    return headers


def _call(method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        _runner_url() + path,
        data=data,
        method=method,
        headers=_headers(),
    )
    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
            return json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError) as exc:
        raise RunnerError(f"{method} {path} failed: {exc}") from exc


def list_queued() -> list[dict[str, Any]]:
    queued: list[dict[str, Any]] = _call("GET", "/v1/qa/requests?status=queued")
    return queued


def claim(request_id: str) -> None:
    _call("POST", f"/v1/qa/requests/{request_id}/claim")


def _git_lines(args: tuple[str, ...], working_directory: str) -> list[str]:
    try:
        result = subprocess.run(
            args,
            cwd=working_directory,
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ChangeDetectionError(GIT_FAILED_REASON) from exc
    if result.returncode != 0:
        raise ChangeDetectionError(GIT_FAILED_REASON)
    return [line for line in result.stdout.splitlines() if line]


def changed_files(working_directory: str) -> list[str]:
    if not Path(working_directory).is_dir():
        raise ChangeDetectionError(MISSING_FOLDER_REASON)
    tracked = _git_lines(GIT_DIFF_ARGS, working_directory)
    untracked = _git_lines(GIT_UNTRACKED_ARGS, working_directory)
    return sorted(set(tracked + untracked))


def has_ui_change(files: list[str]) -> bool:
    return any(UI_FILE_RE.search(name) for name in files)


def blocked_verdict(scenario: str, reason: str) -> dict[str, Any]:
    return build_verdict(scenario=scenario, blocked_reason=reason)


def skipped_verdict(scenario: str) -> dict[str, Any]:
    return {**build_verdict(scenario=scenario), "verdict": "skipped"}


def container_path(host_path: str) -> str:
    return map_host_path(host_path, Settings.from_env().path_map)


def early_verdict(scenario: str, working_directory: str) -> dict[str, Any] | None:
    try:
        files = changed_files(container_path(working_directory))
    except ChangeDetectionError as exc:
        return blocked_verdict(scenario, str(exc))
    return None if has_ui_change(files) else skipped_verdict(scenario)


def _heartbeat_path() -> Path:
    return Path(
        container_path(os.environ.get(HEARTBEAT_HOST_PATH_ENV, DEFAULT_HEARTBEAT_HOST_PATH))
    )


def _write_heartbeat(last_action: str) -> None:
    path = _heartbeat_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "name": "qa_worker",
        "time": datetime.now(UTC).strftime(TIME_FORMAT),
        "last_action": last_action,
    }
    pending_path = path.with_suffix(".json.part")
    pending_path.write_text(json.dumps(payload), encoding="utf-8")
    os.replace(pending_path, path)


def _kill_tree(process: subprocess.Popen[str]) -> None:
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/T", "/F", "/PID", str(process.pid)],
            capture_output=True,
            timeout=KILL_TIMEOUT_SECONDS,
            check=False,
        )
        return
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        # the process group is already empty: nothing is left to kill
        return


@dataclass(frozen=True)
class CommandOutcome:
    output: str
    exit_code: int | None
    timed_out: bool
    missing_command: bool
    log_path: Path | None


def run_command(
    command: str, working_directory: str, *, timeout_seconds: float, runs_dir: Path
) -> CommandOutcome:
    try:
        process = subprocess.Popen(
            shlex.split(command),
            cwd=working_directory,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=os.name != "nt",
        )
    except FileNotFoundError:
        return CommandOutcome(
            output="", exit_code=None, timed_out=False, missing_command=True, log_path=None
        )
    timed_out = False
    try:
        output, _ = process.communicate(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        timed_out = True
        _kill_tree(process)
        output, _ = process.communicate()
    runs_dir.mkdir(parents=True, exist_ok=True)
    log_descriptor, log_name = tempfile.mkstemp(dir=runs_dir, prefix="regression-", suffix=".log")
    with os.fdopen(log_descriptor, "w", encoding="utf-8") as log:
        log.write(output)
    return CommandOutcome(
        output=output,
        exit_code=process.returncode,
        timed_out=timed_out,
        missing_command=False,
        log_path=Path(log_name),
    )


def _verdicts_from_output(output: str) -> tuple[list[str], str]:
    try:
        parsed = json.loads(output)
    except ValueError:
        return [], "run printed no JSON verdicts"
    if not isinstance(parsed, list):
        return [], "run printed no JSON verdicts"
    return [str(item.get("verdict")) for item in parsed if isinstance(item, dict)], ""


def _verdict_dicts_from_output(output: str) -> list[dict[str, Any]]:
    # qa regression prints one verdict dict, qa run and qa api print a list of them
    try:
        parsed = json.loads(output)
    except ValueError:
        return []
    if isinstance(parsed, dict):
        return [parsed]
    if not isinstance(parsed, list):
        return []
    return [item for item in parsed if isinstance(item, dict)]


def _stack_id_from_stderr(stderr: str) -> str | None:
    match = None
    for line in stderr.splitlines():
        found = STACK_ID_RE.search(line)
        if found:
            match = found
    return match.group(1) if match else None


def _tear_down(stack_id: str) -> None:
    try:
        stack_down(stack_from_id(stack_id))
    except (QaStackError, ValueError) as error:
        logger.error("qa stack %s teardown incomplete: %s", stack_id, error)


def _tear_down_ui_layer(stack_id: str | None) -> None:
    if stack_id is None:
        logger.error("qa layer ui printed no stack id; the stack was not torn down")
    else:
        _tear_down(stack_id)


def _wait_for_run(process: subprocess.Popen[str]) -> tuple[str, str]:
    try:
        return process.communicate(timeout=RUN_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        logger.error("qa run exceeded %d minutes; process tree killed", QA_RUN_TIMEOUT_MINUTES)
        _kill_tree(process)
        return process.communicate()


def _layer_command(layer: str, args: list[str], working_directory: str = "") -> LayerRunner:
    environment = {**os.environ, "QA_RUN_TIMEOUT_MINUTES": str(QA_RUN_TIMEOUT_MINUTES)}

    def run(_timeout_seconds: float) -> list[dict[str, Any]]:
        if layer == "ui" and working_directory:
            early = early_verdict(QA_SCENARIO, working_directory)
            if early is not None:
                return [early]
        process = subprocess.Popen(
            [sys.executable, "-m", "claude_runner.qa", *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=environment,
            start_new_session=os.name != "nt",
        )
        stack_id: str | None = None
        try:
            output, stderr = _wait_for_run(process)
            if layer == "ui":
                stack_id = _stack_id_from_stderr(stderr)
        finally:
            # also reaps grandchildren that outlived a normal exit
            _kill_tree(process)
            # the ui run owns the stack its child started: torn down on success,
            # failure and timeout alike, as _execute does
            if layer == "ui":
                _tear_down_ui_layer(stack_id)
        logger.info("qa layer %s exited with code %s", layer, process.returncode)
        return _verdict_dicts_from_output(output)

    return run


def _layer_runners(request_row: dict[str, Any]) -> dict[str, LayerRunner]:
    working_directory = str(request_row.get("working_directory", ""))
    return {
        "ui": _layer_command("ui", ["run"], working_directory),
        "regression": _layer_command(
            "regression", ["regression", "--workdir", working_directory], working_directory
        ),
        "api": _layer_command("api", ["api"], working_directory),
    }


def _run_child() -> tuple[list[str], str | None, str]:
    environment = {**os.environ, "QA_RUN_TIMEOUT_MINUTES": str(QA_RUN_TIMEOUT_MINUTES)}
    process = subprocess.Popen(
        [sys.executable, "-m", "claude_runner.qa", "run"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=environment,
        start_new_session=os.name != "nt",
    )
    try:
        output, stderr = _wait_for_run(process)
    finally:
        # also reaps grandchildren that outlived a normal exit
        _kill_tree(process)
    logger.info("qa run exited with code %s", process.returncode)
    verdicts, error = _verdicts_from_output(output)
    return verdicts, _stack_id_from_stderr(stderr), error


LayerRunner = Callable[[float], list[dict[str, Any]]]


def _layer_verdict(layer: str, verdict: str, reason: str) -> dict[str, Any]:
    return {
        **build_verdict(scenario=layer, failed_reason=reason),
        "verdict": verdict,
        "layer": layer,
    }


def _bounded(runner: LayerRunner, timeout_seconds: float) -> list[dict[str, Any]]:
    pool = ThreadPoolExecutor(max_workers=1)
    try:
        return pool.submit(runner, timeout_seconds).result(timeout=timeout_seconds)
    finally:
        # a layer that overran is abandoned here; its own process bound ends it
        pool.shutdown(wait=False, cancel_futures=True)


def _run_layer(layer: str, runner: LayerRunner, deadline: float) -> list[dict[str, Any]]:
    remaining_seconds = deadline - time.monotonic()
    if remaining_seconds <= 0:
        return [_layer_verdict(layer, "timeout", "request outer bound reached")]
    timeout_seconds = min(LAYER_TIMEOUT_SECONDS[layer], remaining_seconds)
    try:
        verdicts = _bounded(runner, timeout_seconds)
    except TimeoutError:
        return [_layer_verdict(layer, "timeout", f"layer exceeded {timeout_seconds:.0f} seconds")]
    except Exception as exc:
        return [_layer_verdict(layer, "error", f"{type(exc).__name__}: {exc}")]
    return [{**verdict, "layer": layer} for verdict in verdicts]


def run_layers(layers: list[str], runners: Mapping[str, LayerRunner]) -> list[dict[str, Any]]:
    unknown = sorted(set(layers) - set(LAYER_TIMEOUT_SECONDS))
    if unknown:
        raise ValueError(f"unknown QA layer: {unknown}")
    deadline = time.monotonic() + RUN_TIMEOUT_SECONDS
    verdicts: list[dict[str, Any]] = []
    for layer in LAYER_TIMEOUT_SECONDS:
        if layer in layers:
            verdicts.extend(_run_layer(layer, runners[layer], deadline))
    return verdicts


def smoke_enabled(options: Mapping[str, Any]) -> bool:
    return options.get("smoke") is True and importlib.util.find_spec(SMOKE_MODULE) is not None


def _execute(request_row: dict[str, Any]) -> tuple[list[str], str]:
    working_directory = str(request_row.get("working_directory", ""))
    if working_directory:
        early = early_verdict(QA_SCENARIO, working_directory)
        if early is not None:
            return [str(early["verdict"])], ""
    stack_id: str | None = None
    try:
        verdicts, stack_id, error = _run_child()
    finally:
        # The stack is torn down on success, failure and timeout alike: the child's own
        # teardown only runs while the child is alive.
        if stack_id is None:
            logger.error("qa run printed no stack id; the stack was not torn down")
        else:
            _tear_down(stack_id)
    return verdicts, error


def _serve(request_row: dict[str, Any]) -> None:
    request_id = str(request_row["id"])
    claim(request_id)
    layers = request_row.get("layers")
    verdicts: list[Any]
    if layers:
        verdicts = run_layers(list(layers), _layer_runners(request_row))
        error = ""
    else:
        verdicts, error = _execute(request_row)
    _call("POST", f"/v1/qa/requests/{request_id}/result", {"verdicts": verdicts, "error": error})
    logger.info("qa request %s reported with %d verdict(s)", request_id, len(verdicts))
    _write_heartbeat(f"reported {request_id}")


def _lock_path() -> Path:
    return _heartbeat_path().with_suffix(".lock")


def _acquire_lock() -> bool:
    lock_path = _lock_path()
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return False
    os.close(descriptor)
    return True


def _serve_locked(request_row: dict[str, Any]) -> bool:
    if not _acquire_lock():
        logger.warning("another qa run holds the lock; skipping")
        return False
    try:
        _serve(request_row)
    finally:
        _lock_path().unlink(missing_ok=True)
    return True


def _serve_locked_safely(request_row: dict[str, Any]) -> bool:
    try:
        return _serve_locked(request_row)
    except RunnerError as exc:
        logger.error("qa request %s not reported: %s", request_row.get("id"), exc)
        return False


def _handle_queue(queued: list[dict[str, Any]]) -> int:
    if not queued:
        _write_heartbeat("idle")
        return POLL_SECONDS
    if _serve_locked_safely(queued[0]):
        return 0
    _write_heartbeat("claim not reported")
    return POLL_SECONDS


def _backoff_seconds(consecutive_errors: int) -> int:
    return min(POLL_SECONDS << (consecutive_errors - 1), BACKOFF_MAX_SECONDS)


def _stop_on_signal(stop: threading.Event) -> None:
    # the handler only sets the event: logging inside a signal handler can deadlock
    def request_stop(_signum: int, _frame: FrameType | None) -> None:
        stop.set()

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m claude_runner.qa_worker",
        description=(
            "Polls the runner for QA requests and runs them one at a time, "
            "until SIGINT or SIGTERM."
        ),
    )
    parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if not os.environ.get(BEARER_ENV):
        logger.warning("%s is empty: the runner is called without a bearer token", BEARER_ENV)
    stop = threading.Event()
    _stop_on_signal(stop)
    consecutive_errors = 0
    for _ in range(MAX_POLL_CYCLES):
        if stop.is_set():
            break
        try:
            queued = list_queued()
        except RunnerError as exc:
            consecutive_errors += 1
            logger.error("poll failed (%d in a row): %s", consecutive_errors, exc)
            if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                return 1
            delay = _backoff_seconds(consecutive_errors)
        else:
            consecutive_errors = 0
            delay = _handle_queue(queued)
        if stop.wait(delay):
            break
    else:
        logger.info("poll cycle bound reached; exiting")
    logger.info("qa worker exiting")
    return 0


if __name__ == "__main__":
    sys.exit(main())
