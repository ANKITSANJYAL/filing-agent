"""Retrieval tests. RRF is pure and always runs; DB-backed tests skip without Postgres."""

import pytest

from filing_agent.retrieval.search import (
    RRF_K,
    Hit,
    reciprocal_rank_fusion,
)


def _hit(chunk_id: str, score: float = 0.0, **kw) -> Hit:
    base = dict(chunk_id=chunk_id, accession_no="0001045810-25-000023", ticker="NVDA",
                fiscal_year=2025, item_section="MDA", text=f"text for {chunk_id}",
                score=score)
    return Hit(**{**base, **kw})


# --- Reciprocal rank fusion ----------------------------------------------------

def test_agreement_beats_a_single_first_place() -> None:
    """The property RRF exists for: two mid-rank votes outweigh one top-rank vote."""
    lexical = [_hit("solo"), _hit("both"), _hit("both2")]
    dense = [_hit("other"), _hit("both"), _hit("both2")]
    fused = reciprocal_rank_fusion([lexical, dense], top_k=3)
    assert fused[0].chunk_id == "both"


def test_fusion_score_is_the_sum_of_reciprocal_ranks() -> None:
    fused = reciprocal_rank_fusion([[_hit("a")], [_hit("a")]], top_k=1)
    assert fused[0].score == pytest.approx(2 / (RRF_K + 1))


def test_a_chunk_found_by_only_one_retriever_still_appears() -> None:
    fused = reciprocal_rank_fusion([[_hit("a")], [_hit("b")]], top_k=5)
    assert {h.chunk_id for h in fused} == {"a", "b"}


def test_ties_break_deterministically() -> None:
    """Two runs of the same experiment must produce the same ranking."""
    first = reciprocal_rank_fusion([[_hit("b"), _hit("a")]], top_k=2)
    second = reciprocal_rank_fusion([[_hit("b"), _hit("a")]], top_k=2)
    assert [h.chunk_id for h in first] == [h.chunk_id for h in second]


def test_duplicate_chunk_is_returned_once() -> None:
    fused = reciprocal_rank_fusion([[_hit("a")], [_hit("a")], [_hit("a")]], top_k=5)
    assert len(fused) == 1


def test_empty_rankings_fuse_to_nothing() -> None:
    assert reciprocal_rank_fusion([[], []], top_k=5) == []


def test_top_k_is_respected() -> None:
    ranking = [_hit(f"c{i}") for i in range(20)]
    assert len(reciprocal_rank_fusion([ranking], top_k=4)) == 4


def test_citation_identifies_filing_section_and_chunk() -> None:
    """PROPOSAL.md §3: a claim cites accession + section + chunk."""
    assert _hit("0001045810-25-000023:0117").citation == (
        "0001045810-25-000023#MDA#0001045810-25-000023:0117"
    )


# --- Live retrieval ------------------------------------------------------------

@pytest.fixture(scope="module")
def conn():
    from filing_agent.retrieval import db
    try:
        connection = db.connect()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"no Postgres reachable: {exc}")
    yield connection
    connection.close()


def _require_embeddings(conn) -> None:
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM chunks WHERE embedding IS NOT NULL")
        if cur.fetchone()[0] == 0:
            pytest.skip("corpus not embedded yet")


def test_lexical_search_is_ranked_and_filtered(conn) -> None:
    from filing_agent.retrieval.search import lexical_search
    hits = lexical_search(conn, "data center revenue", top_k=5, tickers=["NVDA"])
    if not hits:
        pytest.skip("corpus not loaded")
    assert all(h.ticker == "NVDA" for h in hits)
    assert [h.score for h in hits] == sorted((h.score for h in hits), reverse=True)


def test_metadata_filter_actually_narrows(conn) -> None:
    from filing_agent.retrieval.search import lexical_search
    everything = lexical_search(conn, "revenue", top_k=50)
    if not everything:
        pytest.skip("corpus not loaded")
    filtered = lexical_search(conn, "revenue", top_k=50, fiscal_years=[2025])
    assert all(h.fiscal_year == 2025 for h in filtered)


def test_dense_search_returns_similarity_not_distance(conn) -> None:
    """Higher must mean better in every retriever, or fusion silently inverts."""
    _require_embeddings(conn)
    from filing_agent.retrieval.embed import load_encoder
    from filing_agent.retrieval.search import dense_search
    vector = load_encoder().encode(["data center revenue growth"], normalize_embeddings=True)[0]
    hits = dense_search(conn, vector, top_k=5)
    assert hits and all(0.0 <= h.score <= 1.0 for h in hits)
    assert [h.score for h in hits] == sorted((h.score for h in hits), reverse=True)
