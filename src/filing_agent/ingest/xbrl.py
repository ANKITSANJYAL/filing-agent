"""XBRL facts from EDGAR companyfacts — the ground truth for numeric verification.

Every tier-1 eval answer comes from here, not from a model. Two constraints make that
trustworthy:

1. **Traceability** — a fact is kept only if its `accn` is a filing in our manifest, so
   every verified number points at a document we hold and can cite.
2. **Unambiguity** — the same concept and period can appear in several filings (an
   original and later comparatives) with *different* values after a restatement.
   `assert_no_conflicting_values` hard-fails on that, because a question whose answer
   depends on which filing you read has no single correct answer (D-0006).
"""

from __future__ import annotations

import datetime as dt
import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Final

from pydantic import BaseModel

from .edgar_client import EdgarClient

COMPANYFACTS_URL: Final[str] = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
XBRL_DIR: Final[Path] = Path("data/raw/xbrl")
TAXONOMY: Final[str] = "us-gaap"

# Concepts reported by ALL EIGHT tickers *within our 64 filings* (D-0019). The
# qualifier matters: measuring over each company's full history gives a different and
# wrong answer, because AAPL and MSFT reported `Revenues` years ago but not in FY2024-25.
#
# PROPOSAL.md §9 names GrossProfit as a safe standard concept; only 4 of 8 report it,
# because a bank has no gross profit. This is the §9 concept-mapping risk, measured.
CORE_CONCEPTS: Final[tuple[str, ...]] = (
    "NetIncomeLoss",
    "IncomeTaxExpenseBenefit",
    "EarningsPerShareBasic",
    "EarningsPerShareDiluted",
    "Assets",
    "StockholdersEquity",
    "LiabilitiesAndStockholdersEquity",
    "RetainedEarningsAccumulatedDeficit",
    "AccumulatedOtherComprehensiveIncomeLossNetOfTax",
    "ComprehensiveIncomeNetOfTax",
    "EffectiveIncomeTaxRateContinuingOperations",
    "NetCashProvidedByUsedInOperatingActivities",
    "NetCashProvidedByUsedInInvestingActivities",
    "NetCashProvidedByUsedInFinancingActivities",
    "PaymentsForRepurchaseOfCommonStock",
    "OperatingLeaseLiability",
    "WeightedAverageNumberOfSharesOutstandingBasic",
)

# There is NO revenue concept common to all eight tickers — the single most obvious
# metric in the corpus requires per-ticker mapping. Measured within our filings:
REVENUE_CONCEPT_BY_TICKER: Final[dict[str, str]] = {
    "NVDA": "Revenues",
    "XOM": "Revenues",
    "PFE": "Revenues",
    "WMT": "Revenues",
    "COST": "Revenues",
    "AAPL": "RevenueFromContractWithCustomerExcludingAssessedTax",
    "MSFT": "RevenueFromContractWithCustomerExcludingAssessedTax",
    # Bank convention, and the economically correct concept for JPM — not a workaround.
    "JPM": "RevenuesNetOfInterestExpense",
}


def concepts_for(ticker: str) -> tuple[str, ...]:
    """Tracked concepts for one ticker: the universal core plus its revenue tag."""
    return (*CORE_CONCEPTS, REVENUE_CONCEPT_BY_TICKER[ticker])


class XbrlError(AssertionError):
    """XBRL facts failed a structural expectation (D-0007)."""


class XbrlFact(BaseModel):
    ticker: str
    cik: int
    concept: str
    unit: str
    value: float
    period_start: dt.date | None  # None for instant facts (balance-sheet items)
    period_end: dt.date
    accession_no: str
    form: str
    filed_date: dt.date
    restated: bool = False  # set by resolve_restatements when filings disagreed

    @property
    def is_duration(self) -> bool:
        return self.period_start is not None

    @property
    def period_key(self) -> tuple[str, str, str | None, str]:
        """Identity of the economic fact, independent of which filing reported it."""
        return (
            self.concept, self.unit,
            self.period_start.isoformat() if self.period_start else None,
            self.period_end.isoformat(),
        )


def fetch_company_facts(client: EdgarClient, cik: int, root: Path = XBRL_DIR) -> Path:
    """Cache-first download of one company's complete XBRL fact set."""
    path, _ = client.download(COMPANYFACTS_URL.format(cik=cik), root / f"CIK{cik:010d}.json")
    return path


def load_facts(
    path: Path,
    ticker: str,
    allowed_accessions: set[str],
    concepts: tuple[str, ...] = CORE_CONCEPTS,
) -> list[XbrlFact]:
    """Parse companyfacts, keeping only concepts we track from filings we hold.

    The accession filter is the traceability guarantee: a fact whose source filing is
    not in the corpus cannot be cited, so it is not ground truth for us.
    """
    payload: dict[str, Any] = json.loads(path.read_text())
    cik = int(payload["cik"])
    facts: list[XbrlFact] = []
    available = payload.get("facts", {}).get(TAXONOMY, {})
    for concept in concepts:
        entry = available.get(concept)
        if entry is None:
            continue
        for unit, observations in entry.get("units", {}).items():
            for obs in observations:
                if obs.get("accn") not in allowed_accessions:
                    continue
                facts.append(
                    XbrlFact(
                        ticker=ticker, cik=cik, concept=concept, unit=unit,
                        value=float(obs["val"]),
                        period_start=(
                            dt.date.fromisoformat(obs["start"]) if obs.get("start") else None
                        ),
                        period_end=dt.date.fromisoformat(obs["end"]),
                        accession_no=obs["accn"], form=obs.get("form", ""),
                        filed_date=dt.date.fromisoformat(obs["filed"]),
                    )
                )
    return facts


def assert_facts_traceable(
    facts: list[XbrlFact], allowed_accessions: set[str], source: str = "<xbrl>"
) -> None:
    """Every fact must point at a filing we actually hold."""
    orphans = {f.accession_no for f in facts} - allowed_accessions
    if orphans:
        raise XbrlError(
            f"{source}: {len(orphans)} fact accession(s) absent from the corpus manifest: "
            f"{sorted(orphans)[:5]}"
        )


def resolve_restatements(facts: list[XbrlFact]) -> list[XbrlFact]:
    """Collapse each economic fact to one value, preferring the most recently filed.

    NVIDIA's 10-for-1 split (June 2024) means FY2023 diluted EPS is reported as 11.93 in
    the original 10-K and 1.19 as a comparative afterwards. Both are "correct"; only one
    is the answer an analyst would give today, and an eval answer key needs exactly one
    (D-0006). Most-recently-filed wins because a split is a real economic event and the
    adjusted figure is the current basis. Collapsed facts are marked `restated=True` so
    the choice stays visible rather than becoming folklore.
    """
    grouped: dict[tuple, list[XbrlFact]] = defaultdict(list)
    for fact in facts:
        grouped[(fact.ticker, *fact.period_key)].append(fact)
    resolved: list[XbrlFact] = []
    for group in grouped.values():
        winner = max(group, key=lambda f: (f.filed_date, f.accession_no))
        resolved.append(
            winner.model_copy(update={"restated": len({g.value for g in group}) > 1})
        )
    return sorted(resolved, key=lambda f: (f.ticker, f.concept, f.period_end))


def assert_no_conflicting_values(
    facts: list[XbrlFact],
    source: str = "<xbrl>",
    allowed: frozenset[tuple[str, str]] = frozenset(),
    rel_tolerance: float = 1e-9,
) -> None:
    """Hard-fail when one economic fact carries different values across filings.

    A restated figure appears once in its original 10-K and again as a comparative in
    the next year's, sometimes with a different value. An eval answer key built from
    such a fact has no single correct answer, so this must surface at ingest rather
    than as an unexplainable eval failure months later (D-0006).
    """
    grouped: dict[tuple, set[float]] = defaultdict(set)
    for fact in facts:
        grouped[(fact.ticker, *fact.period_key)].add(fact.value)
    problems: list[str] = []
    for key, values in grouped.items():
        ticker, concept = key[0], key[1]
        if len(values) < 2 or (ticker, concept) in allowed:
            continue
        lo, hi = min(values), max(values)
        if abs(hi - lo) > rel_tolerance * max(abs(hi), 1.0):
            problems.append(f"{ticker} {concept} {key[3]}..{key[4]}: {sorted(values)}")
    if problems:
        raise XbrlError(
            f"{source}: {len(problems)} restated/conflicting fact(s):\n  "
            + "\n  ".join(problems[:8])
        )


def assert_concept_coverage(
    facts: list[XbrlFact],
    tickers: tuple[str, ...],
    concepts: tuple[str, ...],
    source: str = "<xbrl>",
) -> None:
    """Completeness: every ticker must report every tracked concept (D-0009).

    Correctness checks say nothing about facts we never loaded; an empty set passes
    them all.
    """
    have = {(f.ticker, f.concept) for f in facts}
    missing = [f"{t}/{c}" for t in tickers for c in concepts if (t, c) not in have]
    if missing:
        raise XbrlError(
            f"{source}: {len(missing)} ticker/concept pair(s) with no facts: {missing[:10]}"
        )
