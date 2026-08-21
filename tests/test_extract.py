"""Extraction tests. Each mirrors a failure observed on the real NVDA FY2025 10-K."""

import pytest

from filing_agent.ingest.extract import (
    ExtractionError,
    assert_extraction_plausible,
    assert_no_xbrl_metadata,
    extract_text,
)

IXBRL_HEADER = """
<ix:header><ix:hidden>
  <ix:nonNumeric contextRef="c-1">0001045810 us-gaap:FairValueInputsLevel2Member</ix:nonNumeric>
</ix:hidden>
<ix:resources><xbrli:context id="c-1"><xbrli:entity>0001045810</xbrli:entity></xbrli:context>
</ix:resources></ix:header>
"""


def test_ixbrl_header_is_dropped_entirely() -> None:
    """Failure 1: naive stripping made this 33% of extracted text."""
    html = f"<html><body>{IXBRL_HEADER}<p>Item 7. MD&amp;A</p></body></html>"
    text = extract_text(html)
    assert "us-gaap:" not in text
    assert "0001045810" not in text
    assert "Item 7. MD&A" in text


def test_unrelated_nested_tag_does_not_end_the_skip_early() -> None:
    """A <div> inside <ix:header> must not be mistaken for the header's own close."""
    html = f"<html><ix:header><div><span>0001045810 dei:EntityCentralIndexKey</span></div>" \
           f"</ix:header><p>Revenue was $130.5 billion.</p></html>"
    text = extract_text(html)
    assert "dei:" not in text and "0001045810" not in text
    assert "Revenue was $130.5 billion." in text


def test_script_and_style_are_dropped() -> None:
    html = "<html><style>.x{color:red}</style><script>var a=1;</script><p>Net income</p></html>"
    assert extract_text(html) == "Net income"


# --- Table geometry (failure 2) ------------------------------------------------

def test_table_row_survives_as_one_line() -> None:
    """Failure 2: a severed row loses the label that makes the figure meaningful."""
    html = """<table><tr><td>Revenue</td><td>130,497</td><td>60,922</td></tr>
              <tr><td>Cost of revenue</td><td>32,639</td><td>16,621</td></tr></table>"""
    lines = extract_text(html).split("\n")
    assert lines == ["Revenue | 130,497 | 60,922", "Cost of revenue | 32,639 | 16,621"]


def test_figure_is_never_separated_from_its_label() -> None:
    html = "<table><tr><td>Gross profit</td><td>97,858</td></tr></table>"
    line = extract_text(html).split("\n")[0]
    assert "Gross profit" in line and "97,858" in line


def test_layout_spacer_cells_are_dropped() -> None:
    """Real SEC rows carry empty cells for visual alignment."""
    html = ("<table><tr><td>Gross profit</td><td>97,858</td><td></td><td></td>"
            "<td>44,301</td><td></td><td></td><td>15,356</td></tr></table>")
    assert extract_text(html) == "Gross profit | 97,858 | 44,301 | 15,356"


def test_orphaned_currency_symbol_is_reattached() -> None:
    html = ("<table><tr><td>Net income</td><td>$</td><td>72,880</td>"
            "<td></td><td>$</td><td>29,760</td></tr></table>")
    assert extract_text(html) == "Net income | $72,880 | $29,760"


def test_em_dash_nulls_are_preserved() -> None:
    """Genuine nulls render as em dashes, so dropping empty cells loses nothing."""
    html = "<table><tr><td>Net income</td><td>—</td><td></td><td>4,368</td></tr></table>"
    assert extract_text(html) == "Net income | — | 4,368"


def test_block_elements_become_separate_lines() -> None:
    html = "<div><p>Item 1A. Risk Factors</p><p>Our business is subject to risks.</p></div>"
    assert extract_text(html).split("\n") == [
        "Item 1A. Risk Factors", "Our business is subject to risks."
    ]


def test_nbsp_and_entities_are_normalised() -> None:
    assert extract_text("<p>Item&#160;7.&nbsp;MD&amp;A</p>") == "Item 7. MD&A"


# --- Assertions (D-0007) -------------------------------------------------------

def test_metadata_assertion_flags_leaked_prefixes() -> None:
    with pytest.raises(ExtractionError, match="us-gaap:"):
        assert_no_xbrl_metadata("Revenue us-gaap:RevenueFromContract 130,497", "nvda.htm")


def test_metadata_assertion_passes_on_clean_text() -> None:
    assert_no_xbrl_metadata("Revenue | 130,497 | 60,922")


def test_plausibility_assertion_catches_a_parser_that_bailed() -> None:
    with pytest.raises(ExtractionError, match="parser likely failed"):
        assert_extraction_plausible("x", "y" * 100_000, "nvda.htm")


def test_plausibility_assertion_passes_on_normal_output() -> None:
    assert_extraction_plausible("a" * 5_000, "b" * 100_000)
