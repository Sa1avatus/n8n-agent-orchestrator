"""Usage metrics: retrieval logs joined with claude-runner run activity.

The pure functions here compute the metrics from in-memory inputs (log rows and
runner events). :func:`usage_report` orchestrates the real read path — pulling the
selected files per request/candidate out of the retrieval log and the files each run
opened out of the runner events — then maps runs to requests by their correlation
keys. Everything is read-only and derived offline; no retrieval state is mutated.
"""

from __future__ import annotations

import json
import re
import shlex
from typing import Any

import httpx

from .logstore import RetrievalLog

# File-opening commands recognised inside Bash commands (case-insensitive first word).
READ_COMMANDS = frozenset(
    {"cat", "sed", "head", "tail", "grep", "awk", "less", "more", "nl", "wc", "sort", "uniq"}
)
# Argument flags that are not file paths. A leading dash whose second character is one
# of these is treated as a flag (``-e``, ``-f``, …) rather than an operand.
_FLAG_LETTERS = frozenset("eFABiClvmotbsdkcpgEGLhqxzTaY")
# A token that is a redirection target, never a file.
_REDIRECT_RE = re.compile(r"^(?:[12]?>>?|&>>?)\S*$")
# A token that looks like a pure number or shell operator, never a file.
_NUMBER_OR_OP_RE = re.compile(r"^[-+]?(\d+|\d+\.\d+|&+|\|+|&&|\|\||;|&|\||\?)$")
# Flags that consume a following token as their value (the value is a pattern,
# script, or count, never a file). Everything else that looks like a flag is
# treated as a boolean flag whose following token is a normal operand.
_VALUE_FLAGS = frozenset(
    {
        "-e",
        "-f",
        "-A",
        "-B",
        "-C",
        "-m",
        "-L",
        "-t",
        "-y",
        "-T",
        "-b",
        "-d",
        "-k",
        "-z",
    }
)


def normalize_path(path: str, corpus_root: str) -> str:
    """``/corpus_root/app/parser.py`` -> ``app/parser.py``.

    Paths under ``corpus_root`` are made relative to it (``Path.relative_to`` style);
    anything else is returned unchanged so the metric still has something to compare.
    """
    path = path.strip()
    if not path:
        return ""
    if corpus_root:
        root = corpus_root.strip().rstrip("/")
        if path.startswith("/"):
            if path.startswith(root + "/"):
                return path[len(root) + 1 :].lstrip("/")
            # absolute but not under the root: strip the leading slash
            return path.lstrip("/")
        return path
    return path.lstrip("/")


def _timestamp(value: Any) -> float | None:
    if not value:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        pass
    try:
        from datetime import datetime

        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _is_flag(token: str) -> bool:
    """A shell flag: ``--`` or ``-X`` where X is a single letter (or ``--flag``)."""
    if token.startswith("--"):
        return True
    return len(token) > 1 and token[0] == "-" and token[1].isalpha()


def _is_number_or_op(token: str) -> bool:
    return bool(_NUMBER_OR_OP_RE.match(token))


def _path_arg(token: str) -> str | None:
    """A candidate file operand (quotes stripped), or ``None`` if the token is a
    flag, redirection target, number, or shell operator."""
    if _is_flag(token) or _REDIRECT_RE.match(token) or _is_number_or_op(token):
        return None
    return token.strip("'\"")


def _split_commands(command: str) -> list[str]:
    """Split a shell command into its sub-commands on ``&&``, ``||``, ``;``, ``|``.

    Each part is re-joined by spaces so flags and their values stay intact; quoted
    values (``"class X"``, ``'s/a/b/'``) are preserved as-is so that
    :func:`shlex.split` keeps them as a single token.
    """
    parts: list[str] = []
    buf = ""
    for token in command.split():
        if token in ("&&", "||", ";", "|"):
            parts.append(buf)
            buf = ""
        else:
            buf = (buf + " " + token) if buf else token
    parts.append(buf)
    return [p for p in parts if p.strip()]


def _collect_operands(command: str, opened: set[str]) -> None:
    """Add file operands from the file-opening sub-commands of ``command``.

    Only operands of the commands in :data:`READ_COMMANDS` count. Each sub-command is
    split on ``&&``/``||``/``;``/``|``; within one, the leading flag tokens (and any
    flag that takes a following value, e.g. ``grep -n "class X"``, ``sed -n '1,50p'``,
    ``sed -e 's/a/b/'``) are skipped — a flag's value is a pattern/script, not a file.
    Everything else that is not a flag, redirection, number, or operator is a file,
    e.g. ``cat pyproject.toml`` yields ``pyproject.toml``.
    """
    for part in _split_commands(command):
        try:
            tokens = shlex.split(part)
        except ValueError:
            tokens = part.split()
        if not tokens:
            continue
        cmd = tokens[0]
        if cmd.lower() not in READ_COMMANDS:
            continue
        # For grep the first non-flag operand is the search pattern, and for sed it is
        # the script (e.g. ``1,50p`` / ``s/a/b/``) — neither is a file.
        skip_first_operand = cmd.lower() in ("grep", "sed")
        i = 1
        while i < len(tokens):
            token = tokens[i]
            if _is_flag(token):
                # Flag: skip it and, if it takes a following value, skip that too.
                # A -e or -f flag consumes its value as the pattern/script, after
                # which the remaining operands are files (no more skipping).
                if token in _VALUE_FLAGS and i + 1 < len(tokens) and not _is_flag(tokens[i + 1]):
                    if token in ("-e", "-f"):
                        skip_first_operand = False
                    i += 2
                else:
                    i += 1
                continue
            if _REDIRECT_RE.match(token) or _is_number_or_op(token):
                i += 1
                continue
            if skip_first_operand:
                # The pattern/script; skip it and stop skipping.
                skip_first_operand = False
                i += 1
                continue
            path = _path_arg(token)
            if path:
                opened.add(path)
            i += 1


def opened_files_from_events(events: list[dict[str, Any]]) -> tuple[set[str], int]:
    """``(files_opened, ahawr_search_calls)`` from a run's event entries.

    Reads come from ``Read``/``Edit``/``Write`` tool inputs (``file_path``) and from
    file operands inside ``Bash`` commands (``cat``/``sed``/``head``/``tail``/``grep``/…).
    ``ahawr_search_calls`` counts Bash commands whose first word is ``ahawr-search``.
    """
    opened: set[str] = set()
    ahawr_search_calls = 0
    for entry in events:
        if entry.get("kind") != "tool_use":
            continue
        name = entry.get("name", "")
        input = entry.get("input") or {}
        if name in {"Read", "Edit", "Write"}:
            path = input.get("file_path")
            if path:
                opened.add(str(path).strip())
        elif name == "Bash":
            command = str(input.get("command") or "")
            if not command.strip():
                continue
            first = command.split(None, 1)[0]
            if first == "ahawr-search":
                ahawr_search_calls += 1
                continue
            _collect_operands(command, opened)
    return opened, ahawr_search_calls


def request_scope(request: dict[str, Any]) -> tuple[str, str]:
    """Correlation scope of a request row: ``(mission_id, task_id)``.

    Denormalized ``trace_mission_id``/``trace_task_id`` win; otherwise fall back to the
    ``trace_json`` ``state_namespace`` (mission) and ``task_id``.
    """
    mission = str(request.get("trace_mission_id") or "")
    task = str(request.get("trace_task_id") or "")
    trace = json.loads(request.get("trace_json") or "{}")
    if not mission:
        mission = str(trace.get("state_namespace") or trace.get("mission_id") or "")
    if not task:
        task = str(trace.get("task_id") or "")
    return (mission, task)


def selected_files(
    requests: list[dict[str, Any]], candidates: list[dict[str, Any]]
) -> dict[str, dict[str, int]]:
    """``{request_id: {relative_path: selected_token_count}}``.

    A candidate contributes when ``selected`` is true. Its ``path`` is relative to the
    corpus root (as stored in ``retrieval_candidates``), and its ``token_count``
    (0 if absent) is summed per request. ``corpora_json`` holds corpus IDs, not paths,
    so no root-based normalisation is applied here.
    """
    by_request: dict[str, dict[str, int]] = {}
    for candidate in candidates:
        if not candidate.get("selected"):
            continue
        request_id = str(candidate.get("request_id") or "")
        if not request_id:
            continue
        path = str(candidate.get("path") or "").strip()
        if not path:
            continue
        token_count = int(candidate.get("token_count") or 0)
        by_request.setdefault(request_id, {})
        by_request[request_id][path] = by_request[request_id].get(path, 0) + token_count
    return by_request


def _match_opened_to_selected(opened_abs: set[str], selected_rel: set[str]) -> set[str]:
    """Map opened (absolute) paths to selected relative paths.

    An opened path that equals a selected relative path, or that ends with
    ``/`` + that relative path, is normalised to that relative path (longest match
    wins). An opened path that matches no selected path is kept as-is (absolute), so
    it surfaces in ``missed_files``.
    """
    matched: set[str] = set()
    for path in opened_abs:
        best: str | None = None
        for rel in selected_rel:
            if (path == rel or path.endswith("/" + rel)) and (best is None or len(rel) > len(best)):
                best = rel
        matched.add(best if best is not None else path)
    return matched


def _match_run_to_request(
    run: dict[str, Any],
    requests: list[dict[str, Any]],
    request_key: dict[str, tuple[str, str]],
    request_ts: dict[str, float],
    profile_of_request: dict[str, str],
) -> str | None:
    """The request whose ``role`` equals the run's profile and whose
    ``(mission_id, task_id)`` matches, constrained to the window
    ``request.ts <= run.started_at < next same-key-same-profile request.ts``.

    Returns ``None`` when no request matches — unmatched runs are dropped (no fallback
    to the most recent request).
    """
    key = (
        str(run.get("mission_id") or run.get("correlation_mission_id") or ""),
        str(run.get("task_id") or run.get("correlation_task_id") or ""),
    )
    if not key[0] or not key[1]:
        return None
    role = str(run.get("role") or "")
    started = _timestamp(run.get("started_at"))
    if started is None:
        return None
    # Requests with this key and role, in ts order.
    matches = [
        rid for rid, rk in request_key.items() if rk == key and profile_of_request[rid] == role
    ]
    matches.sort(key=lambda rid: request_ts[rid])
    for i, rid in enumerate(matches):
        lower = request_ts[rid]
        upper = request_ts[matches[i + 1]] if i + 1 < len(matches) else float("inf")
        if lower <= started < upper:
            return rid
    return None


def compute_usage(
    requests: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    runs: list[dict[str, Any]],
    run_events: dict[str, list[dict[str, Any]]],
) -> dict[str, dict[str, Any]]:
    """Per-profile usage metrics from in-memory inputs.

    Each request is matched to the runs that share its ``role`` (``profile``) and
    ``(mission_id, task_id)`` and that started inside ``request.ts <= started_at <
    next same-key-same-profile request.ts``; unmatched runs are dropped. Per request,
    ``selected`` is its selected files (relative to the corpus root) and ``opened`` the
    files its matched runs opened; an opened absolute path is matched to a selected
    relative path when it ends with ``/`` + that path. Per profile the metrics are:
      * ``selected_files`` / ``opened_files`` — distinct-file counts
      * ``precision`` — Σ|selected ∩ opened| / Σ|selected| (0.0 if none selected)
      * ``recall`` — Σ|selected ∩ opened| / Σ|opened| (0.0 if none opened)
      * ``token_share`` — tokens of selected chunks whose file was opened / Σ context
      * ``missed_files`` — ``[{path, requests}, …]``: files opened but never selected
      * ``ahawr_search_calls`` — count of ``ahawr-search`` Bash commands
    """
    by_request = selected_files(requests, candidates)
    request_key: dict[str, tuple[str, str]] = {
        str(r["request_id"]): request_scope(r) for r in requests
    }
    request_ts: dict[str, float] = {
        str(r["request_id"]): float(r.get("ts") or 0.0) for r in requests
    }
    request_context: dict[str, int] = {
        str(r["request_id"]): int(r.get("context_tokens") or 0) for r in requests
    }
    profile_of_request: dict[str, str] = {
        str(r["request_id"]): str(r.get("profile") or "") for r in requests
    }

    # Per matched pair (request, its runs): per-request metrics.
    per_request: dict[str, dict[str, Any]] = {}
    for r in requests:
        rid = str(r["request_id"])
        selected = {path: tokens for path, tokens in by_request.get(rid, {}).items()}
        root_set = set(selected)

        # Matched runs: same role and key, inside the request's time window.
        matched: list[str] = []
        for run in runs:
            if (
                _match_run_to_request(run, requests, request_key, request_ts, profile_of_request)
                == rid
            ):
                matched.append(str(run["run_id"]))

        opened_raw: set[str] = set()
        searches = 0
        for run_id in matched:
            files, s = opened_files_from_events(run_events.get(run_id) or [])
            opened_raw.update(files)
            searches += s

        # Match opened absolute paths to selected relative paths (suffix match);
        # unmatched opened paths stay absolute so they surface in missed_files.
        opened = _match_opened_to_selected(opened_raw, root_set)

        selected_opened = root_set & opened
        n_selected = len(root_set)
        n_opened = len(opened)
        selected_tokens = sum(tokens for path, tokens in selected.items())
        opened_selected_tokens = sum(tokens for path, tokens in selected.items() if path in opened)
        per_request[rid] = {
            "selected_tokens": selected_tokens,
            "selected_opened_tokens": opened_selected_tokens,
            "n_selected": n_selected,
            "n_selected_opened": len(selected_opened),
            "n_opened": n_opened,
            "context_tokens": request_context.get(rid, 0),
            "searches": searches,
            "selected": root_set,
            "opened": opened,
        }

    # Aggregate per profile.
    profiles: dict[str, dict[str, Any]] = {}

    def entry(profile: str) -> dict[str, Any]:
        return profiles.setdefault(
            profile,
            {
                "selected_files": set(),
                "opened_files": set(),
                "n_selected_total": 0,
                "n_opened_total": 0,
                "n_selected_opened_total": 0,
                "selected_tokens_total": 0,
                "selected_opened_tokens_total": 0,
                "context_tokens_total": 0,
                "ahawr_search_calls": 0,
                "missed": {},
            },
        )

    for rid, req in per_request.items():
        profile = profile_of_request[rid]
        prof = entry(profile)
        prof["selected_files"].update(req["selected"])
        prof["opened_files"].update(req["opened"])
        prof["n_selected_total"] += req["n_selected"]
        prof["n_opened_total"] += req["n_opened"]
        prof["n_selected_opened_total"] += req["n_selected_opened"]
        prof["selected_tokens_total"] += req["selected_tokens"]
        prof["selected_opened_tokens_total"] += req["selected_opened_tokens"]
        prof["context_tokens_total"] += req["context_tokens"]
        prof["ahawr_search_calls"] += req["searches"]
        # Files opened but never selected for this request: count the requests.
        for path in req["opened"] - req["selected"]:
            prof["missed"][path] = prof["missed"].get(path, 0) + 1

    result: dict[str, dict[str, Any]] = {}
    for profile, prof in profiles.items():
        n_selected = prof["n_selected_total"]
        n_opened = prof["n_opened_total"]
        n_selected_opened = prof["n_selected_opened_total"]
        precision = n_selected_opened / n_selected if n_selected else 0.0
        recall = n_selected_opened / n_opened if n_opened else 0.0
        context = prof["context_tokens_total"]
        token_share = prof["selected_opened_tokens_total"] / context if context else 0.0
        missed = [
            {"path": path, "requests": count}
            for path, count in sorted(prof["missed"].items(), key=lambda kv: (-kv[1], kv[0]))
        ]
        result[profile] = {
            "selected_files": len(prof["selected_files"]),
            "opened_files": len(prof["opened_files"]),
            "precision": precision,
            "recall": recall,
            "token_share": token_share,
            "selected_tokens": prof["selected_tokens_total"],
            "context_tokens": context,
            "missed_files": missed,
            "ahawr_search_calls": prof["ahawr_search_calls"],
        }
    return result


async def usage_report(
    log: RetrievalLog,
    runner_url: str,
    since: float | None = None,
    profile: str | None = None,
) -> dict[str, Any]:
    """Read the retrieval log and the claude-runner API, then compute usage metrics.

    ``runner_url`` is the base URL (e.g. ``http://localhost:8899``); the runner API is
    called as ``GET {base}/v1/runs`` and ``GET {base}/v1/runs/{id}/events``. The log
    is closed before returning.
    """
    requests = log.requests(since)
    request_ids = {r["request_id"] for r in requests}
    candidates = [row for row in log.iter_feature_rows(since) if row["request_id"] in request_ids]
    log.close()

    async with httpx.AsyncClient() as client:
        runs: list[dict[str, Any]] = []
        response = await client.get(f"{runner_url}/v1/runs", params={"limit": "500"})
        runs = response.json().get("runs") or []
        run_events: dict[str, list[dict[str, Any]]] = {}
        for run in runs:
            run_id = run.get("run_id")
            if not run_id:
                continue
            body = (
                await client.get(f"{runner_url}/v1/runs/{run_id}/events", params={"after": "0"})
            ).json()
            run_events[str(run_id)] = body.get("events") or []

    report = compute_usage(requests, candidates, runs, run_events)
    if profile is not None:
        report = {profile: report[profile]} if profile in report else {}
    return report
