"""Section-detection tests. Each encodes a real filing's structure or a real bug."""

import pytest

from filing_agent.ingest.sections import (
    FINANCIAL_STATEMENTS,
    MARKET_RISK,
    MDA,
    REQUIRED_10K,
    RISK_FACTORS,
    Section,
    SectionError,
    assert_sections_ordered,
    assert_sections_substantive,
    find_sections,
)


def _doc(body: list[str]) -> list[str]:
    """A filing: table of contents (bare items) followed by the body (titled items)."""
    toc = ["Part I", "Item 1.", "Item 1A.", "Item 7.", "Item 7A.", "Item 8."]
    return toc + body


BODY = [
    "Item 1. Business",
    *["We design and sell accelerated computing platforms." for _ in range(20)],
    "Item 1A. Risk Factors",
    *["Our business is subject to substantial risks." for _ in range(40)],
    "Item 1B. Unresolved Staff Comments",
    "None.",
    "Item 7. Management's Discussion and Analysis of Financial Condition",
    *["Revenue increased 114% driven by Data Center." for _ in range(30)],
    "Item 7A. Quantitative and Qualitative Disclosures about Market Risk",
    *["We are exposed to interest rate risk." for _ in range(15)],
    "Item 8. Financial Statements and Supplementary Data",
    *["Revenue | $130,497 | $60,922" for _ in range(25)],
]


def _by_name(sections: list[Section]) -> dict[str, Section]:
    return {s.name: s for s in sections}


# --- TOC vs body ---------------------------------------------------------------

def test_bare_toc_entries_are_not_chosen_as_sections() -> None:
    """TOC items are bare ('Item 1A.'); the title lands in a separate table cell."""
    sections = _by_name(find_sections(_doc(BODY)))
    assert sections[RISK_FACTORS].start_line > 5  # past the TOC block
    assert sections[RISK_FACTORS].heading == "Item 1A. Risk Factors"


def test_body_occurrence_wins_on_span_even_without_toc_detection() -> None:
    """No TOC heuristic: the occurrence owning the most content simply wins."""
    doubled = _doc(BODY[:2] + BODY)  # an extra early mention of Item 1
    assert find_sections(doubled)[0].n_lines > 5


# --- Span boundaries -----------------------------------------------------------

def test_section_ends_at_next_item_not_next_tracked_section() -> None:
    """RISK_FACTORS must stop at Item 1B, not swallow Items 1B-6 up to Item 7."""
    sections = _by_name(find_sections(_doc(BODY)))
    risk = sections[RISK_FACTORS]
    assert risk.n_lines == 41  # heading + 40 body lines, ending at Item 1B
    assert risk.end_line < sections[MDA].start_line


def test_sections_do_not_overlap() -> None:
    assert_sections_ordered(find_sections(_doc(BODY)))


def test_overlapping_sections_are_rejected() -> None:
    bad = [
        Section(name=MDA, start_line=10, end_line=90, heading="Item 7. MD&A", item="7"),
        Section(name=MARKET_RISK, start_line=50, end_line=99, heading="Item 7A. Q", item="7A"),
    ]
    with pytest.raises(SectionError, match="overlaps"):
        assert_sections_ordered(bad, "filing")


# --- Real-filing formatting quirks --------------------------------------------

def test_long_heading_with_parenthetical_is_still_a_heading() -> None:
    """Regression: COST's real MD&A heading is 171 chars; a 160-char cap dropped it."""
    body = list(BODY)
    body[body.index("Item 7. Management's Discussion and Analysis of Financial Condition")] = (
        "Item 7—Management's Discussion and Analysis of Financial Condition and Results "
        "of Operations (amounts in millions, except per share, share, percentages and "
        "warehouse count data)"
    )
    sections = _by_name(find_sections(_doc(body)))
    assert MDA in sections and sections[MDA].n_lines > 12


def test_em_dash_separator_is_accepted() -> None:
    sections = _by_name(find_sections(_doc(
        ["Item 1A—Risk Factors", *["risk" for _ in range(30)]]
    )))
    assert sections[RISK_FACTORS].item == "1A"


# --- 10-Q Part disambiguation --------------------------------------------------

def test_10q_item_1_is_financial_statements_not_legal_proceedings() -> None:
    """A 10-Q reuses item numbers: Part I Item 1 is financials, Part II Item 1 is legal."""
    lines = [
        "Part I. Financial Information",
        "Item 1. Financial Statements",
        *["Revenue | $44,062 | $26,044" for _ in range(30)],
        "Item 2. Management's Discussion and Analysis",
        *["Revenue increased." for _ in range(20)],
        "Part II. Other Information",
        "Item 1. Legal Proceedings",
        "See Note 12.",
        "Item 1A. Risk Factors",
        *["Risks described in our Annual Report." for _ in range(15)],
    ]
    sections = _by_name(find_sections(lines, form="10-Q"))
    assert sections[FINANCIAL_STATEMENTS].heading == "Item 1. Financial Statements"
    assert sections[RISK_FACTORS].item == "1A"
    assert MDA in sections


# --- Substance assertion (D-0016) ---------------------------------------------

def test_substantive_sections_pass() -> None:
    assert_sections_substantive(find_sections(_doc(BODY)), REQUIRED_10K, "NVDA")


def test_stub_section_hard_fails() -> None:
    """JPM's Item 7 owns 2 lines: 'Refer to pages 133-142.'"""
    lines = _doc([
        "Item 1A. Risk Factors", *["risk" for _ in range(30)],
        "Item 7. Management's Discussion and Analysis",
        "Refer to Management's discussion and analysis on pages 133-142.",
        "Item 7A. Quantitative and Qualitative Disclosures about Market Risk",
        *["market risk" for _ in range(20)],
        "Item 8. Financial Statements and Supplementary Data", *["stmt" for _ in range(20)],
    ])
    with pytest.raises(SectionError, match="incorporated by reference"):
        assert_sections_substantive(find_sections(lines), REQUIRED_10K, "JPM")


def test_allowlisted_stub_passes() -> None:
    """Known by-reference filings stay visible in config rather than silently passing."""
    lines = _doc([
        "Item 1A. Risk Factors", *["risk" for _ in range(30)],
        "Item 7. Management's Discussion and Analysis", "Refer to pages 133-142.",
        "Item 7A. Quantitative and Qualitative Disclosures about Market Risk",
        *["market risk" for _ in range(20)],
        "Item 8. Financial Statements and Supplementary Data", *["stmt" for _ in range(20)],
    ])
    assert_sections_substantive(
        find_sections(lines), REQUIRED_10K, "JPM", allowed_stubs=frozenset({MDA})
    )


def test_missing_section_hard_fails() -> None:
    lines = _doc(["Item 1A. Risk Factors", *["risk" for _ in range(30)]])
    with pytest.raises(SectionError, match="not found"):
        assert_sections_substantive(find_sections(lines), REQUIRED_10K, "ACME")
