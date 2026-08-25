"""Generate the labelled query -> section pairs for the T1.6 retrieval ablation.

Two rules keep the comparison fair, and both matter more than the questions themselves:

1. **Queries never reuse the target section's wording.** They are written in generic
   analyst vocabulary from a template. Deriving a query from the text it should retrieve
   hands the match to lexical search by construction, and the resulting table would
   report "BM25 beats dense" when it actually reports a leak.
2. **Stub sections are excluded.** JPM and XOM incorporate MD&A by reference and NVDA
   files its statements under Item 15 (D-0016). Asking retrieval to find two lines of
   cross-reference measures a corpus limitation, not a retriever.

Provenance is template-generated, not third-party annotation. The write-up says so.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final

from ..config import STUB_SECTION_ALLOWLIST
from ..ingest.corpus import ManifestRow
from ..ingest.extract import extract_text
from ..ingest.sections import (
    BUSINESS,
    FINANCIAL_STATEMENTS,
    MARKET_RISK,
    MDA,
    RISK_FACTORS,
    find_sections,
)
from .retrieval_eval import RetrievalCase

# Analyst phrasing, deliberately generic. No template contains a phrase copied from the
# sections it targets, so neither retriever gets a vocabulary advantage.
TEMPLATES: Final[dict[str, tuple[str, ...]]] = {
    RISK_FACTORS: (
        "What are the principal risks {ticker} disclosed for fiscal {year}?",
        "Which threats to its business did {ticker} identify in fiscal {year}?",
    ),
    MDA: (
        "How did {ticker} explain its operating performance in fiscal {year}?",
        "What did {ticker} management say drove results in fiscal {year}?",
    ),
    FINANCIAL_STATEMENTS: (
        "What were {ticker}'s audited financial results for fiscal {year}?",
        "Show {ticker}'s consolidated statements for fiscal {year}.",
    ),
    MARKET_RISK: (
        "What market risk exposures did {ticker} report for fiscal {year}?",
        "How is {ticker} exposed to interest rate and currency movements in fiscal {year}?",
    ),
    BUSINESS: (
        "What products and operations does {ticker} describe for fiscal {year}?",
        "How does {ticker} describe its business segments in fiscal {year}?",
    ),
}

# Section order for deterministic, balanced sampling.
SECTION_ORDER: Final[tuple[str, ...]] = (
    RISK_FACTORS, MDA, FINANCIAL_STATEMENTS, BUSINESS, MARKET_RISK,
)
MIN_SECTION_LINES: Final[int] = 12
TARGET_CASES: Final[int] = 50


def build_cases(
    manifest: Sequence[ManifestRow],
    target: int = TARGET_CASES,
) -> list[RetrievalCase]:
    """One case per (10-K, substantive section), sampled to `target`, deterministically."""
    candidates: list[tuple[str, str, ManifestRow]] = []
    for row in sorted(manifest, key=lambda r: (r.ticker, r.fiscal_year)):
        if not row.downloaded or row.form != "10-K":
            continue
        allowed_stubs = STUB_SECTION_ALLOWLIST.get(row.ticker, frozenset())
        lines = extract_text(_read(row)).split("\n")
        by_name = {s.name: s for s in find_sections(lines, row.form)}
        for section in SECTION_ORDER:
            found = by_name.get(section)
            if section in allowed_stubs or found is None:
                continue
            if found.n_lines < MIN_SECTION_LINES:
                continue  # substance, not just presence (D-0016)
            candidates.append((section, row.ticker, row))

    # Round-robin by TICKER, cycling sections within each. Cycling sections alone popped
    # tickers alphabetically and exhausted the 50 slots before reaching XOM — dropping
    # one of the eight companies entirely, and specifically one of the structurally
    # unusual by-reference filers the eval most needs to cover.
    buckets: dict[str, list[tuple[str, ManifestRow]]] = {}
    for section, ticker, row in candidates:
        buckets.setdefault(ticker, []).append((section, row))
    # Each ticker leads with a different section. Without the rotation every ticker pops
    # RISK_FACTORS first and the sample skews hard toward whichever section sorts first
    # (MARKET_RISK fell to a single case).
    n_sections = len(SECTION_ORDER)
    for offset, ticker in enumerate(sorted(buckets)):
        buckets[ticker].sort(
            key=lambda sr: (
                (SECTION_ORDER.index(sr[0]) - offset) % n_sections,
                sr[1].fiscal_year,
            )
        )

    cases: list[RetrievalCase] = []
    index = 0
    while len(cases) < target and any(buckets.values()):
        for ticker in sorted(buckets):
            if not buckets[ticker] or len(cases) >= target:
                continue
            section, row = buckets[ticker].pop(0)
            templates = TEMPLATES[section]
            query = templates[index % len(templates)].format(
                ticker=ticker, year=row.fiscal_year
            )
            cases.append(RetrievalCase(
                case_id=f"r-{len(cases) + 1:03d}-{ticker}-{section}-{row.fiscal_year}",
                query=query,
                relevant=[(row.accession_no, section)],
                tickers=(ticker,),
                fiscal_years=(row.fiscal_year,),
            ))
            index += 1
    return cases


def _read(row: ManifestRow) -> str:
    from pathlib import Path

    return Path(row.local_path).read_text(errors="ignore")
