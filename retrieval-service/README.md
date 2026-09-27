# AHAWR Retrieval Service

**English** | [Русский](#русский)

The Context Retrieval Layer for AHAWR is a separate, read-only HTTP service. It gives the Worker and Reviewer relevant, current, provenance-aware context from repository code and project documentation. It never stores or restores AHAWR execution state.

```mermaid
flowchart LR
    N8N["n8n AHAWR v13"] -->|POST /retrieve (fail-open)| API["FastAPI"]
    Ops["Operator / CI"] -->|POST /index · /invalidate| API
    API --> P["Pipeline: sync → query → cache → lexical+vector+symbol → RRF →<br/>hard filters → text-only rerank → deterministic ranking → budgeted context"]
    P <--> L[("Local SQLite index<br/>FTS5 · vectors · symbols · change log · cache")]
    P --> LOG[("Retrieval feature log")]
    P -->|optional| R["reranker-service /v1/rerank"]
    P -->|optional backend + fallback| RAG["rag-platform /v1"]
    WS[("Workspace (read-only)")] --> P
```

## Quick start

```bash
pip install -e ".[dev]"
export RETRIEVAL_ALLOWED_ROOTS=/path/to/workspace RETRIEVAL_DATA_DIR=./var
ahawr-retrieval index my-repo --root /path/to/workspace
ahawr-retrieval retrieve --corpus my-repo --query "where is the retry delay applied?"
ahawr-retrieval serve --port 8500          # POST /retrieve, /index, /invalidate
```

Docker: `cp .env.example .env && docker compose up -d --build`. The workspace is mounted read-only.

## Documentation

| Topic | Document |
|---|---|
| Architecture: profiles, pipeline, freshness, cache, ranking, logging, backends | [`docs/retrieval/ARCHITECTURE.md`](../docs/retrieval/ARCHITECTURE.md) |
| n8n integration, configuration, API, rag-platform backend | [`docs/retrieval/INTEGRATION.md`](../docs/retrieval/INTEGRATION.md) |
| Eval Harness, datasets, metrics, decision rule | [`docs/retrieval/EVALUATION.md`](../docs/retrieval/EVALUATION.md) |
| Phases 1–6 | [`docs/retrieval/ROADMAP.md`](../docs/retrieval/ROADMAP.md) |
| AHAWR state boundary | [`ARCHITECTURE_CONTRACT.md`](../ARCHITECTURE_CONTRACT.md) §5, §9 |

## Development

```bash
ruff check src tests && ruff format --check src tests
mypy src
pytest                     # coverage gate: 80 %
```

The tests need no network or model downloads. HTTP backends (reranker, embeddings, rag-platform) are mocked with `respx`.

---

## Русский

Context Retrieval Layer для AHAWR — отдельный read-only HTTP-сервис. Он даёт Worker и Reviewer релевантный, актуальный контекст из кода репозитория и документации проекта, с provenance для каждого фрагмента. Состояние выполнения AHAWR сервис не хранит и не восстанавливает.

- API: `POST /retrieve`, `POST /index`, `POST /invalidate`, а также `GET /corpora` и `GET /health`.
- Профили: `worker` (контекст для решения задачи) и `reviewer` (контекст для проверки результата, acceptance criteria и validation evidence).
- Retrieval: BM25 (FTS5) + векторы + поиск по символам, RRF, text-only cross-encoder (`reranker-service`), детерминированные фильтры и детерминированный слой ранжирования.
- Версионирование: у кода и документации отдельные модели версий и свежести; content hash у каждого чанка; инкрементальная индексация; инвалидация на уровне чанков.
- Кэш учитывает профиль, задачу, состояние корпуса и семантический fingerprint запроса.
- Логи содержат все признаки кандидатов для будущего LTR.
- Бэкенд: по умолчанию собственный локальный индекс; `rag-platform` подключается как внешний сервис. Если он недоступен, сервис автоматически переходит на локальный индекс.
- Eval Harness: `ahawr-retrieval-eval` (gold/silver, Recall/Precision/MRR/nDCG, системные метрики и метрики AHAWR).

Подробности — в документах из таблицы выше.
