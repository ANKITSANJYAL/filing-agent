"""Splits a filing into retrievable chunks carrying the metadata from PROPOSAL.md §4.1.

Three properties the rest of the system depends on:

1. **A table row is never split.** T1.2a emits one row per line, so chunking at line
   granularity makes this structural rather than best-effort — a figure can never be
   separated from its label.
2. **A chunk never spans a section boundary**, so `item_section` is exact rather than
   approximate.
3. **The whole document is chunked**, not just detected sections. Lines outside any
   section become `UNSECTIONED`, which keeps NVDA's financial statements retrievable
   even though they sit outside Item 8 (D-0016).
"""

from __future__ import annotations

import datetime as dt
from typing import Final

from pydantic import BaseModel

from .sections import Section

UNSECTIONED: Final[str] = "UNSECTIONED"

# Size proxy. `tiktoken` is the wrong tokenizer for Claude and would mis-size every
# chunk; the right tool is Anthropic's `count_tokens` endpoint. Until an API key is
# available we size in characters at ~4 chars/token and calibrate later against
# count_tokens on a sample of real chunks (D-0017).
CHARS_PER_TOKEN: Final[int] = 4
TARGET_TOKENS: Final[int] = 512
MAX_TOKENS: Final[int] = 768  # headroom so a table run can finish inside one chunk

TARGET_CHARS: Final[int] = TARGET_TOKENS * CHARS_PER_TOKEN
MAX_CHARS: Final[int] = MAX_TOKENS * CHARS_PER_TOKEN

CELL_SEP: Final[str] = " | "


class ChunkError(AssertionError):
    """Chunks failed a structural expectation (D-0007)."""


class Chunk(BaseModel):
    """One retrievable unit. Metadata mirrors PROPOSAL.md §4.1."""

    chunk_id: str  # "<accession>:<index>" — stable, and what a citation resolves to
    accession_no: str
    ticker: str
    cik: int
    form_type: str
    fiscal_year: int
    fiscal_period: dt.date  # period of report, not filing date (D-0007)
    filing_date: dt.date
    item_section: str
    start_line: int
    end_line: int  # exclusive
    text: str

    @property
    def n_chars(self) -> int:
        return len(self.text)

    @property
    def estimated_tokens(self) -> int:
        return self.n_chars // CHARS_PER_TOKEN


def is_table_row(line: str) -> bool:
    return CELL_SEP in line


def section_spans(sections: list[Section], n_lines: int) -> list[tuple[str, int, int]]:
    """Tile [0, n_lines) with named section spans plus UNSECTIONED gaps."""
    spans: list[tuple[str, int, int]] = []
    cursor = 0
    for section in sorted(sections, key=lambda s: s.start_line):
        if section.start_line > cursor:
            spans.append((UNSECTIONED, cursor, section.start_line))
        spans.append((section.name, section.start_line, section.end_line))
        cursor = section.end_line
    if cursor < n_lines:
        spans.append((UNSECTIONED, cursor, n_lines))
    return spans


def _split_span(lines: list[str], start: int, end: int) -> list[tuple[int, int]]:
    """Line ranges for one span, packing to TARGET_CHARS.

    A run of consecutive table rows is allowed to overflow to MAX_CHARS so a table is
    not sliced across chunks for the sake of a size target.
    """
    ranges: list[tuple[int, int]] = []
    buf_start, buf_chars = start, 0
    for i in range(start, end):
        line_chars = len(lines[i]) + 1
        mid_table = i > buf_start and is_table_row(lines[i - 1]) and is_table_row(lines[i])
        limit = MAX_CHARS if mid_table else TARGET_CHARS
        if i > buf_start and buf_chars + line_chars > limit:
            ranges.append((buf_start, i))
            buf_start, buf_chars = i, 0
        buf_chars += line_chars
    if buf_start < end:
        ranges.append((buf_start, end))
    return ranges


def chunk_filing(
    lines: list[str],
    sections: list[Section],
    *,
    accession_no: str,
    ticker: str,
    cik: int,
    form_type: str,
    fiscal_year: int,
    fiscal_period: dt.date,
    filing_date: dt.date,
) -> list[Chunk]:
    """Chunk an entire filing, tagging each chunk with the section it falls in."""
    chunks: list[Chunk] = []
    for name, span_start, span_end in section_spans(sections, len(lines)):
        for lo, hi in _split_span(lines, span_start, span_end):
            text = "\n".join(lines[lo:hi]).strip()
            if not text:
                continue
            chunks.append(
                Chunk(
                    chunk_id=f"{accession_no}:{len(chunks):04d}",
                    accession_no=accession_no, ticker=ticker, cik=cik,
                    form_type=form_type, fiscal_year=fiscal_year,
                    fiscal_period=fiscal_period, filing_date=filing_date,
                    item_section=name, start_line=lo, end_line=hi, text=text,
                )
            )
    return chunks


def assert_chunks_valid(
    chunks: list[Chunk], lines: list[str], source: str = "<filing>"
) -> None:
    """Hard-fail on chunking that would silently corrupt retrieval (D-0007).

    Checks the three properties above. Oversize single-line chunks are allowed —
    an 800-character table row must stay whole; splitting it is the worse failure.
    """
    problems: list[str] = []
    prev_end = 0
    for chunk in chunks:
        if chunk.start_line < prev_end:
            problems.append(f"{chunk.chunk_id}: overlaps previous chunk")
        prev_end = max(prev_end, chunk.end_line)
        if chunk.end_line - chunk.start_line > 1 and chunk.n_chars > MAX_CHARS:
            problems.append(f"{chunk.chunk_id}: {chunk.n_chars} chars exceeds {MAX_CHARS}")
        for line in lines[chunk.start_line:chunk.end_line]:
            if is_table_row(line) and line.strip() and line.strip() not in chunk.text:
                problems.append(f"{chunk.chunk_id}: table row lost from chunk text")
                break
    if len({c.chunk_id for c in chunks}) != len(chunks):
        problems.append("duplicate chunk_id")
    if problems:
        raise ChunkError(f"{source}: " + "; ".join(problems[:6]))
