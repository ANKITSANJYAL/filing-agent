"""FastAPI service (PROPOSAL.md §4.6 / T2.6): API-key auth and rate limiting.

The rate limiter is the same `TokenBucket` written for SEC etiquette. We ask SEC to
tolerate 8 req/s from us; extending the same courtesy to callers of this service costs
one import and keeps one implementation under test.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any, Final

from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from pydantic import BaseModel, Field

from ..agent.graph import answer
from ..agent.schemas import Memo
from ..ingest.edgar_client import TokenBucket

API_KEY_ENV: Final[str] = "FILING_AGENT_API_KEY"
# Per-caller budget. Generous enough for interactive use, low enough that a runaway
# client cannot drive model spend unbounded.
RATE_PER_SECOND: Final[float] = 1.0
BURST: Final[float] = 5.0

DISCLAIMER: Final[str] = (
    "Research and citation tool over public SEC filings. Not investment advice. "
    "Figures are verified against SEC XBRL data where available; unverified figures "
    "are flagged in confidence_notes."
)


class AskRequest(BaseModel):
    question: str = Field(min_length=3, max_length=1000)


class AskResponse(BaseModel):
    memo: Memo
    disclaimer: str = DISCLAIMER


class HealthResponse(BaseModel):
    status: str
    chunks: int
    facts: int


def _require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    """Reject unauthenticated calls.

    If no key is configured the service refuses *every* request rather than running
    open — failing closed is the only safe default for something that spends money.
    """
    expected = os.environ.get(API_KEY_ENV, "")
    if not expected:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            f"{API_KEY_ENV} is not configured; refusing to serve unauthenticated",
        )
    if x_api_key != expected:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid or missing X-API-Key")


def create_app(
    conn_factory: Callable[[], Any] | None = None,
    client_factory: Callable[[], Any] | None = None,
    encoder_factory: Callable[[], Any] | None = None,
) -> FastAPI:
    """Build the app. Factories are injected so tests need no database or API key."""
    app = FastAPI(
        title="filing-agent",
        description=DISCLAIMER,
        version="0.1.0",
    )
    buckets: dict[str, TokenBucket] = {}
    state: dict[str, Any] = {}

    def _resources() -> tuple[Any, Any, Any]:
        if "conn" not in state:
            import anthropic

            from ..retrieval import db
            from ..retrieval.embed import load_encoder

            state["conn"] = (conn_factory or db.connect)()
            state["client"] = (client_factory or anthropic.Anthropic)()
            state["encoder"] = (encoder_factory or load_encoder)()
        return state["conn"], state["client"], state["encoder"]

    def _rate_limit(request: Request, x_api_key: str | None = Header(default=None)) -> None:
        caller = x_api_key or (request.client.host if request.client else "anonymous")
        bucket = buckets.setdefault(caller, TokenBucket(RATE_PER_SECOND, BURST))
        # Non-blocking: a queued request would tie up a worker instead of shedding load.
        if not bucket.try_acquire():
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                f"rate limit exceeded ({RATE_PER_SECOND}/s, burst {int(BURST)})",
            )

    @app.get("/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        conn, _, _ = _resources()
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM chunks")
            chunks = cur.fetchone()[0]
            cur.execute("SELECT count(*) FROM xbrl_facts")
            facts = cur.fetchone()[0]
        return HealthResponse(status="ok", chunks=chunks, facts=facts)

    @app.post(
        "/ask",
        response_model=AskResponse,
        dependencies=[Depends(_require_api_key), Depends(_rate_limit)],
    )
    def ask(payload: AskRequest) -> AskResponse:
        conn, client, encoder = _resources()
        memo = answer(payload.question, conn, client, encoder)
        return AskResponse(memo=memo)

    return app
