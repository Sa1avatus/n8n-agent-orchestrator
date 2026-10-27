"""Facts for the mission report: validated digest, task table, criteria, known unmet, remarks.

Pure functions except collect_repo_facts, the only reader of git state. A value the digest does not
carry is rendered as NO_EVIDENCE and never filled in. Status comes from the stored status only,
never from reason text.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from typing import Any, TypedDict

from claude_runner.provider_secrets import REDACTED_TEXT

NO_EVIDENCE = "no evidence"
ACCEPTED = "accepted"
MAX_REMARK_CHARS = 300
MAX_PACK_CHARS = 12000
PACK_HEADER = "FACTS (generated, authoritative, do not contradict, do not invent rows)"
PACK_END = "END OF FACTS"
CHECK_TOOLS = ("pytest", "ruff", "mypy")


@dataclass(frozen=True)
class ReportTask:
    task_id: str
    title: str | None
    status: str
    attempts: int | None
    last_score: float | None
    files_changed: list[str] | None
    last_review_text: str | None


@dataclass(frozen=True)
class ReportDigest:
    mission_id: str
    mission_name: str
    acceptance_criteria: list[str]
    tasks: list[ReportTask]
    validation_output: str
    operator_accepted_task_ids: list[str]


def _require_str(value: Any, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{where}: expected a non-empty string, got {type(value).__name__}")
    return value


def _optional_str(value: Any, where: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{where}: expected a string or null, got {type(value).__name__}")
    return value


def _optional_int(value: Any, where: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{where}: expected a non-negative integer or null")
    return int(value)


def _optional_number(value: Any, where: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{where}: expected a number or null")
    return float(value)


def _str_list(value: Any, where: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{where}: expected a list of strings")
    return list(value)


def _object_list(value: Any, where: str) -> list[Any]:
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise ValueError(f"{where}: expected a list of objects")
    return list(value)


def _parse_task(raw: dict[str, Any], where: str) -> ReportTask:
    files_changed = raw.get("files_changed")
    return ReportTask(
        task_id=_require_str(raw.get("id"), f"{where}.id"),
        title=_optional_str(raw.get("title"), f"{where}.title"),
        status=_require_str(raw.get("status"), f"{where}.status"),
        attempts=_optional_int(raw.get("attempts"), f"{where}.attempts"),
        last_score=_optional_number(raw.get("last_score"), f"{where}.last_score"),
        files_changed=None
        if files_changed is None
        else _str_list(files_changed, f"{where}.files_changed"),
        last_review_text=_optional_str(raw.get("last_review_text"), f"{where}.last_review_text"),
    )


def parse_digest(raw: Any) -> ReportDigest:
    if not isinstance(raw, dict):
        raise ValueError(f"digest: expected an object, got {type(raw).__name__}")
    tasks = _object_list(raw.get("tasks"), "tasks")
    events = _object_list(raw.get("operator_accept_events"), "operator_accept_events")
    return ReportDigest(
        mission_id=_require_str(raw.get("mission_id"), "mission_id"),
        mission_name=_require_str(raw.get("mission_name"), "mission_name"),
        acceptance_criteria=_str_list(raw.get("acceptance_criteria"), "acceptance_criteria"),
        tasks=[_parse_task(task, f"tasks[{index}]") for index, task in enumerate(tasks)],
        validation_output=_optional_str(raw.get("validation_output"), "validation_output") or "",
        operator_accepted_task_ids=[
            _require_str(event.get("task_id"), f"operator_accept_events[{index}].task_id")
            for index, event in enumerate(events)
        ],
    )


def effective_status(task: ReportTask, operator_accepted_task_ids: list[str]) -> str:
    if task.task_id in operator_accepted_task_ids:
        return ACCEPTED
    return task.status


def _or_no_evidence(value: str | None) -> str:
    return value if value else NO_EVIDENCE


def _escape_cell(cell: str) -> str:
    return cell.replace("\n", " ").replace("|", "\\|")


def _unaccepted_tasks(digest: ReportDigest) -> list[ReportTask]:
    return [
        task
        for task in digest.tasks
        if effective_status(task, digest.operator_accepted_task_ids) != ACCEPTED
    ]


def render_criteria(criteria: list[str]) -> str:
    if not criteria:
        return NO_EVIDENCE
    return "\n".join(f"{number}. {text}" for number, text in enumerate(criteria, start=1))


def _task_row(task: ReportTask, operator_accepted_task_ids: list[str]) -> str:
    cells = [
        task.task_id,
        _or_no_evidence(task.title),
        effective_status(task, operator_accepted_task_ids),
        NO_EVIDENCE if task.attempts is None else str(task.attempts),
        NO_EVIDENCE if task.last_score is None else f"{task.last_score:g}",
        _or_no_evidence(", ".join(task.files_changed or [])),
    ]
    return "| " + " | ".join(_escape_cell(cell) for cell in cells) + " |"


def render_task_table(digest: ReportDigest) -> str:
    if not digest.tasks:
        return NO_EVIDENCE
    lines = ["| id | title | status | attempts | score | files |", "|---|---|---|---|---|---|"]
    lines.extend(_task_row(task, digest.operator_accepted_task_ids) for task in digest.tasks)
    return "\n".join(lines)


def unresolved_remarks(digest: ReportDigest) -> list[tuple[str, str]]:
    return [
        (task.task_id, _or_no_evidence(task.last_review_text)[:MAX_REMARK_CHARS])
        for task in _unaccepted_tasks(digest)
    ]


def known_unmet(digest: ReportDigest) -> list[str]:
    return [
        f"{task.task_id} {_or_no_evidence(task.title)}: {task.status}"
        for task in _unaccepted_tasks(digest)
    ]


class RepoFacts(TypedDict):
    diff_stat_uncommitted: str | None
    diff_stat_committed: str | None
    untracked_files: list[str] | str | None


GIT_TIMEOUT_SECONDS = 30
GIT_UNCOMMITTED_ARGS = ("git", "diff", "--stat", "--no-color", "HEAD", "--")
GIT_UNTRACKED_ARGS = ("git", "ls-files", "--others", "--exclude-standard")


class _GitError(Exception):
    pass


def _run_git(args: tuple[str, ...], workdir: str) -> str:
    try:
        result = subprocess.run(
            args,
            cwd=workdir,
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        raise _GitError(f"git timed out after {GIT_TIMEOUT_SECONDS} seconds") from error
    except OSError as error:
        raise _GitError(f"could not run git: {error}") from error
    if result.returncode != 0:
        first_line = (result.stderr.strip().splitlines() or ["no stderr"])[0]
        raise _GitError(f"git exited with status {result.returncode}: {first_line}")
    return result.stdout


def _diff_stat(args: tuple[str, ...], workdir: str) -> str:
    try:
        return _run_git(args, workdir)
    except _GitError as error:
        return f"{NO_EVIDENCE}: {error}"


def _untracked_files(workdir: str) -> list[str] | str:
    try:
        output = _run_git(GIT_UNTRACKED_ARGS, workdir)
    except _GitError as error:
        return f"{NO_EVIDENCE}: {error}"
    return [line for line in output.splitlines() if line]


def collect_repo_facts(workdir: str, since_ref: str) -> RepoFacts:
    if since_ref.startswith("-"):
        committed = f"{NO_EVIDENCE}: since_ref must not start with '-'"
    else:
        committed = _diff_stat(
            ("git", "diff", "--stat", "--no-color", f"{since_ref}..HEAD", "--"), workdir
        )
    return RepoFacts(
        diff_stat_uncommitted=_diff_stat(GIT_UNCOMMITTED_ARGS, workdir),
        diff_stat_committed=committed,
        untracked_files=_untracked_files(workdir),
    )


_TOOL_WORD = re.compile(r"\b(pytest|ruff|mypy)\b")
_CHECK_LINE = re.compile(r"passed|failed|error|Found \d+ errors?|Success")


def extract_check_lines(text: str) -> list[str]:
    blocks: dict[str, list[str]] = {}
    current: str | None = None
    for line in text.splitlines():
        found = _TOOL_WORD.search(line)
        if found:
            current = found.group(1)
            blocks[current] = []
        if current is not None:
            blocks[current].append(line)
    lines: list[str] = []
    for tool in CHECK_TOOLS:
        matched = [line for line in blocks.get(tool, []) if _CHECK_LINE.search(line)]
        lines.extend(matched or [f"{tool}: {NO_EVIDENCE}"])
    return lines


_REDACTIONS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?i)(\bbearer\s+)[A-Za-z0-9._~+/=-]+"), rf"\1{REDACTED_TEXT}"),
    (re.compile(r"([A-Za-z][A-Za-z0-9+.-]*://[^\s:/@]*:)\S+@"), rf"\1{REDACTED_TEXT}@"),
    (re.compile(r"\b(?:sk-|ghp_|github_pat_|xox[abp]-)[A-Za-z0-9_-]{12,}"), REDACTED_TEXT),
    (
        re.compile(
            r"(?i)\b((?:password|passwd|secret|token|api[_-]?key|access[_-]?key)"
            r"[\"']?\s*[:=]\s*[\"']?)[^\s\"',;]+"
        ),
        rf"\1{REDACTED_TEXT}",
    ),
    (re.compile(r"(?<![A-Za-z0-9_])([A-Z][A-Z0-9_]*)=\S+"), rf"\1={REDACTED_TEXT}"),
)


def redact(text: str) -> str:
    for pattern, replacement in _REDACTIONS:
        text = pattern.sub(replacement, text)
    return text


_STAT_SEPARATOR = " | "


def _stat_lines(stat: str | None, names_only: bool) -> list[str]:
    if not stat or not stat.strip():
        return [NO_EVIDENCE]
    if not names_only or stat.startswith(NO_EVIDENCE):
        return stat.splitlines()
    return [
        line.split(_STAT_SEPARATOR)[0].strip()
        for line in stat.splitlines()
        if _STAT_SEPARATOR in line
    ]


def _files_section(repo_facts: RepoFacts, names_only: bool) -> list[str]:
    untracked = repo_facts["untracked_files"]
    if untracked is None:
        untracked_lines = [NO_EVIDENCE]
    elif isinstance(untracked, str):
        untracked_lines = [untracked]
    else:
        untracked_lines = untracked or ["none"]
    return [
        "uncommitted (git diff --stat):",
        *_stat_lines(repo_facts["diff_stat_uncommitted"], names_only),
        "committed in the mission window (git diff --stat):",
        *_stat_lines(repo_facts["diff_stat_committed"], names_only),
        "new untracked files:",
        *untracked_lines,
    ]


def _render_pack(
    digest: ReportDigest,
    repo_facts: RepoFacts,
    remarks: list[tuple[str, str]],
    names_only: bool,
) -> str:
    remark_lines = [f"{task_id}: {text}" for task_id, text in remarks] or ["none"]
    unmet_lines = known_unmet(digest) or ["none"]
    lines = [
        PACK_HEADER,
        "1. Mission",
        f"mission id: {digest.mission_id}",
        f"mission name: {digest.mission_name}",
        "2. Acceptance criteria",
        render_criteria(digest.acceptance_criteria),
        "3. Task table",
        render_task_table(digest),
        "4. Files changed",
        *_files_section(repo_facts, names_only),
        "5. Quoted check lines",
        *extract_check_lines(digest.validation_output),
        "6. Unresolved Reviewer remarks",
        *remark_lines,
        "7. Known unmet",
        *unmet_lines,
    ]
    return "\n".join(lines)


def _finish(body: str, removed_chars: int | None) -> str:
    tail = [] if removed_chars is None else [f"CUT: {removed_chars} characters removed"]
    return redact("\n".join([body, *tail, PACK_END]))


def build_pack(digest: ReportDigest, repo_facts: RepoFacts) -> str:
    remarks = unresolved_remarks(digest)
    full_body = _render_pack(digest, repo_facts, remarks, names_only=False)
    pack = _finish(full_body, None)
    if len(pack) <= MAX_PACK_CHARS:
        return pack
    stages = [(remarks[dropped:], False) for dropped in range(1, len(remarks) + 1)]
    stages.append(([], True))
    for kept_remarks, names_only in stages:
        body = _render_pack(digest, repo_facts, kept_remarks, names_only)
        pack = _finish(body, len(full_body) - len(body))
        if len(pack) <= MAX_PACK_CHARS:
            return pack
    raise ValueError(f"facts pack does not fit in {MAX_PACK_CHARS} characters after the cut rule")
