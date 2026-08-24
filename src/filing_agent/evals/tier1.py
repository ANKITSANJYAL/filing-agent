"""Tier-1 eval set: numeric questions graded by exact match against XBRL.

Zero LLM grading (PROPOSAL.md §4.4.1). Questions are generated *from* the resolved fact
set, so the answer key is XBRL by construction — it cannot be contaminated by what the
retrieval system happens to answer well (D-0003).

Restated facts are excluded. An agent reading NVDA's FY2024 10-K would correctly report
diluted EPS of 11.93 while the resolved key says 1.19 (D-0020); grading that as wrong
would penalise a correct answer, so those facts do not generate questions.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Final, Literal

from pydantic import BaseModel

from ..ingest.filing_index import fiscal_year_for
from ..ingest.xbrl import XbrlFact

TIER1_PATH: Final[Path] = Path("evals/tier1.jsonl")

# Analyst-readable labels. A question says "net income", not "NetIncomeLoss".
CONCEPT_LABELS: Final[dict[str, str]] = {
    "NetIncomeLoss": "net income",
    "IncomeTaxExpenseBenefit": "income tax expense",
    "EarningsPerShareBasic": "basic earnings per share",
    "EarningsPerShareDiluted": "diluted earnings per share",
    "Assets": "total assets",
    "StockholdersEquity": "total stockholders' equity",
    "LiabilitiesAndStockholdersEquity": "total liabilities and stockholders' equity",
    "RetainedEarningsAccumulatedDeficit": "retained earnings",
    "AccumulatedOtherComprehensiveIncomeLossNetOfTax": "accumulated other comprehensive income",
    "ComprehensiveIncomeNetOfTax": "comprehensive income",
    "EffectiveIncomeTaxRateContinuingOperations": "effective income tax rate",
    "NetCashProvidedByUsedInOperatingActivities": "net cash provided by operating activities",
    "NetCashProvidedByUsedInInvestingActivities": "net cash used in investing activities",
    "NetCashProvidedByUsedInFinancingActivities": "net cash used in financing activities",
    "PaymentsForRepurchaseOfCommonStock": "payments for repurchase of common stock",
    "OperatingLeaseLiability": "operating lease liability",
    "WeightedAverageNumberOfSharesOutstandingBasic": "weighted average basic shares outstanding",
    "Revenues": "total revenue",
    "RevenueFromContractWithCustomerExcludingAssessedTax": "total revenue",
    "RevenuesNetOfInterestExpense": "total revenue net of interest expense",
}

QuestionType = Literal["point", "yoy_change", "yoy_pct", "cross_ticker"]

# An annual duration fact. Fiscal years vary in length (52/53-week retail calendars),
# so this is a band, not an equality.
ANNUAL_MIN_DAYS: Final[int] = 350
ANNUAL_MAX_DAYS: Final[int] = 380

# Exact by default. PROPOSAL.md §4.4.1 promises exact-match grading against EDGAR, and
# a relative 1e-6 on a $72.88B figure would silently accept $72,880 of error. Reported
# XBRL amounts and their differences are integers well inside float64's exact range
# (< 2^53), so equality is the honest comparison.
DEFAULT_TOLERANCE: Final[float] = 0.0

# Percentages are genuinely non-terminating, so the key is rounded to 2dp and graded to
# half of the last place — i.e. any answer that rounds to the same 2dp is correct. Still
# fully programmatic; no analyst reports a percentage to nine decimals.
PERCENT_DECIMALS: Final[int] = 2
PERCENT_ABS_TOLERANCE: Final[float] = 0.005


class Tier1Error(AssertionError):
    """The eval set failed a structural expectation (D-0007)."""


class EvalQuestion(BaseModel):
    question_id: str
    question: str
    question_type: QuestionType
    tickers: tuple[str, ...]
    concept: str
    fiscal_years: tuple[int, ...]
    expected_value: float | str
    unit: str
    rel_tolerance: float = DEFAULT_TOLERANCE
    abs_tolerance: float = 0.0
    source_accessions: tuple[str, ...]

    def grade(self, answer: float | str) -> bool:
        """Exact match within tolerance. No model in the loop."""
        if isinstance(self.expected_value, str):
            return str(answer).strip().upper() == self.expected_value.upper()
        try:
            got = float(answer)
        except (TypeError, ValueError):
            return False
        scale = max(abs(self.expected_value), 1.0)
        allowed = max(self.rel_tolerance * scale, self.abs_tolerance)
        return abs(got - self.expected_value) <= allowed


def _decimal_delta(later: float, earlier: float) -> float:
    """Difference computed in decimal, not binary float.

    XBRL amounts are decimal quantities. Subtracting them as float64 injects artifacts
    that are not in the data: 7.09 - 5.71 yields 1.3800000000000008, so an exact-match
    key would reject the correct answer of 1.38.
    """
    return float(Decimal(str(later)) - Decimal(str(earlier)))


def _label(concept: str) -> str:
    return CONCEPT_LABELS.get(concept, concept)


def _annual(facts: list[XbrlFact]) -> list[tuple[int, XbrlFact]]:
    """(fiscal_year, fact) for annual durations and period-end instants only.

    Mixing a quarterly fact into an annual question is the obvious way to build an
    eval that is wrong in a way nobody notices.
    """
    out: list[tuple[int, XbrlFact]] = []
    for fact in facts:
        if fact.restated:
            continue
        if fact.is_duration:
            days = (fact.period_end - fact.period_start).days
            if not ANNUAL_MIN_DAYS <= days <= ANNUAL_MAX_DAYS:
                continue
        fiscal_year, ambiguous = fiscal_year_for(fact.period_end)
        if ambiguous:
            continue
        out.append((fiscal_year, fact))
    return out


def build_tier1(facts: list[XbrlFact], fiscal_years: tuple[int, ...]) -> list[EvalQuestion]:
    """Generate the deterministic tier-1 question set from resolved XBRL facts."""
    annual = [(fy, f) for fy, f in _annual(facts) if fy in fiscal_years]
    by_key: dict[tuple[str, str, int], XbrlFact] = {}
    for fiscal_year, fact in annual:
        by_key[(fact.ticker, fact.concept, fiscal_year)] = fact

    questions: list[EvalQuestion] = []

    def add(q: EvalQuestion) -> None:
        questions.append(q)

    latest = max(fiscal_years)
    earliest = min(fiscal_years)

    for (ticker, concept, fiscal_year), fact in sorted(by_key.items()):
        label = _label(concept)
        when = "for" if fact.is_duration else "as of the end of"
        add(EvalQuestion(
            question_id=f"t1-point-{ticker}-{concept}-{fiscal_year}",
            question=f"What was {ticker}'s {label} {when} fiscal year {fiscal_year}?",
            question_type="point", tickers=(ticker,), concept=concept,
            fiscal_years=(fiscal_year,), expected_value=fact.value, unit=fact.unit,
            source_accessions=(fact.accession_no,),
        ))

    if earliest == latest:
        # A single fiscal year has nothing to compare against; without this guard the
        # loop below pairs each fact with itself and emits "changed by 0" questions.
        return questions

    for (ticker, concept, fiscal_year), fact in sorted(by_key.items()):
        if fiscal_year != latest:
            continue
        prior = by_key.get((ticker, concept, earliest))
        if prior is None or prior.unit != fact.unit:
            continue
        label = _label(concept)
        add(EvalQuestion(
            question_id=f"t1-chg-{ticker}-{concept}",
            question=(f"By how much did {ticker}'s {label} change from fiscal year "
                      f"{earliest} to fiscal year {latest}? Report the absolute change."),
            question_type="yoy_change", tickers=(ticker,), concept=concept,
            fiscal_years=(earliest, latest),
            expected_value=_decimal_delta(fact.value, prior.value),
            unit=fact.unit, source_accessions=(prior.accession_no, fact.accession_no),
        ))
        if prior.value != 0:
            add(EvalQuestion(
                question_id=f"t1-pct-{ticker}-{concept}",
                question=(f"What was the percentage change in {ticker}'s {label} from "
                          f"fiscal year {earliest} to fiscal year {latest}? "
                          "Report a percentage."),
                question_type="yoy_pct", tickers=(ticker,), concept=concept,
                fiscal_years=(earliest, latest),
                expected_value=round(
                    _decimal_delta(fact.value, prior.value) / abs(prior.value) * 100.0,
                    PERCENT_DECIMALS,
                ),
                unit="percent", abs_tolerance=PERCENT_ABS_TOLERANCE,
                source_accessions=(prior.accession_no, fact.accession_no),
            ))
    return questions


# Sampling priority. An analyst-facing eval should lead with the figures an analyst
# actually asks about; "accumulated other comprehensive income" is a valid fact but a
# poor headline question. Lower index = sampled first.
CONCEPT_PRIORITY: Final[tuple[str, ...]] = (
    "Revenues",
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "RevenuesNetOfInterestExpense",
    "NetIncomeLoss",
    "EarningsPerShareDiluted",
    "Assets",
    "NetCashProvidedByUsedInOperatingActivities",
    "StockholdersEquity",
    "EarningsPerShareBasic",
    "IncomeTaxExpenseBenefit",
    "PaymentsForRepurchaseOfCommonStock",
    "RetainedEarningsAccumulatedDeficit",
)


def select_frozen_set(
    questions: list[EvalQuestion], per_stratum: int = 3
) -> list[EvalQuestion]:
    """Deterministically pick a balanced CI set: `per_stratum` per (ticker, type).

    No RNG — selection is a stable sort by concept priority then question_id, so
    regenerating the set on any machine yields byte-identical output. That matters
    because the set is frozen and committed (D-0003).
    """
    def rank(question: EvalQuestion) -> tuple[int, str]:
        try:
            priority = CONCEPT_PRIORITY.index(question.concept)
        except ValueError:
            priority = len(CONCEPT_PRIORITY)
        return priority, question.question_id

    strata: dict[tuple[str, str], list[EvalQuestion]] = {}
    for question in questions:
        strata.setdefault((question.tickers[0], question.question_type), []).append(question)
    chosen: list[EvalQuestion] = []
    for key in sorted(strata):
        chosen.extend(sorted(strata[key], key=rank)[:per_stratum])
    return sorted(chosen, key=lambda q: q.question_id)


def write_tier1(questions: list[EvalQuestion], path: Path = TIER1_PATH) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for question in sorted(questions, key=lambda q: q.question_id):
            fh.write(question.model_dump_json() + "\n")
    return path


def read_tier1(path: Path = TIER1_PATH) -> list[EvalQuestion]:
    with path.open(encoding="utf-8") as fh:
        return [EvalQuestion(**json.loads(line)) for line in fh if line.strip()]


def assert_questions_valid(
    questions: list[EvalQuestion],
    allowed_accessions: set[str],
    source: str = "<tier1>",
) -> None:
    """Every question must be uniquely identified, citable, and self-gradeable."""
    problems: list[str] = []
    ids = [q.question_id for q in questions]
    if len(set(ids)) != len(ids):
        problems.append("duplicate question_id")
    for question in questions:
        orphan = set(question.source_accessions) - allowed_accessions
        if orphan:
            problems.append(f"{question.question_id}: uncitable source {sorted(orphan)}")
        if not question.grade(question.expected_value):
            problems.append(f"{question.question_id}: does not grade its own answer")
        expected = question.expected_value
        if isinstance(expected, float) and expected != expected:
            problems.append(f"{question.question_id}: NaN expected value")
    if problems:
        raise Tier1Error(f"{source}: " + "; ".join(problems[:8]))
