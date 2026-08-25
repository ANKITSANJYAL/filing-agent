"""Frozen experiment configuration: model identifiers and corpus scope.

These constants are the experimental controls. Changing MODEL_ABLATION mid-experiment
invalidates every arm of the architecture ablation (DECISIONS.md D-0002); it must be
treated as a schema change, not a config tweak.
"""

from typing import Final

# --- Models (proposal §8, revised by DECISIONS.md D-0002) ---

# Used identically by Arms A, B, and C. Holding this constant is what makes the
# ablation an architecture measurement rather than a model benchmark.
#
# NOTE ON PINNING: Anthropic publishes no dated snapshot variant for this model —
# "claude-sonnet-5" IS the complete, exact identifier, and appending a date suffix
# produces a 404. Reproducibility therefore comes from recording the `model` field
# echoed back on every API response into each eval run's metadata, alongside a hash
# of this file. See DECISIONS.md D-0002 condition (a).
MODEL_ABLATION: Final[str] = "claude-sonnet-5"

# Planner / classifier nodes only. This one does have a dated snapshot, so we pin it.
MODEL_ROUTER: Final[str] = "claude-haiku-4-5-20251001"

# Judge. Deliberately a different model family from the system under test, to reduce
# self-preference bias (proposal §8.7). Calibrated against hand labels regardless.
MODEL_JUDGE: Final[str] = "gpt-5.2"

# --- Corpus scope (proposal §4.1 — locked) ---

TICKERS: Final[tuple[str, ...]] = (
    "NVDA",
    "AAPL",
    "MSFT",
    "JPM",
    "XOM",
    "PFE",
    "WMT",
    "COST",
)

FISCAL_YEARS: Final[tuple[int, ...]] = (2024, 2025)

# How each company names itself in its own filings. Filings never use ticker symbols in
# body text ("Apple Inc.", never "AAPL"), so eval queries phrased with tickers measure a
# vocabulary mismatch rather than retrieval quality — dense recall@5 rose 0.220 -> 0.380
# on the identical labels when queries used names instead (DECISIONS.md D-0029).
COMPANY_NAMES: Final[dict[str, str]] = {
    "NVDA": "NVIDIA",
    "AAPL": "Apple",
    "MSFT": "Microsoft",
    "JPM": "JPMorgan Chase",
    "XOM": "Exxon Mobil",
    "PFE": "Pfizer",
    "WMT": "Walmart",
    "COST": "Costco",
}

# SEC's ticker map points at the *current* registrant, which is not always the filer
# that submitted the reports in our window. XOM now maps to CIK 2115436
# ("ExxonMobil Holdings Corp", first filing 2026-07, form 8-K12B — a holding-company
# reorganization). Every FY2024-FY2025 10-K/10-Q was filed by the predecessor,
# CIK 34088 ("EXXON MOBIL CORP"). See DECISIONS.md D-0008.
#
# This override is correct *for this corpus window only*. A future FY2026 corpus would
# need the successor CIK, and possibly both.
CIK_OVERRIDES: Final[dict[str, int]] = {
    "XOM": 34088,
}

# One 10-K per ticker per fiscal year. The invariant that would have caught both the
# XOM and JPM defects (DECISIONS.md D-0009).
EXPECTED_ANNUAL_REPORTS_PER_TICKER: Final[int] = len(FISCAL_YEARS)

# Sections that are known to be cross-reference stubs rather than content, per ticker.
# Measured across all 16 10-Ks (D-0016): 48 of 64 section-slots are substantive and
# every exception below is a documented structural convention, not a parser bug.
# The assertion hard-fails on any stub NOT listed here, so a new one stops the run.
STUB_SECTION_ALLOWLIST: Final[dict[str, frozenset[str]]] = {
    # Incorporates MD&A, market risk, and financial statements by reference to page
    # ranges elsewhere in the document.
    "JPM": frozenset({"MDA", "MARKET_RISK", "FINANCIAL_STATEMENTS"}),
    "XOM": frozenset({"MDA", "MARKET_RISK", "FINANCIAL_STATEMENTS"}),
    # Item 8 points at the statements filed under Part IV, Item 15.
    "NVDA": frozenset({"FINANCIAL_STATEMENTS"}),
    # Genuinely brief Item 7A that cross-references the MD&A market-risk discussion.
    "PFE": frozenset({"MARKET_RISK"}),
}

# (ticker, concept) pairs where filings legitimately disagree on a value because of a
# corporate action, not a data error. NVIDIA's 10-for-1 split (June 2024) restated every
# per-share and share-count figure: FY2023 diluted EPS is 11.93 in the original 10-K and
# 1.19 as a later comparative. Resolution rule and rationale in DECISIONS.md D-0020.
# Anything NOT listed here hard-fails, so an unexpected restatement stops the run.
XBRL_RESTATEMENT_ALLOWLIST: Final[frozenset[tuple[str, str]]] = frozenset({
    ("NVDA", "EarningsPerShareBasic"),
    ("NVDA", "EarningsPerShareDiluted"),
    ("NVDA", "WeightedAverageNumberOfSharesOutstandingBasic"),
    ("NVDA", "WeightedAverageNumberOfDilutedSharesOutstanding"),
})

# --- SEC etiquette (CLAUDE.md §4) ---

SEC_MAX_REQUESTS_PER_SECOND: Final[float] = 10.0
SEC_RATE_LIMIT_SAFETY_FACTOR: Final[float] = 0.8  # target 8 req/s, not the ceiling
