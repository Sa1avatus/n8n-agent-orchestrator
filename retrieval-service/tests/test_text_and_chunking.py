from ahawr_retrieval.chunking import ChunkingConfig, chunk_file, classify_path
from ahawr_retrieval.text import (
    content_terms,
    expand_for_index,
    extract_identifiers,
    extract_paths,
    split_identifier,
)

from .conftest import CLIENT_TS, GUIDE_MD, PARSER_PY


def test_split_identifier_handles_camel_and_snake() -> None:
    assert split_identifier("parseHTTPResponse_v2") == ["parse", "http", "response", "v2"]
    assert split_identifier("compute_total") == ["compute", "total"]


def test_identifier_and_path_extraction() -> None:
    text = "Fix `InvoiceCache.evict` in app/parser.py and call compute_total() from README.md"
    idents = extract_identifiers(text)
    assert "InvoiceCache.evict" in idents
    assert "compute_total" in idents
    paths = extract_paths(text)
    assert "app/parser.py" in paths and "README.md" in paths


def test_content_terms_drop_stopwords_and_expand_identifiers() -> None:
    terms = content_terms("The parse_invoice function must handle totals")
    assert "the" not in terms and "must" not in terms
    assert {"parse_invoice", "parse", "invoice", "totals"} <= set(terms)
    assert "snake" in expand_for_index("snake_case value")


def test_classify_path() -> None:
    assert classify_path("docs/guide.md") == ("doc", "markdown")
    assert classify_path("app/x.py") == ("code", "python")
    assert classify_path("Dockerfile") == ("code", "dockerfile")
    assert classify_path("image.png") is None


def test_python_chunks_follow_symbols_with_qualnames() -> None:
    chunked = chunk_file("app/parser.py", PARSER_PY)
    symbols = {s.qualname: s for s in chunked.symbols}
    assert {
        "parse_invoice",
        "compute_total",
        "InvoiceCache",
        "InvoiceCache.size",
        "InvoiceCache.store",
    } <= set(symbols)
    assert symbols["InvoiceCache.store"].kind == "method"
    size = symbols["InvoiceCache.size"]
    # decorator lines belong to the method
    assert PARSER_PY.splitlines()[size.start_line - 1].strip() == "@property"
    anchors = [c.anchor for c in chunked.chunks]
    assert "parse_invoice" in anchors and "compute_total" in anchors
    assert any(a.startswith("_module@^") for a in anchors)  # imports / constants


def test_large_class_is_split_into_methods() -> None:
    body = "\n".join(f"    def method_{i}(self):\n        return {i}\n" for i in range(40))
    text = f'class Big:\n    """Doc."""\n\n{body}'
    chunked = chunk_file("big.py", text, ChunkingConfig(max_lines=30))
    anchors = {c.anchor for c in chunked.chunks}
    assert "Big.method_0" in anchors and "Big.method_39" in anchors
    assert any(a.startswith("Big#body") for a in anchors)


def test_editing_one_function_keeps_other_chunks_stable() -> None:
    before = {c.anchor: c.content for c in chunk_file("app/parser.py", PARSER_PY).chunks}
    edited = PARSER_PY.replace("total += float", "total += 1.0 * float")
    edited = "# leading comment shifts every line\n" + edited
    after = {c.anchor: c.content for c in chunk_file("app/parser.py", edited).chunks}
    changed = {a for a in before if before[a] != after.get(a)}
    assert changed <= {"compute_total", "_module@^#1"}
    assert "compute_total" in changed
    assert before["parse_invoice"] == after["parse_invoice"]


def test_typescript_symbols() -> None:
    chunked = chunk_file("web/client.ts", CLIENT_TS)
    qualnames = {s.qualname for s in chunked.symbols}
    assert {
        "PaymentRequest",
        "submitPayment",
        "RetryPolicy",
        "RetryPolicy.shouldRetry",
    } <= qualnames


def test_go_receiver_methods() -> None:
    text = (
        "package main\n\nfunc (s *Server) Start(port int) error {\n\treturn nil\n}\n\n"
        "type Server struct {\n\tport int\n}\n"
    )
    qualnames = {s.qualname for s in chunk_file("main.go", text).symbols}
    assert {"Server.Start", "Server"} <= qualnames


def test_markdown_sections_ignore_fenced_headings() -> None:
    chunked = chunk_file("docs/guide.md", GUIDE_MD)
    sections = [c.section for c in chunked.chunks]
    assert "Billing Guide > Invoice totals" in sections
    assert "Billing Guide > Payment retries" in sections
    assert not any(s and "Not A Heading" in s for s in sections)


def test_long_markdown_section_is_split_with_breadcrumb() -> None:
    paragraphs = "\n\n".join(f"Paragraph {i} " + "word " * 80 for i in range(20))
    chunked = chunk_file("long.md", f"# Title\n\n## Part\n\n{paragraphs}\n")
    parts = [c for c in chunked.chunks if c.section == "Title > Part"]
    assert len(parts) > 1
    assert parts[1].content.startswith("[Title > Part] (continued)")
