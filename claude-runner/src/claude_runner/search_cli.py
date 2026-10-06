"""``ahawr-search``: on-demand retrieval of a query against the mission's corpus.

POSTs ``/retrieve`` to the ahawr-retrieval service with the worker profile, a
corpus named after the working directory and a ``corpus_roots`` entry for that
directory, then prints the top fragments as ``path:start-end`` plus the fragment
text.  The command is fail-open: on any HTTP error or timeout it prints one
short line and exits 0, so a Worker never blocks on a retrieval outage.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
from typing import Any

import httpx

DEFAULT_URL = "http://ahawr-retrieval:8500"
# A search takes 0.2-0.5 s, but the first search of a folder indexes it inside the request:
# measured 8-9 s for 30 small files on an idle service, so 10 s timed out for every new folder
# and each retry hit the same wall. A refused connection still fails at once.
DEFAULT_TIMEOUT = 30.0
DEFAULT_BUDGET = 1500
# The service rejects a budget outside this range with HTTP 422 (models.py: ge=100, le=100000).
MIN_BUDGET = 100
MAX_BUDGET = 100_000

# Roots the retrieval service is allowed to read.
ALLOWED_PREFIXES = ("/d/", "/workspace")


def resolve_root(root: str) -> tuple[str, str | None]:
    """Resolve *root* via ``realpath`` and check it is inside an allowed prefix.

    Returns ``(resolved, None)`` when usable, ``(resolved, reason)`` when not.
    """
    resolved = os.path.realpath(root)
    for prefix in ALLOWED_PREFIXES:
        if resolved == prefix.rstrip("/") or resolved.startswith(prefix):
            return resolved, None
    return resolved, f"folder {resolved} is unavailable to the retrieval service"


def corpus_slug(path: str) -> str:
    """Slug a directory path into a corpus id the retrieval service accepts.

    ``/d/OpenAIProjects/job-searching-assistant`` becomes
    ``d-openaiprojects-job-searching-assistant``: lowercased, non-alphanumeric
    characters replaced by ``-``, trimmed to the id pattern.
    """
    s = re.sub(r"[^A-Za-z0-9]+", "-", path).lower()
    s = re.sub(r"^-+", "", s)
    s = re.sub(r"-+$", "", s)
    if s:
        s = s[0].lower() + s[1:64]
    return s


async def fetch_detailed(
    url: str,
    query: str,
    budget: int,
    corpora: list[str],
    corpus_roots: dict[str, str],
    timeout: float,
    transport: httpx.AsyncBaseTransport | None = None,
) -> tuple[dict[str, Any] | None, str]:
    """POST to ``/retrieve``; returns ``(payload, "")`` or ``(None, reason)`` on any failure.

    The reason tells a Worker what to do next: a request the service rejected (fix the
    arguments) is not the same as a timeout (a new folder may still be indexing) or an outage.
    """
    try:
        async with httpx.AsyncClient(timeout=timeout, transport=transport) as client:
            response = await client.post(
                url + "/retrieve",
                json={
                    "query": query,
                    "profile": "worker",
                    "budget": {"max_tokens": budget},
                    "corpora": corpora,
                    "corpus_roots": corpus_roots,
                },
            )
            if 400 <= response.status_code < 500:
                detail = " ".join(response.text.split())[:200]
                return (
                    None,
                    f"the service rejected the request (HTTP {response.status_code}): {detail}",
                )
            if response.status_code >= 500:
                return None, f"the service failed (HTTP {response.status_code})"
            data = response.json()
            if not isinstance(data, dict):
                return None, "the service returned an unexpected answer"
            return data, ""
    except httpx.TimeoutException:
        return None, (
            f"no answer within {timeout:g} s; a folder searched for the first time is indexed "
            "on this call (the service keeps indexing after you stop waiting), so run the same "
            "command again in a minute or add --timeout 120"
        )
    except (httpx.HTTPError, OSError):
        return None, "the service cannot be reached"
    except ValueError:
        # A non-JSON 2xx response (e.g. an HTML error page) is treated as no result.
        return None, "the service returned an unexpected answer"


async def fetch(
    url: str,
    query: str,
    budget: int,
    corpora: list[str],
    corpus_roots: dict[str, str],
    timeout: float,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, Any] | None:
    """POST to ``/retrieve``; returns the parsed payload or ``None`` on any failure."""
    payload, _ = await fetch_detailed(url, query, budget, corpora, corpus_roots, timeout, transport)
    return payload


def format_chunks(payload: dict[str, Any], k: int) -> str:
    """Render the top *k* chunks as ``path:start-end`` + text."""
    lines: list[str] = []
    for chunk in payload.get("chunks", [])[:k]:
        path = chunk.get("path", "")
        start = chunk.get("start_line", 0)
        end = chunk.get("end_line", 0)
        content = chunk.get("content", "")
        lines.append(f"{path}:{start}-{end}\n{content}")
    return "\n".join(lines)


def main(
    argv: list[str] | None = None,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> int:
    parser = argparse.ArgumentParser(
        prog="ahawr-search",
        epilog=(
            'example: ahawr-search "where is the retry delay applied" --k 8 --budget 3000\n'
            'another folder: ahawr-search "state_write f_l" --root /d/rag-tmp/tree/src '
            "(a folder searched for the first time is indexed by that call)"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        # ``--max 20`` must not silently become ``--max-tokens 20``.
        allow_abbrev=False,
    )
    # Models guess the interface, so it is forgiving: the query may be several words without
    # quotes or ``--query``, and the usual spellings of --k and --budget are accepted.
    parser.add_argument("words", nargs="*", metavar="query", help="text to search for")
    parser.add_argument(
        "-q", "--query", dest="query_option", help="the query (instead of the words)"
    )
    parser.add_argument(
        "--k",
        "-k",
        "-n",
        "--limit",
        "--top",
        "--max",
        "--count",
        dest="k",
        type=int,
        default=5,
        help="number of fragments",
    )
    parser.add_argument(
        "--budget",
        "--max-tokens",
        dest="budget",
        type=int,
        default=DEFAULT_BUDGET,
        help=f"token budget ({MIN_BUDGET}-{MAX_BUDGET})",
    )
    parser.add_argument(
        "--root",
        default=None,
        help="folder to search (default: the current directory); a folder given as an "
        "extra argument is taken as the root too",
    )
    parser.add_argument(
        "--url",
        default=os.environ.get("AHAWR_RETRIEVAL_URL", DEFAULT_URL),
        help="retrieval service URL",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT,
        help="request timeout in seconds",
    )
    args, unknown = parser.parse_known_args(argv)
    if unknown:
        print(f"ahawr-search: ignoring unknown option(s): {' '.join(unknown)}", file=sys.stderr)
    words = list(args.words)
    root = args.root
    if root is None:
        # ``ahawr-search "query" /path/to/folder`` is the usual guess: take the last argument
        # that is an existing folder given as a path (not a bare word) as the root.
        for word in reversed(words):
            if word.startswith(("/", "./", "../")) and os.path.isdir(word):
                root = word
                words.remove(word)
                break
    root = root or "."
    args.query = (args.query_option or " ".join(words)).strip()
    if not args.query:
        parser.error("a query is required")
    budget = min(max(args.budget, MIN_BUDGET), MAX_BUDGET)
    if budget != args.budget:
        print(
            f"ahawr-search: --budget changed to {budget} (allowed {MIN_BUDGET}-{MAX_BUDGET})",
            file=sys.stderr,
        )

    resolved, unavailable = resolve_root(root)
    if unavailable:
        print(unavailable)
        return 0

    slug = corpus_slug(resolved)
    try:
        payload, reason = asyncio.run(
            fetch_detailed(
                args.url,
                args.query,
                budget,
                [slug],
                {slug: resolved},
                args.timeout,
                transport=transport,
            )
        )
    except (httpx.TimeoutException, httpx.HTTPError, OSError):
        payload, reason = None, "the service cannot be reached"
    if payload is None:
        print(f"retrieval unavailable: {reason}")
        return 0
    print(format_chunks(payload, args.k))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
