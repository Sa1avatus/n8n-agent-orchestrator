"""FastAPI application: POST /retrieve, POST /index, POST /invalidate (+ health, corpora)."""

from __future__ import annotations

import hmac
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from . import __version__
from .config import Settings
from .indexer import IndexingError
from .models import (
    IndexRequest,
    IndexResponse,
    InvalidateRequest,
    InvalidateResponse,
    RetrieveRequest,
    RetrieveResponse,
)
from .service import RetrievalError, RetrievalService


def create_app(
    settings: Settings | None = None,
    service_factory: Callable[[Settings], RetrievalService] | None = None,
) -> FastAPI:
    settings = settings or Settings.from_env()
    service = (service_factory or RetrievalService)(settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        service.close()

    app = FastAPI(
        lifespan=lifespan,
        title="AHAWR Retrieval Service",
        version=__version__,
        description=(
            "Read-only, provenance-aware context retrieval for AHAWR Worker and Reviewer roles. "
            "Never a source of AHAWR execution state."
        ),
    )
    app.state.service = service

    def authorize(authorization: str | None = Header(default=None)) -> None:
        if not settings.api_key:
            return
        expected = f"Bearer {settings.api_key}"
        if not authorization or not hmac.compare_digest(authorization, expected):
            raise HTTPException(status_code=401, detail="invalid or missing bearer token")

    @app.exception_handler(RetrievalError)
    async def _retrieval_error(_: Request, exc: RetrievalError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content={"detail": str(exc)})

    @app.exception_handler(IndexingError)
    async def _indexing_error(_: Request, exc: IndexingError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @app.get("/health")
    def health() -> dict[str, Any]:
        return service.health()

    @app.post("/retrieve", response_model=RetrieveResponse, dependencies=[Depends(authorize)])
    def retrieve(body: RetrieveRequest) -> RetrieveResponse:
        return service.retrieve(body)

    @app.post("/index", response_model=IndexResponse, dependencies=[Depends(authorize)])
    def index(body: IndexRequest) -> IndexResponse:
        return service.index(body)

    @app.post("/invalidate", response_model=InvalidateResponse, dependencies=[Depends(authorize)])
    def invalidate(body: InvalidateRequest) -> InvalidateResponse:
        return service.invalidate(body)

    @app.get("/corpora", dependencies=[Depends(authorize)])
    def corpora() -> list[dict[str, Any]]:
        return [service.corpus_info(c.corpus_id) for c in service.store.list_corpora()]

    @app.get("/corpora/{corpus_id}", dependencies=[Depends(authorize)])
    def corpus(corpus_id: str) -> dict[str, Any]:
        return service.corpus_info(corpus_id)

    return app
