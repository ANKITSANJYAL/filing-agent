"""Agent tools with Pydantic contracts (PROPOSAL.md §4.3).

Three tools, and the second is the one the project rests on:

* `retrieve`      — hybrid search over the chunk corpus
* `xbrl_lookup`   — the ground-truth primitive every numeric claim is checked against
* `calculator`    — arithmetic the model must not do in its head

Two deliberate design points:

1. `xbrl_lookup` resolves a *label* ("revenue") to the concept that particular company
   reports, because no revenue tag is common to all eight (D-0019). Asking the model to
   know that JPMorgan files `RevenuesNetOfInterestExpense` would be asking it to memorise
   a corpus quirk.
2. `calculator` parses an AST with an operator allowlist rather than calling `eval`.
   The expression is model output, and model output is untrusted input.
"""

from __future__ import annotations

import ast
import datetime as dt
import operator
from collections.abc import Sequence
from typing import Any, Final

import psycopg
from pydantic import BaseModel, Field

from ..config import COMPANY_NAMES, FISCAL_YEARS
from ..ingest.xbrl import CORE_CONCEPTS, REVENUE_CONCEPT_BY_TICKER
from ..retrieval.search import Hit, hybrid_search, lexical_search

# Natural-language label -> XBRL concept, for concepts that are the same everywhere.
LABEL_TO_CONCEPT: Final[dict[str, str]] = {
    "net income": "NetIncomeLoss",
    "income tax expense": "IncomeTaxExpenseBenefit",
    "basic earnings per share": "EarningsPerShareBasic",
    "diluted earnings per share": "EarningsPerShareDiluted",
    "eps": "EarningsPerShareDiluted",
    "total assets": "Assets",
    "assets": "Assets",
    "stockholders equity": "StockholdersEquity",
    "shareholders equity": "StockholdersEquity",
    "equity": "StockholdersEquity",
    "retained earnings": "RetainedEarningsAccumulatedDeficit",
    "comprehensive income": "ComprehensiveIncomeNetOfTax",
    "effective tax rate": "EffectiveIncomeTaxRateContinuingOperations",
    "operating cash flow": "NetCashProvidedByUsedInOperatingActivities",
    "cash from operations": "NetCashProvidedByUsedInOperatingActivities",
    "investing cash flow": "NetCashProvidedByUsedInInvestingActivities",
    "financing cash flow": "NetCashProvidedByUsedInFinancingActivities",
    "share repurchases": "PaymentsForRepurchaseOfCommonStock",
    "buybacks": "PaymentsForRepurchaseOfCommonStock",
    "operating lease liability": "OperatingLeaseLiability",
    "shares outstanding": "WeightedAverageNumberOfSharesOutstandingBasic",
}
# Labels whose concept depends on the filer (D-0019).
REVENUE_LABELS: Final[frozenset[str]] = frozenset(
    {"revenue", "revenues", "total revenue", "net revenue", "sales", "top line"}
)


class ToolError(RuntimeError):
    """A tool could not answer. Surfaced to the agent, never silently swallowed."""


# --- retrieve ------------------------------------------------------------------

class RetrieveInput(BaseModel):
    query: str = Field(min_length=1)
    tickers: list[str] | None = None
    fiscal_years: list[int] | None = None
    top_k: int = Field(default=5, ge=1, le=50)


class RetrieveOutput(BaseModel):
    hits: list[Hit]
    n_hits: int

    @property
    def is_empty(self) -> bool:
        return self.n_hits == 0


def retrieve(
    conn: psycopg.Connection, params: RetrieveInput, query_vector: Any | None = None
) -> RetrieveOutput:
    """Hybrid search when an embedding is supplied, lexical alone when it is not.

    The fallback matters for the re-plan loop: a degraded retrieval is better than a
    tool that raises and forces the whole graph to abort.
    """
    if query_vector is None:
        hits = lexical_search(conn, params.query, params.top_k,
                              params.tickers, params.fiscal_years)
    else:
        hits = hybrid_search(conn, params.query, query_vector, params.top_k,
                             params.tickers, params.fiscal_years)
    return RetrieveOutput(hits=hits, n_hits=len(hits))


# --- xbrl_lookup ---------------------------------------------------------------

class XbrlLookupInput(BaseModel):
    ticker: str = Field(min_length=1)
    concept: str = Field(min_length=1, description="XBRL concept or a natural label")
    fiscal_year: int


class XbrlLookupOutput(BaseModel):
    found: bool
    ticker: str
    resolved_concept: str | None = None
    value: float | None = None
    unit: str | None = None
    period_end: dt.date | None = None
    accession_no: str | None = None
    restated: bool = False
    available_concepts: list[str] = Field(default_factory=list)
    detail: str = ""


def resolve_concept(ticker: str, concept: str) -> str | None:
    """Map a label to the concept *this filer* uses, or pass an exact concept through."""
    if concept in CORE_CONCEPTS or concept in REVENUE_CONCEPT_BY_TICKER.values():
        return concept
    key = concept.strip().lower().replace("'", "")
    if key in REVENUE_LABELS:
        return REVENUE_CONCEPT_BY_TICKER.get(ticker.upper())
    return LABEL_TO_CONCEPT.get(key)


def xbrl_lookup(conn: psycopg.Connection, params: XbrlLookupInput) -> XbrlLookupOutput:
    """Look up one fact. A miss returns what *is* available rather than just failing.

    The re-plan loop (T2.4) needs to know whether the concept was wrong or the period
    was; an empty result cannot distinguish them.
    """
    ticker = params.ticker.upper()
    resolved = resolve_concept(ticker, params.concept)
    if resolved is None:
        return XbrlLookupOutput(
            found=False, ticker=ticker,
            available_concepts=sorted(LABEL_TO_CONCEPT),
            detail=f"no XBRL concept known for {params.concept!r}",
        )
    with conn.cursor() as cur:
        # Anchor to the 10-K's own period end rather than the calendar year of the
        # fact. Matching on EXTRACT(YEAR FROM period_end) returned NVDA's Q3 figure
        # ($77.1bn, period ending 2025-10-26) for "FY2025" instead of the annual
        # $72.88bn ending 2025-01-26 — verification would then mark correct annual
        # answers wrong against a quarterly fact (D-0032).
        cur.execute(
            "SELECT f.value, f.unit, f.period_end, f.accession_no, f.restated"
            " FROM xbrl_facts f"
            " JOIN filings fl ON fl.ticker = f.ticker AND fl.form_type = '10-K'"
            "                AND fl.fiscal_year = %s"
            " WHERE f.ticker = %s AND f.concept = %s"
            "   AND f.period_end = fl.fiscal_period"
            "   AND (f.period_start IS NULL"
            "        OR (f.period_end - f.period_start) BETWEEN 350 AND 380)"
            " LIMIT 1",
            (params.fiscal_year, ticker, resolved),
        )
        row = cur.fetchone()
        if row is None:
            cur.execute(
                "SELECT DISTINCT concept FROM xbrl_facts WHERE ticker = %s ORDER BY 1",
                (ticker,),
            )
            return XbrlLookupOutput(
                found=False, ticker=ticker, resolved_concept=resolved,
                available_concepts=[r[0] for r in cur.fetchall()],
                detail=f"no {resolved} fact for {ticker} FY{params.fiscal_year}",
            )
    value, unit, period_end, accession_no, restated = row
    return XbrlLookupOutput(
        found=True, ticker=ticker, resolved_concept=resolved, value=float(value),
        unit=unit, period_end=period_end, accession_no=accession_no, restated=restated,
    )


# --- calculator ----------------------------------------------------------------

_OPS: Final[dict[type, Any]] = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
    ast.Div: operator.truediv, ast.Pow: operator.pow, ast.Mod: operator.mod,
    ast.USub: operator.neg, ast.UAdd: operator.pos,
}
MAX_EXPRESSION_CHARS: Final[int] = 200


class CalculatorInput(BaseModel):
    expression: str = Field(min_length=1, max_length=MAX_EXPRESSION_CHARS)


class CalculatorOutput(BaseModel):
    expression: str
    value: float


def _eval_node(node: ast.AST) -> float:
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            raise ToolError(f"unsupported literal: {node.value!r}")
        return float(node.value)
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_eval_node(node.left), _eval_node(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_eval_node(node.operand))
    raise ToolError(f"unsupported expression element: {type(node).__name__}")


def calculator(params: CalculatorInput) -> CalculatorOutput:
    """Evaluate arithmetic via an AST allowlist.

    `eval` on model output is remote code execution with extra steps. Parsing and
    walking an allowlisted node set means a malicious or confused expression cannot
    reach an import, a call, or an attribute.
    """
    try:
        tree = ast.parse(params.expression, mode="eval")
    except SyntaxError as exc:
        raise ToolError(f"could not parse {params.expression!r}: {exc}") from exc
    try:
        value = _eval_node(tree.body)
    except ZeroDivisionError as exc:
        raise ToolError("division by zero") from exc
    return CalculatorOutput(expression=params.expression, value=float(value))


# --- shared helpers ------------------------------------------------------------

def known_tickers() -> Sequence[str]:
    return tuple(COMPANY_NAMES)


def known_fiscal_years() -> Sequence[int]:
    return FISCAL_YEARS
