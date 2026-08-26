"""The LangGraph agent: planner -> retrieve -> verify -> memo -> check (PROPOSAL.md §4.3).

Single agent, typed state, bounded re-plans. Two structural choices carry most of the
safety:

1. **The model never writes a citation.** It returns an *index* into the excerpts it was
   shown plus a verbatim quote; `Citation` objects are built server-side from that index.
   A fabricated `chunk_id` is therefore impossible rather than merely detectable.
2. **Verification runs after generation, in code.** Every numeric claim is re-checked
   against XBRL by `verify_claims`, not by asking the model whether it was right.

Re-planning is bounded at MAX_REPLANS (§4.3). An agent that can loop forever on a
question it cannot answer is a cost incident, not a feature.
"""

from __future__ import annotations

import re
import uuid
from typing import Any, Final, TypedDict

import psycopg
from pydantic import BaseModel, Field

from ..config import COMPANY_NAMES, FISCAL_YEARS, MODEL_ABLATION, MODEL_ROUTER
from ..retrieval.search import Hit
from .prompts import MEMO_SYSTEM, PLANNER_SYSTEM, format_excerpts, format_facts
from .schemas import Citation, Claim, Memo, Verification
from .tools import (
    RetrieveInput,
    XbrlLookupInput,
    resolve_concept,
    retrieve,
    xbrl_lookup,
)

MAX_REPLANS: Final[int] = 3
DEFAULT_TOP_K: Final[int] = 8
# Relative tolerance when comparing a stated figure to its XBRL fact. Filings round to
# millions in prose ("$130.5 billion"), so an exact match would fail correct answers.
VERIFY_REL_TOLERANCE: Final[float] = 0.005


class Plan(BaseModel):
    """Planner output. Constrained so the model cannot invent corpus scope."""

    tickers: list[str] = Field(default_factory=list)
    fiscal_years: list[int] = Field(default_factory=list)
    concepts: list[str] = Field(default_factory=list)
    search_query: str = ""


class DraftClaim(BaseModel):
    """What the memo model is allowed to emit — an index, not a citation."""

    text: str
    source_index: int
    quote: str
    value: float | None = None
    unit: str | None = None
    period: str | None = None
    concept: str | None = None


class DraftMemo(BaseModel):
    answer_summary: str
    claims: list[DraftClaim] = Field(default_factory=list)
    confidence_notes: str = ""


class AgentState(TypedDict, total=False):
    question: str
    trace_id: str
    plan: Plan | None
    hits: list[Hit]
    facts: list[Any]
    memo: Memo | None
    replans: int
    notes: list[str]


# --- nodes ---------------------------------------------------------------------

def plan_node(state: AgentState, *, client: Any) -> AgentState:
    """Haiku-tier routing (PROPOSAL.md §8.6) — cheap classification, not synthesis."""
    allowed = (
        f"Allowed tickers: {', '.join(f'{t} ({n})' for t, n in COMPANY_NAMES.items())}\n"
        f"Allowed fiscal years: {', '.join(str(y) for y in FISCAL_YEARS)}"
    )
    previous = state.get("notes") or []
    retry_hint = (
        f"\n\nA previous attempt failed: {previous[-1]}. Broaden or correct the plan."
        if state.get("replans") else ""
    )
    response = client.messages.parse(
        model=MODEL_ROUTER,
        max_tokens=1024,
        system=PLANNER_SYSTEM,
        messages=[{
            "role": "user",
            "content": f"{allowed}\n\nQuestion: {state['question']}{retry_hint}",
        }],
        output_format=Plan,
    )
    plan: Plan = response.parsed_output or Plan()
    # The model is asked to stay in scope; this enforces it regardless.
    plan.tickers = [t.upper() for t in plan.tickers if t.upper() in COMPANY_NAMES]
    plan.fiscal_years = [y for y in plan.fiscal_years if y in FISCAL_YEARS]
    if not plan.search_query.strip():
        plan.search_query = state["question"]
    return {**state, "plan": plan}


def retrieve_node(state: AgentState, *, conn: psycopg.Connection, encoder: Any) -> AgentState:
    plan = state.get("plan") or Plan(search_query=state["question"])
    vector = encoder.encode([plan.search_query], normalize_embeddings=True)[0]
    out = retrieve(
        conn,
        RetrieveInput(
            query=plan.search_query,
            tickers=plan.tickers or None,
            fiscal_years=plan.fiscal_years or None,
            top_k=DEFAULT_TOP_K,
        ),
        query_vector=vector,
    )
    return {**state, "hits": list(out.hits)}


def facts_node(state: AgentState, *, conn: psycopg.Connection) -> AgentState:
    """Look up every concept the plan named, for every ticker/year in scope."""
    plan = state.get("plan") or Plan()
    facts = []
    for ticker in plan.tickers or []:
        for year in plan.fiscal_years or list(FISCAL_YEARS):
            for concept in plan.concepts or []:
                if resolve_concept(ticker, concept) is None:
                    continue
                facts.append(
                    xbrl_lookup(conn, XbrlLookupInput(
                        ticker=ticker, concept=concept, fiscal_year=year))
                )
    return {**state, "facts": facts}


def memo_node(state: AgentState, *, client: Any, conn: psycopg.Connection) -> AgentState:
    """Draft the memo, then build citations and verify — neither is left to the model."""
    hits: list[Hit] = state.get("hits") or []
    content = (
        f"Question: {state['question']}\n\n"
        f"Verified figures from SEC XBRL data:\n{format_facts(state.get('facts') or [])}\n\n"
        f"Filing excerpts:\n{format_excerpts(hits)}"
    )
    response = client.messages.parse(
        model=MODEL_ABLATION,
        max_tokens=4096,
        system=MEMO_SYSTEM,
        messages=[{"role": "user", "content": content}],
        output_format=DraftMemo,
    )
    draft: DraftMemo = response.parsed_output or DraftMemo(answer_summary="No answer produced.")
    claims, dropped = build_claims(draft, hits, conn)
    notes = list(state.get("notes") or [])
    if dropped:
        notes.append(f"{dropped} draft claim(s) dropped as uncitable")
    confidence = draft.confidence_notes
    if any(not c.verification.verified for c in claims if c.is_numeric) and not confidence.strip():
        confidence = "One or more figures could not be verified against XBRL data."
    memo = Memo(
        answer_summary=draft.answer_summary or "No answer produced.",
        claims=claims,
        confidence_notes=confidence,
        trace_id=state.get("trace_id") or str(uuid.uuid4()),
    )
    return {**state, "memo": memo, "notes": notes}


# --- claim construction and verification (T2.4) ---------------------------------

def build_claims(
    draft: DraftMemo, hits: list[Hit], conn: psycopg.Connection
) -> tuple[list[Claim], int]:
    """Turn draft claims into validated `Claim`s, verifying every figure against XBRL.

    A draft whose `source_index` is out of range, or whose quote is not actually in the
    cited chunk, is dropped rather than repaired — a citation we cannot substantiate is
    worse than a missing one.
    """
    claims: list[Claim] = []
    dropped = 0
    for item in draft.claims:
        if not 0 <= item.source_index < len(hits):
            dropped += 1
            continue
        hit = hits[item.source_index]
        quote = item.quote.strip()
        if not quote or quote not in hit.text:
            dropped += 1
            continue
        citation = Citation(
            accession_no=hit.accession_no, item_section=hit.item_section,
            chunk_id=hit.chunk_id, quote_span=quote,
        )
        verification = verify_claim(item, hit, conn)
        try:
            claims.append(Claim(
                text=item.text, value=item.value, unit=item.unit,
                period=item.period, citation=citation, verification=verification,
            ))
        except ValueError:
            dropped += 1  # violated the §3 contract; do not emit it
    return claims, dropped


def claim_fiscal_year(item: DraftClaim, hit: Hit) -> int:
    """The fiscal year the *claim* is about, not the year of the filing it was read in.

    Every 10-K prints prior-year comparatives, so a correct FY2024 figure is routinely
    quoted from the FY2025 filing. Verifying against the chunk's year marked exactly
    such a claim wrong in the first smoke run (D-0033).
    """
    # Not \b(20\d{2})\b: there is no word boundary in "FY2024" between Y and 2, so
    # the most common phrasing would silently fail to match.
    for year in re.findall(r"(?<!\d)(20\d{2})(?!\d)", item.period or ""):
        if int(year) in FISCAL_YEARS:
            return int(year)
    return hit.fiscal_year


def verify_claim(item: DraftClaim, hit: Hit, conn: psycopg.Connection) -> Verification:
    """Check a figure against XBRL. Prose claims get an extractive verification."""
    if item.value is None:
        return Verification(method="extractive", verified=True,
                            detail="quote matched the cited chunk")
    if not item.concept:
        return Verification(method="unverified", verified=False,
                            detail="no concept supplied for a numeric claim")
    fact = xbrl_lookup(conn, XbrlLookupInput(
        ticker=hit.ticker, concept=item.concept,
        fiscal_year=claim_fiscal_year(item, hit)))
    if not fact.found or fact.value is None:
        return Verification(method="unverified", verified=False, detail=fact.detail)
    scale = max(abs(fact.value), 1.0)
    ok = abs(item.value - fact.value) <= VERIFY_REL_TOLERANCE * scale
    return Verification(
        method="xbrl", verified=ok, concept=fact.resolved_concept,
        period_end=fact.period_end, expected_value=fact.value,
        detail=("matches XBRL" if ok else
                f"claimed {item.value:,.2f} vs XBRL {fact.value:,.2f}"),
    )


def needs_replan(state: AgentState) -> str:
    """Re-plan on empty retrieval or a failed numeric verification, bounded (§4.3)."""
    if state.get("replans", 0) >= MAX_REPLANS:
        return "done"
    memo = state.get("memo")
    if not state.get("hits"):
        return "replan"
    if memo and memo.unverified_numeric_claims:
        return "replan"
    return "done"


def bump_replan(state: AgentState) -> AgentState:
    memo = state.get("memo")
    reason = (
        "retrieval returned nothing" if not state.get("hits")
        else f"{len(memo.unverified_numeric_claims)} figure(s) failed XBRL verification"
        if memo else "no memo produced"
    )
    notes = [*(state.get("notes") or []), reason]
    return {**state, "replans": state.get("replans", 0) + 1, "notes": notes}


# --- graph assembly ------------------------------------------------------------

def build_graph(conn: psycopg.Connection, client: Any, encoder: Any):
    """Wire the nodes. The loop back through `plan` is what makes this an agent.

        plan -> retrieve -> facts -> memo -> (replan -> plan | END)
    """
    from langgraph.graph import END, StateGraph

    graph = StateGraph(AgentState)
    graph.add_node("plan", lambda s: plan_node(s, client=client))
    graph.add_node("retrieve", lambda s: retrieve_node(s, conn=conn, encoder=encoder))
    graph.add_node("facts", lambda s: facts_node(s, conn=conn))
    graph.add_node("memo", lambda s: memo_node(s, client=client, conn=conn))
    graph.add_node("replan", bump_replan)

    graph.set_entry_point("plan")
    graph.add_edge("plan", "retrieve")
    graph.add_edge("retrieve", "facts")
    graph.add_edge("facts", "memo")
    graph.add_conditional_edges("memo", needs_replan, {"replan": "replan", "done": END})
    graph.add_edge("replan", "plan")
    return graph.compile()


def answer(question: str, conn: psycopg.Connection, client: Any, encoder: Any) -> Memo:
    """Run one question end to end and return the validated memo."""
    app = build_graph(conn, client, encoder)
    final: AgentState = app.invoke({
        "question": question, "trace_id": str(uuid.uuid4()),
        "replans": 0, "notes": [], "hits": [], "facts": [],
    })
    memo = final.get("memo")
    if memo is None:
        raise RuntimeError("agent produced no memo")
    return memo
