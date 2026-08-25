"""Schema tests. Each asserts that an *invalid* memo cannot be constructed at all —
the point of the contract is that the model has no way to emit one.
"""

import datetime as dt

import pytest
from pydantic import ValidationError

from filing_agent.agent.schemas import Citation, Claim, Memo, Verification

ACC = "0001045810-25-000023"


def _citation(**kw) -> Citation:
    base = dict(accession_no=ACC, item_section="MDA", chunk_id=f"{ACC}:0117",
                quote_span="Revenue | $130,497 | $60,922 | Up 114%")
    return Citation(**{**base, **kw})


def _xbrl_ok(**kw) -> Verification:
    base = dict(method="xbrl", verified=True, concept="Revenues",
                period_end=dt.date(2025, 1, 26), expected_value=130_497_000_000.0)
    return Verification(**{**base, **kw})


def _claim(**kw) -> Claim:
    base = dict(text="NVIDIA revenue was $130.5 billion in FY2025.",
                value=130_497_000_000.0, unit="USD", period="FY2025",
                citation=_citation(), verification=_xbrl_ok())
    return Claim(**{**base, **kw})


def _memo(**kw) -> Memo:
    base = dict(answer_summary="Revenue grew 114%.", claims=[_claim()],
                trace_id="t-001")
    return Memo(**{**base, **kw})


# --- Citations must resolve --------------------------------------------------

def test_valid_citation_is_accepted() -> None:
    assert _citation().chunk_id.startswith(ACC)


def test_chunk_from_a_different_filing_is_rejected() -> None:
    """A citation pointing at another filing's chunk is fabricated, not merely wrong."""
    with pytest.raises(ValidationError, match="does not belong to filing"):
        _citation(chunk_id="0000320193-25-000079:0004")


def test_empty_quote_span_is_rejected() -> None:
    """The faithfulness checker (T3.5) has nothing to test without the span."""
    with pytest.raises(ValidationError):
        _citation(quote_span="")


# --- Verification must say what it checked -----------------------------------

def test_xbrl_verification_without_a_concept_is_rejected() -> None:
    with pytest.raises(ValidationError, match="requires concept and period_end"):
        Verification(method="xbrl", verified=True, period_end=dt.date(2025, 1, 26))


def test_xbrl_verification_without_a_period_is_rejected() -> None:
    with pytest.raises(ValidationError, match="requires concept and period_end"):
        Verification(method="xbrl", verified=True, concept="Revenues")


def test_unverified_cannot_claim_success() -> None:
    with pytest.raises(ValidationError, match="cannot report verified=True"):
        Verification(method="unverified", verified=True)


def test_failed_xbrl_check_is_representable() -> None:
    """§3 requires failure to be expressible — that is the whole point."""
    v = _xbrl_ok(verified=False, detail="reported 130,000 vs XBRL 130,497")
    assert v.verified is False and v.method == "xbrl"


# --- Numeric claims carry stricter obligations -------------------------------

def test_numeric_claim_without_a_unit_is_rejected() -> None:
    with pytest.raises(ValidationError, match="has no unit"):
        _claim(unit=None)


def test_numeric_claim_without_a_period_is_rejected() -> None:
    """$130bn is unverifiable until you say which period it belongs to."""
    with pytest.raises(ValidationError, match="has no period"):
        _claim(period=None)


def test_numeric_claim_cannot_rest_on_an_llm_judgement() -> None:
    """The project's premise is that numbers are checked programmatically, not judged."""
    with pytest.raises(ValidationError, match="cannot rest on an LLM judgement"):
        _claim(verification=Verification(method="judged", verified=True))


def test_numeric_claim_may_be_explicitly_unverified() -> None:
    """Allowed — and forces the disclosure rule below to engage."""
    claim = _claim(verification=Verification(method="unverified", verified=False))
    assert claim.is_numeric and not claim.verification.verified


def test_prose_claim_needs_no_unit_or_period() -> None:
    claim = _claim(value=None, unit=None, period=None,
                   verification=Verification(method="extractive", verified=True))
    assert not claim.is_numeric


def test_unit_without_a_value_is_rejected() -> None:
    with pytest.raises(ValidationError, match="has a unit but no value"):
        _claim(value=None, period=None,
               verification=Verification(method="extractive", verified=True))


# --- The §3 hard rule: never silent ------------------------------------------

def test_unverified_number_without_a_caveat_is_rejected() -> None:
    """The memo cannot present an unchecked figure with no note anywhere."""
    unverified = _claim(verification=Verification(method="unverified", verified=False))
    with pytest.raises(ValidationError, match="forbids emitting an unverified figure"):
        _memo(claims=[unverified], confidence_notes="")


def test_unverified_number_with_a_caveat_is_accepted() -> None:
    unverified = _claim(verification=Verification(method="unverified", verified=False))
    memo = _memo(claims=[unverified],
                 confidence_notes="Revenue could not be matched to an XBRL fact.")
    assert memo.unverified_numeric_claims


def test_fully_verified_memo_needs_no_caveat() -> None:
    assert _memo(confidence_notes="").verified_fraction == 1.0


def test_verified_fraction_counts_only_programmatic_methods() -> None:
    """An extractive text match is not proof of a number."""
    soft = _claim(verification=Verification(method="extractive", verified=True))
    memo = _memo(claims=[_claim(), soft])
    assert memo.verified_fraction == 0.5


def test_memo_with_no_numeric_claims_is_vacuously_verified() -> None:
    prose = _claim(value=None, unit=None, period=None,
                   verification=Verification(method="extractive", verified=True))
    assert _memo(claims=[prose]).verified_fraction == 1.0


def test_memo_requires_a_trace_id() -> None:
    """Every run must be traceable to its Langfuse span (T4.1)."""
    with pytest.raises(ValidationError):
        _memo(trace_id="")
