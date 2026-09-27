"""Profile-specific query construction from AHAWR task and review fields.

Worker queries describe *what must be done*; Reviewer queries describe *what must be verified*.
Each query is emitted in three forms: a term-rich lexical query, a focused natural-language vector
query and a short reranker query (the cross-encoder scores only ``query + chunk`` text).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .fingerprint import ComponentFingerprint, QueryFingerprint
from .models import RetrieveRequest
from .text import extract_identifiers, extract_paths

RERANK_QUERY_CHARS = 1500
VECTOR_QUERY_CHARS = 2000


@dataclass
class BuiltQuery:
    lexical_text: str
    vector_text: str
    rerank_text: str
    identifiers: list[str]
    paths: list[str]
    scope: list[str]
    components: dict[str, str] = field(default_factory=dict)
    fingerprint: QueryFingerprint = field(default_factory=lambda: QueryFingerprint({}))


def _join(*parts: str, limit: int | None = None) -> str:
    text = "\n".join(p.strip() for p in parts if p and p.strip())
    return text[:limit] if limit else text


def _numbered(items: list[str]) -> str:
    return "\n".join(f"- {item}" for item in items)


def build_query(request: RetrieveRequest) -> BuiltQuery:
    task = request.task
    review = request.review
    feedback = review.feedback if review else ""
    task_text = _join(
        task.title,
        task.objective,
        task.instructions,
        _numbered(task.acceptance_criteria),
        _numbered(task.scope),
    )
    components: dict[str, str] = {}
    extra_anchors: dict[str, list[str]] = {}
    if request.query:
        components["query"] = request.query
        lexical = _join(request.query, task_text)
        vector = _join(request.query, limit=VECTOR_QUERY_CHARS)
        rerank = _join(request.query, limit=RERANK_QUERY_CHARS)
    elif request.profile == "reviewer":
        evidence_paths = list(
            dict.fromkeys(
                [
                    *(review.changed_files if review else []),
                    *(extract_paths(review.worker_output) if review else []),
                ]
            )
        )
        evidence_idents = extract_identifiers(review.worker_output) if review else []
        verify_text = _join(
            task.title,
            task.objective,
            _numbered(task.acceptance_criteria),
            _numbered(task.verification),
        )
        components["task"] = verify_text
        components["evidence"] = " ".join([*evidence_paths, *evidence_idents])
        extra_anchors["evidence"] = evidence_paths
        lexical = _join(verify_text, _numbered(task.scope), components["evidence"], feedback)
        vector = _join(
            task.title,
            task.objective,
            _numbered(task.acceptance_criteria),
            feedback,
            limit=VECTOR_QUERY_CHARS,
        )
        rerank = _join(
            f"Verify: {task.title}",
            task.objective,
            "Acceptance criteria: " + "; ".join(task.acceptance_criteria)
            if task.acceptance_criteria
            else "",
            limit=RERANK_QUERY_CHARS,
        )
    else:
        components["task"] = task_text
        lexical = _join(task_text, _numbered(task.verification), feedback)
        vector = _join(
            task.title, task.objective, task.instructions, feedback, limit=VECTOR_QUERY_CHARS
        )
        rerank = _join(task.title, task.objective, feedback[:500], limit=RERANK_QUERY_CHARS)
    if feedback:
        components["feedback"] = feedback
    if not rerank.strip():
        rerank = lexical[:RERANK_QUERY_CHARS]
    if not vector.strip():
        vector = lexical[:VECTOR_QUERY_CHARS]
    anchor_source = _join(lexical, *(components.values()))
    identifiers = extract_identifiers(anchor_source)
    paths = list(
        dict.fromkeys(
            [*extract_paths(anchor_source), *task.scope, *(review.changed_files if review else [])]
        )
    )
    fingerprint = QueryFingerprint(
        {
            name: ComponentFingerprint.of(text, extra_anchors.get(name))
            for name, text in components.items()
        }
    )
    return BuiltQuery(
        lexical_text=lexical,
        vector_text=vector,
        rerank_text=rerank,
        identifiers=identifiers,
        paths=[p for p in paths if p],
        scope=task.scope,
        components=components,
        fingerprint=fingerprint,
    )
