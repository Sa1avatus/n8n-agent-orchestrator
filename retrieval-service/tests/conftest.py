from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from ahawr_retrieval.config import Settings
from ahawr_retrieval.models import IndexRequest
from ahawr_retrieval.reranker import NoopReranker, Reranker
from ahawr_retrieval.service import RetrievalService

PARSER_PY = '''"""Invoice parsing utilities."""

import json

MAX_LINES = 500


def parse_invoice(text):
    """Parse an invoice document into a dictionary of fields."""
    lines = text.splitlines()[:MAX_LINES]
    return {"lines": lines, "total": compute_total(lines)}


def compute_total(lines):
    total = 0
    for line in lines:
        if line.startswith("TOTAL"):
            total += float(line.split()[-1])
    return total


class InvoiceCache:
    """In-memory cache keyed by invoice number."""

    def __init__(self):
        self._items = {}

    @property
    def size(self):
        return len(self._items)

    def store(self, number, invoice):
        self._items[number] = json.dumps(invoice)

    def evict(self, number):
        self._items.pop(number, None)
'''

TEST_PY = """from app.parser import compute_total, parse_invoice


def test_compute_total_sums_total_lines():
    assert compute_total(["TOTAL 5", "TOTAL 7"]) == 12


def test_parse_invoice_returns_lines():
    assert parse_invoice("a\\nb")["lines"] == ["a", "b"]
"""

CLIENT_TS = """export interface PaymentRequest {
  amount: number;
  currency: string;
}

export async function submitPayment(request: PaymentRequest): Promise<string> {
  const response = await fetch("/pay", { method: "POST", body: JSON.stringify(request) });
  return response.text();
}

export class RetryPolicy {
  constructor(private readonly attempts: number) {}

  shouldRetry(attempt: number): boolean {
    return attempt < this.attempts;
  }
}
"""

GUIDE_MD = """# Billing Guide

Overview of the billing subsystem.

## Invoice totals

The invoice total is the sum of every TOTAL line. Totals are computed by
`compute_total` and must never include tax lines twice.

## Payment retries

Payments are retried by the RetryPolicy with a bounded number of attempts.

```python
# headings inside fences are not sections
# Not A Heading
```

## Deployment

Deploy with docker compose and run migrations first.
"""


def write_workspace(root: Path) -> None:
    (root / "app").mkdir(parents=True, exist_ok=True)
    (root / "tests").mkdir(exist_ok=True)
    (root / "web").mkdir(exist_ok=True)
    (root / "docs").mkdir(exist_ok=True)
    (root / "node_modules" / "lib").mkdir(parents=True, exist_ok=True)
    (root / "app" / "parser.py").write_text(PARSER_PY, encoding="utf-8")
    (root / "tests" / "test_parser.py").write_text(TEST_PY, encoding="utf-8")
    (root / "web" / "client.ts").write_text(CLIENT_TS, encoding="utf-8")
    (root / "docs" / "guide.md").write_text(GUIDE_MD, encoding="utf-8")
    (root / ".env").write_text("SECRET_TOKEN=do-not-index\n", encoding="utf-8")
    (root / "server.pem").write_text("-----BEGIN PRIVATE KEY-----\n", encoding="utf-8")
    (root / "node_modules" / "lib" / "index.js").write_text("function x() {}\n")
    (root / "generated").mkdir(exist_ok=True)
    (root / "generated" / "out.py").write_text("GENERATED = True\nVALUE = 1\n")
    (root / ".gitignore").write_text("generated/\n*.tmp\n", encoding="utf-8")


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    root = tmp_path / "workspace"
    root.mkdir()
    write_workspace(root)
    return root


@pytest.fixture
def settings(tmp_path: Path, workspace: Path) -> Settings:
    return Settings(
        data_dir=tmp_path / "data",
        allowed_roots=[str(tmp_path)],
        sync_min_interval_seconds=0.0,
    )


def make_service(settings: Settings, reranker: Reranker | None = None) -> RetrievalService:
    return RetrievalService(settings, reranker=reranker or NoopReranker())


@pytest.fixture
def service(settings: Settings) -> Iterator[RetrievalService]:
    svc = make_service(settings)
    yield svc
    svc.close()


@pytest.fixture
def indexed(service: RetrievalService, workspace: Path) -> RetrievalService:
    service.index(IndexRequest(corpus_id="ws", root=str(workspace)))
    return service
