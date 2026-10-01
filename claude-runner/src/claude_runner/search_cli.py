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
from typing import Any

import httpx

DEFAULT_URL = "http://ahawr-retrieval:8500"
DEFAULT_TIMEOUT = 10.0
DEFAULT_BUDGET = 1500

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
            if response.status_code >= 400:
                return None
            data = response.json()
            if not isinstance(data, dict):
                return None
            return data
    except (httpx.TimeoutException, httpx.HTTPError, OSError):
        return None
    except ValueError:
        # A non-JSON 2xx response (e.g. an HTML error page) is treated as no result.
        return None


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
    parser = argparse.ArgumentParser(prog="ahawr-search")
    parser.add_argument("query", help="text to search for")
    parser.add_argument("--k", type=int, default=5, help="number of fragments to print")
    parser.add_argument("--budget", type=int, default=DEFAULT_BUDGET, help="token budget")
    parser.add_argument("--root", default=".", help="working directory to search")
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
    args = parser.parse_args(argv)

    resolved, unavailable = resolve_root(args.root)
    if unavailable:
        print(unavailable)
        return 0

    slug = corpus_slug(resolved)
    try:
        payload = asyncio.run(
            fetch(
                args.url,
                args.query,
                args.budget,
                [slug],
                {slug: resolved},
                args.timeout,
                transport=transport,
            )
        )
    except (httpx.TimeoutException, httpx.HTTPError, OSError):
        payload = None
    if payload is None:
        print("retrieval unavailable")
        return 0
    print(format_chunks(payload, args.k))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
