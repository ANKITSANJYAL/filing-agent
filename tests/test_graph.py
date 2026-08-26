"""Agent graph tests.

The parts worth testing hardest are the ones that do NOT involve the model: citation
construction, verification, and the re-plan bound. Those are the guarantees; the LLM
call is the part we cannot assert about.
"""

import pytest

from filing_agent.agent.graph import (
    MAX_REPLANS,
    DraftClaim,
    DraftMemo,
    build_claims,
    bump_replan,
    needs_replan,
    verify_claim,
)
from filing_agent.agent.schemas import Memo, Verification
from filing_agent.retrieval.search import Hit

ACC = "0001045810-25-000023"
CHUNK_TEXT = "Revenue | $130,497 | $60,922 | Up 114%\nData Center revenue grew strongly."


def _hit(**kw) -> Hit:
    base = dict(chunk_id=f"{ACC}:0117", accession_no=ACC, ticker="NVDA",
                fiscal_year=2025, item_section="MDA", text=CHUNK_TEXT, score=0.9)
    return Hit(**{**base, **kw})


def _draft(**kw) -> DraftClaim:
    base = dict(text="NVIDIA revenue grew 114%.", source_index=0,
                quote="Revenue | $130,497 | $60,922 | Up 114%")
    return DraftClaim(**{**base, **kw})


@pytest.fixture(scope="module")
def conn():
    from filing_agent.retrieval import db
    try:
        connection = db.connect()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"no Postgres: {exc}")
    yield connection
    connection.close()


# --- Citations are built, never accepted ---------------------------------------

def test_citation_is_constructed_from_the_index_not_the_model(conn) -> None:
    """The model supplies an index; the chunk_id comes from our own hit list."""
    claims, dropped = build_claims(
        DraftMemo(answer_summary="s", claims=[_draft()]), [_hit()], conn)
    assert dropped == 0
    assert claims[0].citation.chunk_id == f"{ACC}:0117"
    assert claims[0].citation.accession_no == ACC


def test_out_of_range_source_index_is_dropped(conn) -> None:
    """A hallucinated index cannot become a citation to something else."""
    _, dropped = build_claims(
        DraftMemo(answer_summary="s", claims=[_draft(source_index=99)]), [_hit()], conn)
    assert dropped == 1


def test_quote_not_present_in_the_cited_chunk_is_dropped(conn) -> None:
    """A paraphrase is not a quote; an unsubstantiable citation is worse than none."""
    fabricated = _draft(quote="Revenue was approximately one hundred billion dollars")
    claims, dropped = build_claims(
        DraftMemo(answer_summary="s", claims=[fabricated]), [_hit()], conn)
    assert claims == [] and dropped == 1


def test_empty_quote_is_dropped(conn) -> None:
    _, dropped = build_claims(
        DraftMemo(answer_summary="s", claims=[_draft(quote="   ")]), [_hit()], conn)
    assert dropped == 1


def test_claim_violating_the_section3_contract_is_dropped(conn) -> None:
    """A figure with no unit cannot be emitted, so it is dropped rather than repaired."""
    bad = _draft(value=130_497_000_000.0, unit=None, period="FY2025", concept="revenue")
    claims, dropped = build_claims(
        DraftMemo(answer_summary="s", claims=[bad]), [_hit()], conn)
    assert claims == [] and dropped == 1


# --- Verification runs in code, not in the model -------------------------------

def test_prose_claim_gets_an_extractive_verification(conn) -> None:
    v = verify_claim(_draft(), _hit(), conn)
    assert v.method == "extractive" and v.verified


def test_numeric_claim_without_a_concept_is_unverified(conn) -> None:
    v = verify_claim(_draft(value=1.0, unit="USD", period="FY2025"), _hit(), conn)
    assert v.method == "unverified" and not v.verified


def test_correct_figure_verifies_against_xbrl(conn) -> None:
    with conn.cursor() as cur:
        # The ANNUAL fact: anchored to the 10-K's own period end, not the calendar
        # year (which also contains 10-Q periods). See D-0032.
        cur.execute(
            "SELECT f.value FROM xbrl_facts f JOIN filings fl"
            " ON fl.ticker=f.ticker AND fl.form_type='10-K' AND fl.fiscal_year=2025"
            " WHERE f.ticker='NVDA' AND f.concept='NetIncomeLoss'"
            "   AND f.period_end = fl.fiscal_period")
        row = cur.fetchone()
    if row is None:
        pytest.skip("no NVDA facts loaded")
    item = _draft(value=float(row[0]), unit="USD", period="FY2025", concept="net income")
    v = verify_claim(item, _hit(), conn)
    assert v.method == "xbrl" and v.verified and v.expected_value == pytest.approx(row[0])


def test_wrong_figure_fails_verification_with_both_numbers(conn) -> None:
    """The detail must name what was claimed and what XBRL says, for the re-plan hint."""
    item = _draft(value=1.0, unit="USD", period="FY2025", concept="net income")
    v = verify_claim(item, _hit(), conn)
    if v.method != "xbrl":
        pytest.skip("no NVDA facts loaded")
    assert not v.verified and "vs XBRL" in v.detail


# --- The re-plan bound (§4.3) --------------------------------------------------

def _memo(**kw) -> Memo:
    base = dict(answer_summary="s", claims=[], confidence_notes="", trace_id="t")
    return Memo(**{**base, **kw})


def test_empty_retrieval_triggers_a_replan() -> None:
    assert needs_replan({"hits": [], "replans": 0}) == "replan"


def test_unverified_figure_triggers_a_replan() -> None:
    from filing_agent.agent.schemas import Citation, Claim
    unverified = Claim(
        text="x", value=1.0, unit="USD", period="FY2025",
        citation=Citation(accession_no=ACC, item_section="MDA",
                          chunk_id=f"{ACC}:0117", quote_span="q"),
        verification=Verification(method="unverified", verified=False),
    )
    memo = _memo(claims=[unverified], confidence_notes="unverified")
    assert needs_replan({"hits": [_hit()], "memo": memo, "replans": 0}) == "replan"


def test_clean_memo_finishes() -> None:
    assert needs_replan({"hits": [_hit()], "memo": _memo(), "replans": 0}) == "done"


def test_replans_are_bounded() -> None:
    """An agent that loops forever on an unanswerable question is a cost incident."""
    state = {"hits": [], "replans": MAX_REPLANS}
    assert needs_replan(state) == "done"


def test_bump_replan_records_why() -> None:
    """The reason is fed back to the planner, so it must be specific."""
    state = bump_replan({"hits": [], "replans": 0, "notes": []})
    assert state["replans"] == 1
    assert "retrieval returned nothing" in state["notes"][-1]


# --- Comparatives: the claim's period, not the filing's year (D-0033) ----------

def test_claim_period_overrides_the_chunk_year() -> None:
    """A FY2024 figure quoted from the FY2025 filing must verify against FY2024."""
    from filing_agent.agent.graph import claim_fiscal_year
    item = _draft(value=6.08, unit="USD/shares", period="FY2024", concept="diluted eps")
    assert claim_fiscal_year(item, _hit(fiscal_year=2025)) == 2024


def test_various_period_phrasings_are_understood() -> None:
    from filing_agent.agent.graph import claim_fiscal_year
    for phrasing in ("FY2024", "fiscal year 2024", "2024", "year ended Sept 2024"):
        item = _draft(period=phrasing)
        assert claim_fiscal_year(item, _hit(fiscal_year=2025)) == 2024


def test_missing_period_falls_back_to_the_chunk_year() -> None:
    from filing_agent.agent.graph import claim_fiscal_year
    assert claim_fiscal_year(_draft(period=None), _hit(fiscal_year=2025)) == 2025


def test_out_of_corpus_year_falls_back_rather_than_querying_it() -> None:
    from filing_agent.agent.graph import claim_fiscal_year
    assert claim_fiscal_year(_draft(period="FY2019"), _hit(fiscal_year=2025)) == 2025
