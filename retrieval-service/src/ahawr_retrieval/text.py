"""Deterministic text utilities shared by indexing, retrieval, fingerprinting and ranking."""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Iterable

WORD_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|[А-Яа-яЁё]+|\d+")
# Identifier-looking tokens in free text: snake_case, camelCase, PascalCase, dotted names,
# call syntax and backticked spans.
IDENT_RE = re.compile(
    r"`([^`\n]{2,80})`"
    r"|\b([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+)\b"
    r"|\b([A-Za-z_][A-Za-z0-9]*_[A-Za-z0-9_]+)\b"
    r"|\b([a-z]+[A-Z][A-Za-z0-9]*)\b"
    r"|\b([A-Z][a-z0-9]+[A-Z][A-Za-z0-9]*)\b"
    r"|\b([A-Za-z_][A-Za-z0-9_]*)\(\)"
)
PATH_RE = re.compile(
    r"(?<![\w/.-])((?:[\w.-]+/)*[\w.-]+\.(?:py|pyi|js|jsx|ts|tsx|mjs|cjs|go|rs|java|kt|cs|rb|php|"
    r"c|h|cc|cpp|hpp|swift|scala|sh|ps1|sql|md|markdown|rst|txt|adoc|json|ya?ml|toml|ini|cfg|csv|"
    r"html|css|scss|vue|svelte|dockerfile)|(?:[\w.-]+/)+[\w.-]+)(?![\w/])",
    re.IGNORECASE,
)
CAMEL_SPLIT_RE = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+\d*|[A-Z]+\d*|\d+|[А-Яа-яЁё]+")

STOPWORDS = frozenset(
    """
    a an and are as at be but by can could do does for from has have how if in into is it its
    must not of on or should so such that the their then there these this those to use used using
    was we were what when where which while will with would you your all any each only also than
    after before same other new make made need needs one two task tasks please return current
    и в во не что он на я с со как а то все она так его но да ты к у же вы за бы по только ее мне
    было вот от меня еще нет о из ему теперь когда даже ну вдруг ли если уже или ни быть был него
    до вас нибудь опять уж вам ведь там потом себя ничего ей может они тут где есть надо ней для
    мы тебя их чем была сам чтоб без будто чего раз тоже себе под будет ж тогда кто этот того
    потому этого какой совсем ним здесь этом один почти мой тем чтобы нее сейчас были куда зачем
    всех никогда можно при наконец два об другой хоть после над больше тот через эти нас про всего
    них какая много разве три эту моя впрочем хорошо свою этой перед иногда лучше чуть том нельзя
    такой им более всегда конечно всю между это должен должна должны нужно необходимо
    """.split()  # noqa: SIM905
)

_RU_SUFFIXES = (
    "ами",
    "ями",
    "ого",
    "его",
    "ому",
    "ему",
    "ыми",
    "ими",
    "ах",
    "ях",
    "ов",
    "ев",
    "ей",
    "ой",
    "ый",
    "ий",
    "ая",
    "яя",
    "ое",
    "ее",
    "ые",
    "ие",
    "ую",
    "юю",
    "ом",
    "ем",
    "ам",
    "ям",
    "ть",
    "а",
    "я",
    "о",
    "е",
    "ы",
    "и",
    "у",
    "ю",
    "ь",
)
_EN_SUFFIXES = ("ingly", "ing", "edly", "ed", "es", "ly", "s")


def sha256_hex(data: str | bytes) -> str:
    raw = data.encode("utf-8") if isinstance(data, str) else data
    return hashlib.sha256(raw).hexdigest()


def short_hash(*parts: str, length: int = 24) -> str:
    return sha256_hex("\x1f".join(parts))[:length]


def estimate_tokens(text: str) -> int:
    """Cheap, model-agnostic token estimate (~4 characters per token)."""
    return max(1, math.ceil(len(text) / 4)) if text else 0


def normalize_whitespace(text: str) -> str:
    return " ".join(text.split())


def split_identifier(token: str) -> list[str]:
    """Split ``parseHTTPResponse_v2`` into ``['parse', 'http', 'response', 'v2']``."""
    parts: list[str] = []
    for piece in re.split(r"[_\-.\s/]+", token):
        if not piece:
            continue
        parts.extend(p.lower() for p in CAMEL_SPLIT_RE.findall(piece))
    return [p for p in parts if p]


def words(text: str) -> list[str]:
    return WORD_RE.findall(text)


def light_stem(term: str) -> str:
    """Language-light suffix stripping used only for fingerprints (never for indexing)."""
    if len(term) < 5:
        return term
    suffixes = _RU_SUFFIXES if re.match(r"[а-яё]", term) else _EN_SUFFIXES
    for suffix in suffixes:
        if term.endswith(suffix) and len(term) - len(suffix) >= 4:
            return term[: -len(suffix)]
    return term


def content_terms(text: str, *, stem: bool = False, expand: bool = True) -> list[str]:
    """Lower-cased informative terms (plus identifier sub-tokens when ``expand``), unique."""
    seen: dict[str, None] = {}
    for raw in words(text):
        candidates = [raw.lower()]
        if expand and ("_" in raw or re.search(r"[a-z][A-Z]", raw)):
            candidates.extend(split_identifier(raw))
        for term in candidates:
            term = term.strip("_")
            if len(term) < 2 or term in STOPWORDS or term.isdigit():
                continue
            if stem:
                term = light_stem(term)
            seen.setdefault(term, None)
    return list(seen)


def extract_identifiers(text: str) -> list[str]:
    """Code-like identifiers mentioned in free text, original case, unique, order preserved."""
    seen: dict[str, None] = {}
    for match in IDENT_RE.finditer(text):
        value = next((g for g in match.groups() if g), "").strip()
        if not value or " " in value or "/" in value:
            continue
        value = value.rstrip("()")
        if len(value) >= 3:
            seen.setdefault(value, None)
    return list(seen)


def extract_paths(text: str) -> list[str]:
    seen: dict[str, None] = {}
    for match in PATH_RE.finditer(text):
        value = match.group(1).strip("./") if match.group(1).startswith("./") else match.group(1)
        value = value.rstrip(".,;:")
        if len(value) >= 3 and not value.startswith(("http", "www.")) and "://" not in value:
            seen.setdefault(value, None)
    return list(seen)


def expand_for_index(text: str) -> str:
    """Append identifier sub-tokens so FTS can match ``snake`` inside ``snake_case``."""
    extra: dict[str, None] = {}
    for raw in words(text):
        if "_" in raw or re.search(r"[a-z][A-Z]", raw):
            for part in split_identifier(raw):
                if len(part) >= 2:
                    extra.setdefault(part, None)
    return text if not extra else f"{text}\n{' '.join(extra)}"


def simhash64(terms: Iterable[str]) -> int:
    weights = [0] * 64
    for term in terms:
        h = int(sha256_hex(term)[:16], 16)
        for bit in range(64):
            weights[bit] += 1 if (h >> bit) & 1 else -1
    value = 0
    for bit in range(64):
        if weights[bit] > 0:
            value |= 1 << bit
    return value


def hamming64(a: int, b: int) -> int:
    return (a ^ b).bit_count()


def jaccard(a: Iterable[str], b: Iterable[str]) -> float:
    sa, sb = set(a), set(b)
    if not sa and not sb:
        return 1.0
    return len(sa & sb) / len(sa | sb)
