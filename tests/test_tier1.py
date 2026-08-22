"""Tier-1 eval-set tests. Grading is programmatic — no model in the loop."""

import datetime as dt

import pytest

from filing_agent.evals.tier1 import (
    EvalQuestion,
    Tier1Error,
    assert_questions_valid,
    build_tier1,
    read_tier1,
    select_frozen_set,
    write_tier1,
)
from filing_agent.ingest.xbrl import XbrlFact

ACC_2024, ACC_2025 = "0001045810-24-000029", "0001045810-25-000023"
YEARS = (2024, 2025)


def _fact(**kw) -> XbrlFact:
    base = dict(ticker="NVDA", cik=1045810, concept="NetIncomeLoss", unit="USD",
                value=72_880_000_000.0, period_start=dt.date(2024, 1, 29),
                period_end=dt.date(2025, 1, 26), accession_no=ACC_2025,
                form="10-K", filed_date=dt.date(2025, 2, 26))
    return XbrlFact(**{**base, **kw})


def _two_years() -> list[XbrlFact]:
    return [
        _fact(value=29_760_000_000.0, period_start=dt.date(2023, 1, 30),
              period_end=dt.date(2024, 1, 28), accession_no=ACC_2024),
        _fact(),
    ]


def _q(**kw) -> EvalQuestion:
    base = dict(question_id="t1-point-NVDA-NetIncomeLoss-2025", question="What was...?",
                question_type="point", tickers=("NVDA",), concept="NetIncomeLoss",
                fiscal_years=(2025,), expected_value=72_880_000_000.0, unit="USD",
                source_accessions=(ACC_2025,))
    return EvalQuestion(**{**base, **kw})


# --- Grading -------------------------------------------------------------------

def test_exact_answer_grades_correct() -> None:
    assert _q().grade(72_880_000_000.0) is True


def test_off_by_one_dollar_grades_incorrect() -> None:
    """Exact means exact: a 1e-6 relative tolerance would have allowed $72,880."""
    assert _q().grade(72_880_000_001.0) is False


def test_string_answers_are_compared_case_insensitively() -> None:
    assert _q(expected_value="NVDA", question_type="cross_ticker").grade("nvda") is True


def test_unparseable_answer_is_incorrect_not_an_error() -> None:
    assert _q().grade("I could not find this figure") is False


def test_percentage_accepts_any_answer_rounding_to_the_same_2dp() -> None:
    pct = _q(expected_value=28.50, unit="percent", abs_tolerance=0.005)
    assert pct.grade(28.5012) is True and pct.grade(28.51) is False


# --- Fact selection ------------------------------------------------------------

def test_restated_facts_generate_no_questions() -> None:
    """An agent reading the original filing would be marked wrong (D-0020)."""
    facts = [f.model_copy(update={"restated": True}) for f in _two_years()]
    assert build_tier1(facts, YEARS) == []


def test_quarterly_facts_are_excluded_from_annual_questions() -> None:
    quarterly = _fact(period_start=dt.date(2024, 10, 27), period_end=dt.date(2025, 1, 26))
    assert build_tier1([quarterly], YEARS) == []


def test_53_week_fiscal_year_is_still_annual() -> None:
    """Retail calendars run 52 or 53 weeks; the band must accept both."""
    long_year = _fact(period_start=dt.date(2024, 1, 29), period_end=dt.date(2025, 2, 2))
    assert len(build_tier1([long_year], (2025,))) == 1


def test_instant_facts_are_phrased_as_of_period_end() -> None:
    balance = _fact(concept="Assets", period_start=None, value=111_601_000_000.0)
    assert "as of the end of" in build_tier1([balance], YEARS)[0].question


# --- Derived questions ---------------------------------------------------------

def test_change_question_is_the_arithmetic_difference() -> None:
    change = [q for q in build_tier1(_two_years(), YEARS) if q.question_type == "yoy_change"]
    assert len(change) == 1
    assert change[0].expected_value == pytest.approx(43_120_000_000.0)


def test_percentage_question_uses_the_earlier_year_as_base() -> None:
    pct = [q for q in build_tier1(_two_years(), YEARS) if q.question_type == "yoy_pct"][0]
    assert pct.expected_value == pytest.approx(43_120 / 29_760 * 100, abs=0.005)


def test_derived_answers_carry_no_binary_float_dust() -> None:
    """7.09 - 5.71 must be 1.38, not 1.3800000000000008 (Decimal arithmetic)."""
    facts = [_fact(concept="EarningsPerShareDiluted", unit="USD/shares", value=5.71,
                   period_start=dt.date(2023, 1, 30), period_end=dt.date(2024, 1, 28),
                   accession_no=ACC_2024),
             _fact(concept="EarningsPerShareDiluted", unit="USD/shares", value=7.09)]
    change = [q for q in build_tier1(facts, YEARS) if q.question_type == "yoy_change"][0]
    assert repr(change.expected_value) == "1.38"
    assert change.grade(1.38) is True


def test_derived_questions_cite_both_source_filings() -> None:
    change = [q for q in build_tier1(_two_years(), YEARS) if q.question_type == "yoy_change"][0]
    assert set(change.source_accessions) == {ACC_2024, ACC_2025}


def test_zero_base_produces_no_percentage_question() -> None:
    facts = _two_years()
    facts[0] = facts[0].model_copy(update={"value": 0.0})
    assert not [q for q in build_tier1(facts, YEARS) if q.question_type == "yoy_pct"]


# --- Frozen-set selection (D-0003) ---------------------------------------------

def test_selection_is_deterministic() -> None:
    pool = build_tier1(_two_years(), YEARS)
    assert [q.question_id for q in select_frozen_set(pool)] == \
           [q.question_id for q in select_frozen_set(list(reversed(pool)))]


def test_selection_is_balanced_across_tickers_and_types() -> None:
    facts = _two_years() + [f.model_copy(update={"ticker": "AAPL", "cik": 320193})
                            for f in _two_years()]
    chosen = select_frozen_set(build_tier1(facts, YEARS), per_stratum=1)
    assert {q.tickers[0] for q in chosen} == {"NVDA", "AAPL"}
    assert len({q.question_type for q in chosen}) == 3


# --- Structural assertions -----------------------------------------------------

def test_duplicate_question_id_is_rejected() -> None:
    with pytest.raises(Tier1Error, match="duplicate question_id"):
        assert_questions_valid([_q(), _q()], {ACC_2025})


def test_uncitable_source_is_rejected() -> None:
    """Every answer must point at a filing in the corpus."""
    with pytest.raises(Tier1Error, match="uncitable source"):
        assert_questions_valid([_q()], {"0000000000-00-000000"})


def test_question_that_cannot_grade_its_own_answer_is_rejected() -> None:
    with pytest.raises(Tier1Error, match="does not grade its own answer"):
        assert_questions_valid([_q(rel_tolerance=-1.0, abs_tolerance=-1.0)], {ACC_2025})


def test_round_trip_preserves_questions(tmp_path) -> None:
    pool = build_tier1(_two_years(), YEARS)
    path = write_tier1(pool, tmp_path / "t1.jsonl")
    assert [q.model_dump() for q in read_tier1(path)] == \
           [q.model_dump() for q in sorted(pool, key=lambda q: q.question_id)]
