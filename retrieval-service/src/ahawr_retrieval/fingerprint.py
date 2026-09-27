"""Query fingerprints with semantic equivalence for retry reuse.

A retry query is built from structured components (task, reviewer feedback, worker evidence).
Each component is fingerprinted separately at three levels:

1. ``exact``      — hash of whitespace-normalized text;
2. ``normalized`` — hash of the sorted set of stemmed content terms (ignores punctuation,
                    formatting, word order, stop words and casing);
3. ``semantic``   — same *anchors* (identifiers and paths, exact) and term-set Jaccard similarity
                    above a threshold.

Two queries are equivalent only when *every* component is equivalent. Anchors must match exactly:
a new file or symbol mentioned by the Reviewer is a substantive reason for re-retrieval, while a
rephrased sentence is not. The fingerprint therefore never depends on a verbatim hash of the full
Reviewer feedback.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .text import (
    content_terms,
    extract_identifiers,
    extract_paths,
    jaccard,
    light_stem,
    normalize_whitespace,
    sha256_hex,
    simhash64,
)


@dataclass(frozen=True)
class ComponentFingerprint:
    exact: str
    normalized: str
    anchors: tuple[str, ...]
    terms: tuple[str, ...]
    simhash: int

    @classmethod
    def of(cls, text: str, extra_anchors: list[str] | None = None) -> ComponentFingerprint:
        # No identifier splitting: ``RetryPolicy`` and ``retrypolicy`` yield the same term.
        terms = tuple(sorted(set(content_terms(text, stem=True, expand=False))))
        anchors = {a.lower() for a in extract_identifiers(text)}
        anchors |= {p.lower() for p in extract_paths(text)}
        anchors |= {a.lower() for a in extra_anchors or []}
        return cls(
            exact=sha256_hex(normalize_whitespace(text))[:20],
            normalized=sha256_hex(" ".join(terms))[:20],
            anchors=tuple(sorted(anchors)),
            terms=terms,
            simhash=simhash64(terms),
        )

    def is_empty(self) -> bool:
        return not self.terms and not self.anchors


@dataclass(frozen=True)
class QueryFingerprint:
    components: dict[str, ComponentFingerprint]

    @property
    def exact_hash(self) -> str:
        parts = [
            f"{name}={c.exact}" for name, c in sorted(self.components.items()) if not c.is_empty()
        ]
        return sha256_hex("|".join(parts))[:24]

    @property
    def normalized_hash(self) -> str:
        parts = [
            f"{name}={c.normalized}:{','.join(c.anchors)}"
            for name, c in sorted(self.components.items())
            if not c.is_empty()
        ]
        return sha256_hex("|".join(parts))[:24]

    def to_dict(self) -> dict[str, Any]:
        return {
            "exact_hash": self.exact_hash,
            "normalized_hash": self.normalized_hash,
            "components": {
                name: {**asdict(c), "anchors": list(c.anchors), "terms": list(c.terms)}
                for name, c in self.components.items()
            },
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> QueryFingerprint:
        return cls(
            {
                name: ComponentFingerprint(
                    exact=c["exact"],
                    normalized=c["normalized"],
                    anchors=tuple(c["anchors"]),
                    terms=tuple(c["terms"]),
                    simhash=int(c["simhash"]),
                )
                for name, c in data.get("components", {}).items()
            }
        )


@dataclass(frozen=True)
class Equivalence:
    equivalent: bool
    level: str  # exact | normalized | semantic | different
    detail: dict[str, Any]


def compare(a: QueryFingerprint, b: QueryFingerprint, jaccard_threshold: float) -> Equivalence:
    if a.exact_hash == b.exact_hash:
        return Equivalence(True, "exact", {})
    if a.normalized_hash == b.normalized_hash:
        return Equivalence(True, "normalized", {})
    detail: dict[str, Any] = {}
    names = {n for n, c in a.components.items() if not c.is_empty()} | {
        n for n, c in b.components.items() if not c.is_empty()
    }
    for name in sorted(names):
        ca, cb = a.components.get(name), b.components.get(name)
        if ca is None or cb is None or ca.is_empty() or cb.is_empty():
            return Equivalence(False, "different", {"component": name, "reason": "presence"})
        if ca.normalized == cb.normalized and ca.anchors == cb.anchors:
            continue
        if not (_anchors_covered(ca, cb) and _anchors_covered(cb, ca)):
            return Equivalence(False, "different", {"component": name, "reason": "anchors"})
        similarity = jaccard(ca.terms, cb.terms)
        detail[name] = round(similarity, 3)
        if similarity < jaccard_threshold:
            return Equivalence(False, "different", {"component": name, "jaccard": similarity})
    return Equivalence(True, "semantic", detail)


def _anchors_covered(a: ComponentFingerprint, b: ComponentFingerprint) -> bool:
    """Every anchor of ``a`` is present in ``b``: as an anchor, or — for plain identifiers that
    lost their casing/backticks in a rephrase — as a term. Paths must match as anchors."""
    for anchor in a.anchors:
        if anchor in b.anchors:
            continue
        if "/" in anchor or "." in anchor or light_stem(anchor) not in b.terms:
            return False
    return True
