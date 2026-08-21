"""XBRL tests. The load-bearing ones are traceability and restatement resolution —
both guard the claim that every numeric answer has exactly one correct value.
"""

import datetime as dt
import json

import pytest

from filing_agent.ingest.xbrl import (
    CORE_CONCEPTS,
    REVENUE_CONCEPT_BY_TICKER,
    XbrlError,
    XbrlFact,
    assert_concept_coverage,
    assert_facts_traceable,
    assert_no_conflicting_values,
    concepts_for,
    load_facts,
    resolve_restatements,
)

IN_CORPUS = "0001045810-25-000023"
OUT_OF_CORPUS = "0001045810-19-000001"


def _companyfacts(tmp_path, observations, concept="NetIncomeLoss"):
    payload = {
        "cik": 1045810, "entityName": "NVIDIA CORPORATION",
        "facts": {"us-gaap": {concept: {"units": {"USD": observations}}}},
    }
    path = tmp_path / "CIK0001045810.json"
    path.write_text(json.dumps(payload))
    return path


def _obs(val, accn=IN_CORPUS, start="2024-01-29", end="2025-01-26", filed="2025-02-26"):
    return {"start": start, "end": end, "val": val, "accn": accn,
            "form": "10-K", "filed": filed}


def _fact(**kw) -> XbrlFact:
    base = dict(ticker="NVDA", cik=1045810, concept="EarningsPerShareDiluted", unit="USD",
                value=1.19, period_start=dt.date(2023, 1, 30), period_end=dt.date(2024, 1, 28),
                accession_no=IN_CORPUS, form="10-K", filed_date=dt.date(2025, 2, 26))
    return XbrlFact(**{**base, **kw})


# --- Traceability: only facts from filings we hold ------------------------------

def test_facts_from_filings_outside_the_corpus_are_dropped(tmp_path) -> None:
    """A fact we cannot cite is not ground truth for us."""
    path = _companyfacts(tmp_path, [_obs(1.0), _obs(2.0, accn=OUT_OF_CORPUS)])
    facts = load_facts(path, "NVDA", {IN_CORPUS}, ("NetIncomeLoss",))
    assert [f.value for f in facts] == [1.0]


def test_untracked_concepts_are_ignored(tmp_path) -> None:
    path = _companyfacts(tmp_path, [_obs(1.0)], concept="SomeExoticConcept")
    assert load_facts(path, "NVDA", {IN_CORPUS}, ("NetIncomeLoss",)) == []


def test_instant_facts_have_no_period_start(tmp_path) -> None:
    """Balance-sheet items are instants; income-statement items are durations."""
    obs = {"end": "2025-01-26", "val": 111_601_000_000, "accn": IN_CORPUS,
           "form": "10-K", "filed": "2025-02-26"}
    fact = load_facts(_companyfacts(tmp_path, [obs], "Assets"), "NVDA",
                      {IN_CORPUS}, ("Assets",))[0]
    assert fact.period_start is None and fact.is_duration is False


def test_orphan_accession_is_rejected() -> None:
    with pytest.raises(XbrlError, match="absent from the corpus manifest"):
        assert_facts_traceable([_fact(accession_no=OUT_OF_CORPUS)], {IN_CORPUS})


# --- Restatement: NVIDIA's 10-for-1 split ---------------------------------------

def test_conflicting_values_hard_fail() -> None:
    """Pre- and post-split EPS for the same period: 11.93 vs 1.19."""
    pair = [_fact(value=11.93, accession_no="A", filed_date=dt.date(2024, 2, 21)),
            _fact(value=1.19, accession_no="B", filed_date=dt.date(2025, 2, 26))]
    with pytest.raises(XbrlError, match="restated/conflicting"):
        assert_no_conflicting_values(pair)


def test_allowlisted_restatement_passes() -> None:
    pair = [_fact(value=11.93, accession_no="A", filed_date=dt.date(2024, 2, 21)),
            _fact(value=1.19, accession_no="B", filed_date=dt.date(2025, 2, 26))]
    assert_no_conflicting_values(
        pair, allowed=frozenset({("NVDA", "EarningsPerShareDiluted")})
    )


def test_resolution_keeps_the_most_recently_filed_value() -> None:
    """A split is a real economic event; the adjusted figure is today's basis."""
    pair = [_fact(value=11.93, accession_no="A", filed_date=dt.date(2024, 2, 21)),
            _fact(value=1.19, accession_no="B", filed_date=dt.date(2025, 2, 26))]
    resolved = resolve_restatements(pair)
    assert len(resolved) == 1
    assert resolved[0].value == 1.19 and resolved[0].restated is True


def test_agreeing_duplicates_are_deduped_without_a_restated_flag() -> None:
    """The same figure repeated as a comparative is not a restatement."""
    pair = [_fact(accession_no="A", filed_date=dt.date(2024, 2, 21)),
            _fact(accession_no="B", filed_date=dt.date(2025, 2, 26))]
    resolved = resolve_restatements(pair)
    assert len(resolved) == 1 and resolved[0].restated is False


def test_resolved_facts_have_exactly_one_value_per_period() -> None:
    """D-0006: one numeric question, exactly one answer."""
    facts = [_fact(value=v, accession_no=a, filed_date=dt.date(2024 + i, 2, 21))
             for i, (v, a) in enumerate([(11.93, "A"), (1.19, "B")])]
    assert_no_conflicting_values(resolve_restatements(facts))


def test_different_periods_are_not_collapsed() -> None:
    facts = [_fact(period_end=dt.date(2024, 1, 28)), _fact(period_end=dt.date(2025, 1, 26))]
    assert len(resolve_restatements(facts)) == 2


# --- Concept mapping (PROPOSAL.md §9 risk) --------------------------------------

def test_every_ticker_has_a_revenue_concept() -> None:
    """No revenue tag is common to all eight, so each ticker needs an explicit one."""
    assert set(REVENUE_CONCEPT_BY_TICKER) == {
        "NVDA", "AAPL", "MSFT", "JPM", "XOM", "PFE", "WMT", "COST"
    }
    assert REVENUE_CONCEPT_BY_TICKER["JPM"] == "RevenuesNetOfInterestExpense"


def test_concepts_for_appends_the_ticker_revenue_tag() -> None:
    concepts = concepts_for("AAPL")
    assert concepts[:-1] == CORE_CONCEPTS
    assert concepts[-1] == "RevenueFromContractWithCustomerExcludingAssessedTax"


def test_gross_profit_is_not_in_the_core_set() -> None:
    """PROPOSAL.md §9 suggests it, but only 4 of 8 tickers report it — banks have none."""
    assert "GrossProfit" not in CORE_CONCEPTS


# --- Completeness (D-0009) ------------------------------------------------------

def test_missing_concept_hard_fails() -> None:
    with pytest.raises(XbrlError, match="no facts"):
        assert_concept_coverage([_fact()], ("NVDA",), ("NetIncomeLoss",))


def test_empty_fact_set_fails_coverage() -> None:
    """Correctness checks pass vacuously on an empty set; completeness must not."""
    assert_no_conflicting_values([])
    with pytest.raises(XbrlError):
        assert_concept_coverage([], ("NVDA",), ("Assets",))
