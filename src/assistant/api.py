"""FastAPI: `GET /health` and `POST /v1/query`, the workflow behind HTTP. The answer carries its
status, evidence, SQL, cited rows, trace, cost and latency (`/docs` shows the schema).

Run it:  uv run --env-file .env uvicorn assistant.api:app
"""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated

import psycopg
from fastapi import Depends, FastAPI, HTTPException

from assistant.config import Settings, load_pricing, load_settings
from assistant.llm import make_client
from assistant.retrieve import load_index
from assistant.schemas import Contract, FinalAnswer, QueryRequest
from assistant.workflow import Deps, answer

DB_CHECK_TIMEOUT_S = 2

app = FastAPI(title="Procurement Text-to-SQL Assistant", version="1.0.0")


class Health(Contract):
    status: str  # "ok": the process is up; the two flags say what the questions need
    llm_configured: bool  # OPENAI_API_KEY is set
    database: bool  # the read-only connection answers


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return load_settings()


@lru_cache(maxsize=1)
def _build_deps() -> Deps:
    settings = get_settings()
    return Deps(
        client=make_client(settings),
        settings=settings,
        pricing=load_pricing(settings.pricing_path),
        index=load_index(settings.embedding_model),
    )


def get_deps() -> Deps:
    """Built on the first question, so the API starts (and /health answers) without a key."""
    if not get_settings().llm_configured:
        raise HTTPException(503, "OPENAI_API_KEY is not set on the server")
    return _build_deps()


def database_answers(settings: Settings) -> bool:
    try:
        with psycopg.connect(settings.database_url, connect_timeout=DB_CHECK_TIMEOUT_S) as conn:
            conn.execute("SELECT 1")
    except psycopg.Error:
        return False
    return True


@app.get("/health")
def health(settings: Annotated[Settings, Depends(get_settings)]) -> Health:
    return Health(
        status="ok",
        llm_configured=settings.llm_configured,
        database=database_answers(settings),
    )


@app.post("/v1/query")
def query(request: QueryRequest, deps: Annotated[Deps, Depends(get_deps)]) -> FinalAnswer:
    """Every outcome is a 200 with a status (`error` included, so its cost and trace are kept).
    A missing key is 503 and an invalid request is 422."""
    return answer(request, deps)
