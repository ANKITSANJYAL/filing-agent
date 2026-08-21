"""Chunker tests. The load-bearing ones are table integrity and section exactness."""

import datetime as dt

import pytest

from filing_agent.ingest.chunker import (
    MAX_CHARS,
    TARGET_CHARS,
    UNSECTIONED,
    ChunkError,
    assert_chunks_valid,
    chunk_filing,
    section_spans,
)
from filing_agent.ingest.sections import MDA, RISK_FACTORS, Section

META = dict(
    accession_no="0001045810-25-000023", ticker="NVDA", cik=1045810,
    form_type="10-K", fiscal_year=2025, fiscal_period=dt.date(2025, 1, 26),
    filing_date=dt.date(2025, 2, 26),
)


def _sections() -> list[Section]:
    return [
        Section(name=RISK_FACTORS, start_line=2, end_line=6, heading="Item 1A.", item="1A"),
        Section(name=MDA, start_line=6, end_line=10, heading="Item 7.", item="7"),
    ]


# --- Span tiling ---------------------------------------------------------------

def test_gaps_between_sections_become_unsectioned() -> None:
    spans = section_spans(_sections(), 14)
    assert spans == [
        (UNSECTIONED, 0, 2), (RISK_FACTORS, 2, 6), (MDA, 6, 10), (UNSECTIONED, 10, 14)
    ]


def test_whole_document_is_covered() -> None:
    """NVDA's financial statements sit outside Item 8; they must still be chunked."""
    spans = section_spans(_sections(), 14)
    covered = [i for _, lo, hi in spans for i in range(lo, hi)]
    assert covered == list(range(14))


def test_document_with_no_sections_is_still_chunked() -> None:
    chunks = chunk_filing(["some text"] * 5, [], **META)
    assert len(chunks) == 1 and chunks[0].item_section == UNSECTIONED


# --- Table integrity (the T1.2a payoff) ----------------------------------------

def test_table_row_is_never_split_across_chunks() -> None:
    rows = [f"Line item {i} | {i * 1000:,} | {i * 2000:,}" for i in range(400)]
    chunks = chunk_filing(rows, [], **META)
    assert len(chunks) > 1  # genuinely split into several chunks
    rejoined = "\n".join(c.text for c in chunks)
    for row in rows:
        assert row in rejoined, "a row was severed"


def test_table_run_may_overflow_target_to_stay_whole() -> None:
    """A table is not sliced merely to hit a size target."""
    long_row = "Revenue | " + " | ".join(str(n) for n in range(60))
    rows = [long_row] * 12
    chunks = chunk_filing(rows, [], **META)
    assert any(c.n_chars > TARGET_CHARS for c in chunks)
    assert all(c.n_chars <= MAX_CHARS or c.end_line - c.start_line == 1 for c in chunks)


def test_single_oversize_row_is_kept_whole() -> None:
    """Splitting an 800-char row is worse than an oversize chunk."""
    monster = "Item | " + " | ".join(str(n) for n in range(2000))
    chunks = chunk_filing([monster], [], **META)
    assert len(chunks) == 1 and chunks[0].text == monster
    assert_chunks_valid(chunks, [monster])  # oversize single-line chunk is legal


# --- Section exactness ---------------------------------------------------------

def test_chunk_never_spans_a_section_boundary() -> None:
    lines = ["front"] * 2 + ["risk"] * 4 + ["mda"] * 4 + ["back"] * 4
    for chunk in chunk_filing(lines, _sections(), **META):
        assert {"front": UNSECTIONED, "risk": RISK_FACTORS,
                "mda": MDA, "back": UNSECTIONED}[lines[chunk.start_line]] == chunk.item_section


def test_metadata_matches_proposal_section_4_1() -> None:
    chunk = chunk_filing(["text"], [], **META)[0]
    for field in ("ticker", "cik", "form_type", "fiscal_period",
                  "filing_date", "item_section", "accession_no"):
        assert getattr(chunk, field) is not None
    assert chunk.fiscal_period == dt.date(2025, 1, 26)  # period end, not filing date


def test_chunk_ids_are_unique_and_traceable_to_the_filing() -> None:
    chunks = chunk_filing([f"paragraph {i} " * 60 for i in range(20)], [], **META)
    ids = [c.chunk_id for c in chunks]
    assert len(set(ids)) == len(ids)
    assert all(i.startswith("0001045810-25-000023:") for i in ids)


def test_blank_spans_produce_no_empty_chunks() -> None:
    assert chunk_filing(["", "   ", ""], [], **META) == []


# --- Assertions ----------------------------------------------------------------

def test_valid_chunks_pass() -> None:
    lines = [f"sentence number {i} with some content" for i in range(200)]
    assert_chunks_valid(chunk_filing(lines, [], **META), lines)


def test_overlapping_chunks_are_rejected() -> None:
    lines = ["a"] * 10
    chunks = chunk_filing(lines, [], **META)
    chunks.append(chunks[0].model_copy(update={"chunk_id": "x:9999", "start_line": 0}))
    with pytest.raises(ChunkError, match="overlaps"):
        assert_chunks_valid(chunks, lines)


def test_duplicate_chunk_id_is_rejected() -> None:
    lines = ["a"] * 4
    chunks = chunk_filing(lines, [], **META)
    chunks.append(chunks[0].model_copy())
    with pytest.raises(ChunkError, match="duplicate chunk_id"):
        assert_chunks_valid(chunks, lines)
