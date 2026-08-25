"""One place that turns the cached corpus into chunks and facts.

Every verification run so far rebuilt this sequence inline, which is exactly how two
callers drift apart and start disagreeing about what the corpus contains. Chunking and
fact resolution now have a single definition.
"""

from __future__ import annotations

import pathlib
from typing import Final

from ..config import FISCAL_YEARS, TICKERS, XBRL_RESTATEMENT_ALLOWLIST
from .chunker import Chunk, assert_chunks_valid, chunk_filing
from .corpus import ManifestRow, read_manifest
from .edgar_client import EdgarClient
from .extract import assert_no_xbrl_metadata, extract_text
from .filing_index import resolve_ciks
from .sections import find_sections
from .xbrl import (
    XBRL_DIR,
    XbrlFact,
    assert_facts_traceable,
    assert_no_conflicting_values,
    concepts_for,
    fetch_company_facts,
    load_facts,
    resolve_restatements,
)

SEC_USER_AGENT_ENV: Final[str] = "SEC_USER_AGENT"


def build_chunks(manifest: list[ManifestRow] | None = None) -> list[Chunk]:
    """Extract, section and chunk every downloaded filing, asserting as we go."""
    rows = [r for r in (manifest or read_manifest()) if r.downloaded]
    chunks: list[Chunk] = []
    for row in rows:
        html = pathlib.Path(row.local_path).read_text(errors="ignore")
        text = extract_text(html)
        assert_no_xbrl_metadata(text, row.local_path)
        lines = text.split("\n")
        produced = chunk_filing(
            lines, find_sections(lines, row.form),
            accession_no=row.accession_no, ticker=row.ticker, cik=row.cik,
            form_type=row.form, fiscal_year=row.fiscal_year,
            fiscal_period=row.report_date, filing_date=row.filing_date,
        )
        assert_chunks_valid(produced, lines, f"{row.ticker} {row.form} {row.report_date}")
        chunks.extend(produced)
    return chunks


def build_facts(
    client: EdgarClient,
    manifest: list[ManifestRow] | None = None,
    tickers: tuple[str, ...] = TICKERS,
) -> list[XbrlFact]:
    """Fetch (cache-first), filter to corpus accessions, and resolve restatements."""
    rows = [r for r in (manifest or read_manifest()) if r.downloaded]
    accessions = {r.accession_no for r in rows}
    ciks = resolve_ciks(client, tickers)
    raw: list[XbrlFact] = []
    for ticker, cik in ciks.items():
        fetch_company_facts(client, cik)
        raw.extend(
            load_facts(XBRL_DIR / f"CIK{cik:010d}.json", ticker, accessions, concepts_for(ticker))
        )
    assert_facts_traceable(raw, accessions)
    assert_no_conflicting_values(raw, allowed=XBRL_RESTATEMENT_ALLOWLIST)
    return resolve_restatements(raw)


def build_all(
    user_agent: str | None = None,
    tickers: tuple[str, ...] = TICKERS,
    fiscal_years: tuple[int, ...] = FISCAL_YEARS,
) -> tuple[list[ManifestRow], list[Chunk], list[XbrlFact]]:
    """The whole ingest output, from the on-disk cache. No network unless facts are cold."""
    manifest = read_manifest()
    chunks = build_chunks(manifest)
    with EdgarClient(user_agent=user_agent) as client:
        facts = build_facts(client, manifest, tickers)
    return manifest, chunks, facts
