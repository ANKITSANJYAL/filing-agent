"""The four retrieval configurations the W1 ablation compares (PROPOSAL.md §4.2).

    lexical            Postgres full-text search over the generated tsvector
    dense              pgvector cosine over BGE-M3 embeddings
    hybrid             the two fused by reciprocal rank fusion
    hybrid + rerank    fusion, then a bge-reranker-v2-m3 cross-encoder

Each returns the same `Hit` shape, so T1.6 can score them with one harness rather than
four, and Arm B vs Arm C differ only in which function is called.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, Final

import psycopg
from pydantic import BaseModel

if TYPE_CHECKING:
    from sentence_transformers import CrossEncoder

RERANKER_NAME: Final[str] = "BAAI/bge-reranker-v2-m3"

# RRF's smoothing constant. 60 is the value from the original Cormack et al. paper and
# the usual default; it damps the influence of very high ranks so that a chunk ranked
# 1st by one retriever cannot alone dominate a chunk ranked well by both.
RRF_K: Final[int] = 60

DEFAULT_TOP_K: Final[int] = 10
# Fusion sees more candidates than it returns, or agreement between the two retrievers
# cannot be observed below the cut.
CANDIDATE_MULTIPLIER: Final[int] = 5


class Hit(BaseModel):
    chunk_id: str
    accession_no: str
    ticker: str
    fiscal_year: int
    item_section: str
    text: str
    score: float

    @property
    def citation(self) -> str:
        """What a claim points at: filing + section + chunk."""
        return f"{self.accession_no}#{self.item_section}#{self.chunk_id}"


_SELECT: Final[str] = (
    "chunk_id, accession_no, ticker, fiscal_year, item_section, text"
)


def _filters(
    tickers: Sequence[str] | None, fiscal_years: Sequence[int] | None
) -> tuple[str, list[Any]]:
    """Metadata filters — the payoff for carrying chunk metadata (PROPOSAL.md §4.1)."""
    clauses: list[str] = []
    params: list[Any] = []
    if tickers:
        clauses.append("ticker = ANY(%s)")
        params.append(list(tickers))
    if fiscal_years:
        clauses.append("fiscal_year = ANY(%s)")
        params.append(list(fiscal_years))
    return (" AND " + " AND ".join(clauses) if clauses else ""), params


def _to_hits(rows: Sequence[tuple], scores: Sequence[float]) -> list[Hit]:
    return [
        Hit(chunk_id=r[0], accession_no=r[1], ticker=r[2], fiscal_year=r[3],
            item_section=r[4], text=r[5], score=float(s))
        for r, s in zip(rows, scores, strict=True)
    ]


def lexical_search(
    conn: psycopg.Connection,
    query: str,
    top_k: int = DEFAULT_TOP_K,
    tickers: Sequence[str] | None = None,
    fiscal_years: Sequence[int] | None = None,
) -> list[Hit]:
    """Postgres FTS with OR semantics, standing in for BM25 (D-0028).

    The query is lexemised and joined with `|` rather than passed to `plainto_tsquery`,
    which ANDs every term. Conjunctive matching is the wrong model for a natural-language
    question: "What are the principal risks AAPL disclosed for fiscal 2024?" requires a
    chunk containing *all* of those stems, and filings write "Apple Inc." rather than the
    ticker, so the AND matched nothing at all — scoring lexical retrieval 0.000 across
    every case. BM25 ranks documents containing *any* query term, weighted by rarity;
    OR + `ts_rank_cd` is the closest Postgres equivalent.
    """
    where, params = _filters(tickers, fiscal_years)
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT {_SELECT}, ts_rank_cd(tsv, q) AS score "  # noqa: S608 - fixed columns
            "FROM chunks, to_tsquery('english', "
            "  array_to_string(tsvector_to_array(to_tsvector('english', %s)), ' | ')"
            f") q WHERE tsv @@ q{where} ORDER BY score DESC LIMIT %s",
            [query, *params, top_k],
        )
        rows = cur.fetchall()
    return _to_hits([r[:6] for r in rows], [r[6] for r in rows])


def dense_search(
    conn: psycopg.Connection,
    query_vector: Any,
    top_k: int = DEFAULT_TOP_K,
    tickers: Sequence[str] | None = None,
    fiscal_years: Sequence[int] | None = None,
) -> list[Hit]:
    """pgvector cosine. Score is similarity (1 - distance), so higher is better in
    every retriever — mixing conventions is how fusion silently inverts."""
    where, params = _filters(tickers, fiscal_years)
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT {_SELECT}, 1 - (embedding <=> %s) AS score "  # noqa: S608
            f"FROM chunks WHERE embedding IS NOT NULL{where} "
            f"ORDER BY embedding <=> %s LIMIT %s",
            [query_vector, *params, query_vector, top_k],
        )
        rows = cur.fetchall()
    return _to_hits([r[:6] for r in rows], [r[6] for r in rows])


def reciprocal_rank_fusion(
    rankings: Sequence[Sequence[Hit]], top_k: int = DEFAULT_TOP_K, k: int = RRF_K
) -> list[Hit]:
    """Fuse ranked lists by 1/(k + rank).

    Rank-based rather than score-based on purpose: `ts_rank_cd` and cosine similarity
    are not on comparable scales, and normalising them would introduce a tuning knob
    that has to be justified. Ranks need no such calibration.
    """
    scores: dict[str, float] = {}
    best: dict[str, Hit] = {}
    for ranking in rankings:
        for rank, hit in enumerate(ranking, start=1):
            scores[hit.chunk_id] = scores.get(hit.chunk_id, 0.0) + 1.0 / (k + rank)
            best.setdefault(hit.chunk_id, hit)
    ordered = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
    return [best[cid].model_copy(update={"score": score}) for cid, score in ordered[:top_k]]


def hybrid_search(
    conn: psycopg.Connection,
    query: str,
    query_vector: Any,
    top_k: int = DEFAULT_TOP_K,
    tickers: Sequence[str] | None = None,
    fiscal_years: Sequence[int] | None = None,
) -> list[Hit]:
    depth = top_k * CANDIDATE_MULTIPLIER
    return reciprocal_rank_fusion(
        [
            lexical_search(conn, query, depth, tickers, fiscal_years),
            dense_search(conn, query_vector, depth, tickers, fiscal_years),
        ],
        top_k=top_k,
    )


def load_reranker(device: str | None = None) -> CrossEncoder:
    from sentence_transformers import CrossEncoder

    from .embed import resolve_device

    return CrossEncoder(RERANKER_NAME, device=resolve_device(device))


def rerank(
    reranker: CrossEncoder, query: str, hits: Sequence[Hit], top_k: int = DEFAULT_TOP_K
) -> list[Hit]:
    """Cross-encoder rescoring.

    Unlike the bi-encoder, this reads query and chunk *together*, so it can judge
    relevance rather than similarity — at the cost of one forward pass per candidate,
    which is why it only ever sees the fused shortlist.
    """
    if not hits:
        return []
    scores = reranker.predict([(query, hit.text) for hit in hits])
    scored = sorted(
        (h.model_copy(update={"score": float(s)}) for h, s in zip(hits, scores, strict=True)),
        key=lambda h: -h.score,
    )
    return scored[:top_k]
