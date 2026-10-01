"""Deterministic, dependency-free chunkers for repository code and project documentation.

Chunk anchors are derived from structure (symbol qualified name, heading breadcrumb) rather than
line numbers so that an edit in one function does not change the identity of unrelated chunks.
Symbol detection is regex/indentation based on purpose: tree-sitter and static analysis are a
later roadmap phase (see docs/retrieval/ROADMAP.md, phase 3).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath

DOC_LANGUAGES = {
    ".md": "markdown",
    ".markdown": "markdown",
    ".mdx": "markdown",
    ".rst": "rst",
    ".txt": "text",
    ".adoc": "asciidoc",
}
# Fragments of .patch/.diff files longer than this many lines are demoted in deterministic
# ranking (ranking.py), unless the query names the file. Overridable via
# RETRIEVAL_LARGE_PATCH_LINES; the value is read once at import time.
LARGE_PATCH_LINES = int(os.environ.get("RETRIEVAL_LARGE_PATCH_LINES", "1000") or 1000)

CODE_LANGUAGES = {
    ".py": "python",
    ".pyi": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".vue": "javascript",
    ".svelte": "javascript",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".kt": "kotlin",
    ".kts": "kotlin",
    ".cs": "csharp",
    ".scala": "scala",
    ".swift": "swift",
    ".php": "php",
    ".rb": "ruby",
    ".c": "c",
    ".h": "c",
    ".cc": "cpp",
    ".cpp": "cpp",
    ".hpp": "cpp",
    ".hh": "cpp",
    ".sh": "shell",
    ".bash": "shell",
    ".zsh": "shell",
    ".ps1": "powershell",
    ".sql": "sql",
    ".json": "json",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".toml": "toml",
    ".ini": "ini",
    ".cfg": "ini",
    ".csv": "csv",
    ".html": "html",
    ".css": "css",
    ".scss": "css",
    ".xml": "xml",
    ".proto": "proto",
    ".graphql": "graphql",
    ".tf": "hcl",
    ".dockerfile": "dockerfile",
    ".mk": "make",
    ".cmake": "cmake",
    ".patch": "diff",
    ".diff": "diff",
}
SPECIAL_FILENAMES = {
    "dockerfile": "dockerfile",
    "containerfile": "dockerfile",
    "makefile": "make",
    "gnumakefile": "make",
    "cmakelists.txt": "cmake",
}
# Known binary formats, skipped without reading them.
BINARY_SUFFIXES = frozenset(
    {
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".bmp",
        ".ico",
        ".webp",
        ".tiff",
        ".psd",
        ".pdf",
        ".doc",
        ".docx",
        ".xls",
        ".xlsx",
        ".ppt",
        ".pptx",
        ".odt",
        ".zip",
        ".tar",
        ".gz",
        ".tgz",
        ".bz2",
        ".xz",
        ".7z",
        ".rar",
        ".zst",
        ".exe",
        ".dll",
        ".so",
        ".dylib",
        ".a",
        ".o",
        ".obj",
        ".lib",
        ".class",
        ".jar",
        ".pyc",
        ".wasm",
        ".bin",
        ".gguf",
        ".onnx",
        ".pt",
        ".pth",
        ".safetensors",
        ".ckpt",
        ".npy",
        ".npz",
        ".mp3",
        ".wav",
        ".flac",
        ".ogg",
        ".mp4",
        ".mkv",
        ".mov",
        ".avi",
        ".webm",
        ".ttf",
        ".otf",
        ".woff",
        ".woff2",
        ".eot",
        ".sqlite",
        ".sqlite3",
        ".db",
        ".parquet",
        ".pkl",
    }
)
# Variants named by a prefix, e.g. Dockerfile.llama, Dockerfile.dashboard, Makefile.cuda.
SPECIAL_PREFIXES = {
    "dockerfile.": "dockerfile",
    "containerfile.": "dockerfile",
    "makefile.": "make",
}

BRACE_LANGUAGES = {
    "javascript",
    "typescript",
    "go",
    "rust",
    "java",
    "kotlin",
    "csharp",
    "scala",
    "swift",
    "php",
    "c",
    "cpp",
    "shell",
}


@dataclass(frozen=True)
class ChunkDraft:
    anchor: str
    content: str
    start_line: int
    end_line: int
    symbol: str | None = None
    symbol_kind: str | None = None
    section: str | None = None
    language: str | None = None


@dataclass(frozen=True)
class SymbolDef:
    name: str
    qualname: str
    kind: str
    start_line: int
    end_line: int


@dataclass
class ChunkedFile:
    chunks: list[ChunkDraft]
    symbols: list[SymbolDef] = field(default_factory=list)


@dataclass(frozen=True)
class ChunkingConfig:
    max_lines: int = 80
    max_chars: int = 3200
    window_overlap: int = 5
    doc_max_chars: int = 2400


def classify_path(path: str) -> tuple[str, str] | None:
    """Return ``(source_type, language)`` for an indexable path, or ``None``."""
    pure = PurePosixPath(path)
    name = pure.name.lower()
    if name in SPECIAL_FILENAMES:
        return "code", SPECIAL_FILENAMES[name]
    for prefix, language in SPECIAL_PREFIXES.items():
        if name.startswith(prefix):
            return "code", language
    suffix = pure.suffix.lower()
    if suffix in DOC_LANGUAGES:
        return "doc", DOC_LANGUAGES[suffix]
    if suffix in CODE_LANGUAGES:
        return "code", CODE_LANGUAGES[suffix]
    if suffix in BINARY_SUFFIXES:
        return None
    # Any other file is indexed as plain text (what to leave out is .gitignore's job); the
    # indexer still skips binary content it did not recognise by name.
    return "code", "text"


def chunk_file(path: str, text: str, config: ChunkingConfig | None = None) -> ChunkedFile:
    cfg = config or ChunkingConfig()
    kind = classify_path(path)
    if kind is None:
        raise ValueError(f"unsupported file type: {path}")
    source_type, language = kind
    if source_type == "doc":
        if language == "markdown":
            return ChunkedFile(_dedupe(_chunk_markdown(text, cfg, language)))
        return ChunkedFile(_dedupe(_chunk_paragraphs(text, cfg, language)))
    return _chunk_code(text, language, cfg)


# --------------------------------------------------------------------------------------------
# Documentation


_ATX_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_FENCE_RE = re.compile(r"^\s*(```|~~~)")


def _chunk_markdown(text: str, cfg: ChunkingConfig, language: str) -> list[ChunkDraft]:
    lines = text.splitlines()
    sections: list[tuple[str, int, int]] = []  # (breadcrumb, start, end) 0-based inclusive
    stack: list[tuple[int, str]] = []
    in_fence = False
    current_title = "_preamble"
    current_start = 0
    for index, line in enumerate(lines):
        if _FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        heading = _ATX_RE.match(line)
        level = 0
        title = ""
        if heading:
            level, title = len(heading.group(1)), heading.group(2).strip()
        elif (
            index + 1 < len(lines)
            and re.fullmatch(r"=+\s*", lines[index + 1] or "x")
            and line.strip()
        ):
            level, title = 1, line.strip()
        if not level:
            continue
        if index > current_start:
            sections.append((current_title, current_start, index - 1))
        while stack and stack[-1][0] >= level:
            stack.pop()
        stack.append((level, title or "untitled"))
        current_title = " > ".join(t for _, t in stack)
        current_start = index
    if current_start < len(lines):
        sections.append((current_title, current_start, len(lines) - 1))

    chunks: list[ChunkDraft] = []
    for breadcrumb, start, end in sections:
        body = lines[start : end + 1]
        if not any(line.strip() and not _ATX_RE.match(line) for line in body):
            continue
        for part, (p_start, p_end) in enumerate(_paragraph_parts(body, cfg.doc_max_chars), start=1):
            content_lines = body[p_start : p_end + 1]
            if part > 1 and breadcrumb != "_preamble":
                content_lines = [f"[{breadcrumb}] (continued)", *content_lines]
            content = "\n".join(content_lines).strip("\n")
            if not content.strip():
                continue
            chunks.append(
                ChunkDraft(
                    anchor=f"{breadcrumb}#{part}",
                    content=content,
                    start_line=start + p_start + 1,
                    end_line=start + p_end + 1,
                    section=breadcrumb,
                    language=language,
                )
            )
    return chunks


def _paragraph_parts(lines: list[str], max_chars: int) -> list[tuple[int, int]]:
    """Group blank-line separated blocks (fences kept atomic) into parts under ``max_chars``."""
    blocks: list[tuple[int, int]] = []
    start: int | None = None
    in_fence = False
    for index, line in enumerate(lines):
        if _FENCE_RE.match(line):
            in_fence = not in_fence
        if not line.strip() and not in_fence:
            if start is not None:
                blocks.append((start, index - 1))
                start = None
            continue
        if start is None:
            start = index
    if start is not None:
        blocks.append((start, len(lines) - 1))
    parts: list[tuple[int, int]] = []
    part_start: int | None = None
    prev_end = 0
    size = 0
    for b_start, b_end in blocks:
        block_size = sum(len(lines[i]) + 1 for i in range(b_start, b_end + 1))
        if part_start is not None and size + block_size > max_chars:
            parts.append((part_start, prev_end))
            part_start, size = None, 0
        if part_start is None:
            part_start = b_start
        size += block_size
        prev_end = b_end
        if block_size > max_chars:  # a single oversized block becomes its own line windows
            parts.extend(_line_windows(lines, part_start, b_end, max_chars, 60, 0))
            part_start, size = None, 0
    if part_start is not None:
        parts.append((part_start, prev_end))
    return parts


def _chunk_paragraphs(text: str, cfg: ChunkingConfig, language: str) -> list[ChunkDraft]:
    lines = text.splitlines()
    chunks = []
    for part, (start, end) in enumerate(_paragraph_parts(lines, cfg.doc_max_chars), start=1):
        content = "\n".join(lines[start : end + 1]).strip("\n")
        if content.strip():
            chunks.append(
                ChunkDraft(
                    anchor=f"_text#{part}",
                    content=content,
                    start_line=start + 1,
                    end_line=end + 1,
                    language=language,
                )
            )
    return chunks


# --------------------------------------------------------------------------------------------
# Code

_PY_DEF_RE = re.compile(
    r"^(?P<indent>[ \t]*)(?:async[ \t]+)?(?P<kind>def|class)[ \t]+(?P<name>[A-Za-z_]\w*)"
)

_BRACE_PATTERNS: dict[str, list[tuple[re.Pattern[str], str]]] = {
    "javascript": [
        (
            re.compile(
                r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s*\*?\s*"
                r"(?P<name>[A-Za-z_$][\w$]*)"
            ),
            "function",
        ),
        (
            re.compile(
                r"^\s*(?:export\s+)?(?:default\s+)?(?:abstract\s+)?class\s+"
                r"(?P<name>[A-Za-z_$][\w$]*)"
            ),
            "class",
        ),
        (
            re.compile(
                r"^\s*(?:export\s+)?(?:const|let|var)\s+(?P<name>[A-Za-z_$][\w$]*)\s*"
                r"(?::[^=]+)?=\s*(?:async\s+)?(?:function\b|\([^)]*\)\s*(?::[^=]+)?=>|"
                r"[A-Za-z_$][\w$]*\s*=>)"
            ),
            "function",
        ),
        (re.compile(r"^\s*(?:export\s+)?interface\s+(?P<name>[A-Za-z_$][\w$]*)"), "interface"),
        (
            re.compile(r"^\s*(?:export\s+)?type\s+(?P<name>[A-Za-z_$][\w$]*)\s*(?:<[^=]*>)?\s*="),
            "type",
        ),
        (
            re.compile(
                r"^\s+(?:(?:public|private|protected|static|async|readonly|override|get|set)"
                r"\s+)*(?P<name>(?!if\b|for\b|while\b|switch\b|catch\b|return\b)"
                r"[A-Za-z_$][\w$]*)\s*\([^;]*\)\s*(?::\s*[^{;]+)?\{\s*$"
            ),
            "method",
        ),
    ],
    "go": [
        (re.compile(r"^func\s+(?:\((?P<recv>[^)]*)\)\s*)?(?P<name>[A-Za-z_]\w*)"), "function"),
        (re.compile(r"^type\s+(?P<name>[A-Za-z_]\w*)\s+(?:struct|interface)\b"), "type"),
    ],
    "rust": [
        (
            re.compile(
                r"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:const\s+)?(?:async\s+)?(?:unsafe\s+)?"
                r"(?:extern\s+\"[^\"]*\"\s+)?fn\s+(?P<name>[A-Za-z_]\w*)"
            ),
            "function",
        ),
        (
            re.compile(
                r"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:struct|enum|trait|union)\s+"
                r"(?P<name>[A-Za-z_]\w*)"
            ),
            "type",
        ),
        (
            re.compile(r"^\s*impl(?:<[^>]*>)?\s+(?:[\w:<>, ]+\s+for\s+)?(?P<name>[A-Za-z_]\w*)"),
            "impl",
        ),
        (re.compile(r"^\s*(?:pub(?:\([^)]*\))?\s+)?mod\s+(?P<name>[A-Za-z_]\w*)\s*\{"), "module"),
    ],
    "shell": [
        (re.compile(r"^\s*(?:function\s+)?(?P<name>[A-Za-z_][\w-]*)\s*\(\)\s*\{?"), "function"),
    ],
}
_C_LIKE_TYPE = re.compile(
    r"^\s*(?:(?:public|private|protected|internal|static|final|abstract|sealed|partial|data|"
    r"open|export)\s+)*(?:class|interface|struct|enum|record|object|trait)\s+(?P<name>[A-Za-z_]\w*)"
)
_C_LIKE_FUNC = re.compile(
    r"^\s*(?:(?:public|private|protected|internal|static|final|abstract|override|virtual|async|"
    r"inline|extern|suspend|open|operator|fun|func|def|function)\s+)*(?:[\w<>\[\],.?*&:]+\s+)*"
    r"(?P<name>(?!if\b|for\b|while\b|switch\b|catch\b|return\b|else\b|new\b)[A-Za-z_]\w*)\s*"
    r"\([^;]*\)?\s*(?:[:\-]>?\s*[\w<>\[\],.? ]+)?\s*(?:throws\s+[\w., ]+)?\s*\{?\s*$"
)
_STRING_RE = re.compile(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|`(?:\\.|[^`\\])*`')


def _chunk_code(text: str, language: str, cfg: ChunkingConfig) -> ChunkedFile:
    lines = text.splitlines()
    if language == "python":
        symbols = _python_symbols(lines)
    elif language in BRACE_LANGUAGES or language in {"java", "kotlin", "csharp", "c", "cpp"}:
        symbols = _brace_symbols(lines, language)
    else:
        symbols = []
    chunks = _assemble_code_chunks(lines, symbols, language, cfg)
    return ChunkedFile(_dedupe(chunks), symbols)


def _python_symbols(lines: list[str]) -> list[SymbolDef]:
    symbols: list[SymbolDef] = []
    stack: list[tuple[int, str, int]] = []  # (indent, qualname, end_line_index)
    for index, line in enumerate(lines):
        match = _PY_DEF_RE.match(line)
        if not match:
            continue
        indent = len(match.group("indent").expandtabs(4))
        while stack and (stack[-1][0] >= indent or stack[-1][2] < index):
            stack.pop()
        name = match.group("name")
        qualname = f"{stack[-1][1]}.{name}" if stack else name
        start = index
        while start > 0 and lines[start - 1].strip().startswith("@"):
            start -= 1
        end = _python_block_end(lines, index, indent)
        kind = "class" if match.group("kind") == "class" else ("method" if stack else "function")
        symbols.append(SymbolDef(name, qualname, kind, start + 1, end + 1))
        stack.append((indent, qualname, end))
    return symbols


def _python_block_end(lines: list[str], def_index: int, indent: int) -> int:
    depth = 0
    body_start = def_index
    for index in range(def_index, min(len(lines), def_index + 40)):
        code = _STRING_RE.sub("", lines[index].split("#", 1)[0])
        depth += sum(code.count(c) for c in "([{") - sum(code.count(c) for c in ")]}")
        if depth <= 0 and code.rstrip().endswith(":"):
            body_start = index
            break
    end = body_start
    in_triple: str | None = None
    for index in range(body_start + 1, len(lines)):
        line = lines[index]
        stripped = line.strip()
        if in_triple:
            end = index
            if stripped.count(in_triple) % 2 == 1:
                in_triple = None
            continue
        if not stripped:
            continue
        current = len(line[: len(line) - len(line.lstrip())].expandtabs(4))
        if current <= indent and not stripped.startswith("#"):
            break
        end = index
        for quote in ('"""', "'''"):
            if stripped.count(quote) % 2 == 1:
                in_triple = quote
                break
    return end


def _brace_symbols(lines: list[str], language: str) -> list[SymbolDef]:
    patterns = _BRACE_PATTERNS.get(language)
    if patterns is None:
        patterns = [(_C_LIKE_TYPE, "type"), (_C_LIKE_FUNC, "function")]
    generic = language not in _BRACE_PATTERNS
    found: list[tuple[int, int, str, str]] = []  # start, end, name, kind
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith(("//", "/*", "*", "#")):
            continue
        for pattern, kind in patterns:
            match = pattern.match(line)
            if not match:
                continue
            if generic and kind == "function" and not _opens_block_here(lines, index):
                break
            end = _brace_block_end(lines, index)
            if end is None:
                if kind in {"type", "interface"}:
                    end = index
                else:
                    break
            name = match.group("name")
            if language == "go" and match.groupdict().get("recv"):
                receiver = re.findall(r"[A-Za-z_]\w*", match.group("recv") or "")
                if receiver:
                    name = f"{receiver[-1]}.{name}"
            found.append((index, end, name, kind))
            break
    symbols: list[SymbolDef] = []
    open_parents: list[tuple[int, str]] = []  # (end_index, qualname)
    for start, end, name, kind in found:
        while open_parents and open_parents[-1][0] < start:
            open_parents.pop()
        qualname = f"{open_parents[-1][1]}.{name}" if open_parents and "." not in name else name
        simple = name.split(".")[-1]
        if open_parents and kind == "function":
            kind = "method"
        symbols.append(SymbolDef(simple, qualname, kind, start + 1, end + 1))
        if end > start:
            open_parents.append((end, qualname))
    return symbols


def _opens_block_here(lines: list[str], index: int) -> bool:
    """Generic C-like heuristics: a definition opens ``{`` on its line or the next one."""
    if "{" in lines[index]:
        return True
    for following in lines[index + 1 : index + 3]:
        if following.strip():
            return following.strip().startswith("{")
    return False


def _brace_block_end(lines: list[str], start: int) -> int | None:
    depth = 0
    opened = False
    for index in range(start, min(len(lines), start + 2000)):
        code = _STRING_RE.sub("", lines[index].split("//", 1)[0])
        if not opened and index > start + 4:
            return None
        if not opened and code.rstrip().endswith(";") and "{" not in code:
            return None
        for char in code:
            if char == "{":
                depth += 1
                opened = True
            elif char == "}":
                depth -= 1
                if opened and depth <= 0:
                    return index
    return len(lines) - 1 if opened else None


def _assemble_code_chunks(
    lines: list[str], symbols: list[SymbolDef], language: str, cfg: ChunkingConfig
) -> list[ChunkDraft]:
    ranges = [(s.start_line - 1, s.end_line - 1, s) for s in symbols]
    top = [
        r
        for r in ranges
        if not any(
            o is not r and o[0] <= r[0] and r[1] <= o[1] and (o[0], o[1]) != (r[0], r[1])
            for o in ranges
        )
    ]
    top = _unique_ranges(top)
    chunks: list[ChunkDraft] = []
    cursor = 0
    previous = "^"
    for start, end, symbol in top:
        if start < cursor:
            continue
        chunks.extend(_module_chunks(lines, cursor, start - 1, previous, language, cfg))
        chunks.extend(_symbol_chunks(lines, start, end, symbol, ranges, language, cfg))
        cursor = end + 1
        previous = symbol.qualname
    chunks.extend(_module_chunks(lines, cursor, len(lines) - 1, previous, language, cfg))
    return chunks


def _unique_ranges(
    items: list[tuple[int, int, SymbolDef]],
) -> list[tuple[int, int, SymbolDef]]:
    seen: set[tuple[int, int]] = set()
    result = []
    for item in sorted(items, key=lambda r: (r[0], -r[1])):
        if (item[0], item[1]) not in seen:
            seen.add((item[0], item[1]))
            result.append(item)
    return result


def _symbol_chunks(
    lines: list[str],
    start: int,
    end: int,
    symbol: SymbolDef,
    ranges: list[tuple[int, int, SymbolDef]],
    language: str,
    cfg: ChunkingConfig,
) -> list[ChunkDraft]:
    size = sum(len(lines[i]) + 1 for i in range(start, end + 1))
    if end - start + 1 <= cfg.max_lines and size <= cfg.max_chars:
        return [_draft(lines, start, end, symbol.qualname, symbol, language)]
    children = _unique_ranges(
        [
            r
            for r in ranges
            if start <= r[0]
            and r[1] <= end
            and (r[0], r[1]) != (start, end)
            and not any(
                o[0] <= r[0]
                and r[1] <= o[1]
                and (o[0], o[1]) not in {(r[0], r[1]), (start, end)}
                and start <= o[0]
                and o[1] <= end
                for o in ranges
            )
        ]
    )
    chunks: list[ChunkDraft] = []
    if not children:
        for part, (w_start, w_end) in enumerate(
            _line_windows(lines, start, end, cfg.max_chars, cfg.max_lines, cfg.window_overlap),
            start=1,
        ):
            chunks.append(
                _draft(lines, w_start, w_end, f"{symbol.qualname}#part{part}", symbol, language)
            )
        return chunks
    cursor = start
    part = 0
    for c_start, c_end, child in children:
        if c_start < cursor:
            continue
        if _has_code(lines, cursor, c_start - 1):
            for w_start, w_end in _line_windows(
                lines, cursor, c_start - 1, cfg.max_chars, cfg.max_lines, 0
            ):
                part += 1
                chunks.append(
                    _draft(lines, w_start, w_end, f"{symbol.qualname}#body{part}", symbol, language)
                )
        chunks.extend(_symbol_chunks(lines, c_start, c_end, child, ranges, language, cfg))
        cursor = c_end + 1
    if _has_code(lines, cursor, end):
        for w_start, w_end in _line_windows(lines, cursor, end, cfg.max_chars, cfg.max_lines, 0):
            part += 1
            chunks.append(
                _draft(lines, w_start, w_end, f"{symbol.qualname}#body{part}", symbol, language)
            )
    return chunks


def _module_chunks(
    lines: list[str], start: int, end: int, previous: str, language: str, cfg: ChunkingConfig
) -> list[ChunkDraft]:
    if end < start or not _has_code(lines, start, end, minimum=1):
        return []
    anchor = (
        "_file"
        if previous == "^" and start == 0 and end == len(lines) - 1
        else (f"_module@{previous}")
    )
    chunks = []
    for part, (w_start, w_end) in enumerate(
        _line_windows(lines, start, end, cfg.max_chars, cfg.max_lines, cfg.window_overlap),
        start=1,
    ):
        if _has_code(lines, w_start, w_end, minimum=1):
            chunks.append(_draft(lines, w_start, w_end, f"{anchor}#{part}", None, language))
    return chunks


def _has_code(lines: list[str], start: int, end: int, minimum: int = 1) -> bool:
    count = 0
    for index in range(max(start, 0), min(end, len(lines) - 1) + 1):
        stripped = lines[index].strip()
        if stripped and stripped not in {"}", "};", ")", "]", "});"}:
            count += 1
            if count >= minimum:
                return True
    return False


def _line_windows(
    lines: list[str], start: int, end: int, max_chars: int, max_lines: int, overlap: int
) -> list[tuple[int, int]]:
    windows: list[tuple[int, int]] = []
    cursor = start
    while cursor <= end:
        size = 0
        stop = cursor
        while stop <= end and stop - cursor < max_lines:
            size += len(lines[stop]) + 1
            if size > max_chars and stop > cursor:
                break
            stop += 1
        stop = max(stop - 1, cursor)
        windows.append((cursor, stop))
        if stop >= end:
            break
        cursor = max(stop + 1 - overlap, cursor + 1)
    return windows


def _draft(
    lines: list[str],
    start: int,
    end: int,
    anchor: str,
    symbol: SymbolDef | None,
    language: str,
) -> ChunkDraft:
    content = "\n".join(lines[start : end + 1])
    if len(content) > 20000:  # hard cap for pathological single lines (minified code)
        content = content[:20000]
    return ChunkDraft(
        anchor=anchor,
        content=content,
        start_line=start + 1,
        end_line=end + 1,
        symbol=symbol.qualname if symbol else None,
        symbol_kind=symbol.kind if symbol else None,
        language=language,
    )


def _dedupe(chunks: list[ChunkDraft]) -> list[ChunkDraft]:
    counts: dict[str, int] = {}
    result = []
    for chunk in chunks:
        counts[chunk.anchor] = counts.get(chunk.anchor, 0) + 1
        if counts[chunk.anchor] > 1:
            chunk = ChunkDraft(
                **{**chunk.__dict__, "anchor": f"{chunk.anchor}~{counts[chunk.anchor]}"}
            )
        result.append(chunk)
    return result
