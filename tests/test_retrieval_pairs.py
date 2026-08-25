"""Tests for the labelled retrieval pairs — mostly guarding against a biased eval set."""

import re

import pytest

from filing_agent.config import STUB_SECTION_ALLOWLIST
from filing_agent.evals.retrieval_eval import read_cases
from filing_agent.evals.retrieval_pairs import SECTION_ORDER, TEMPLATES

pytest.importorskip("pydantic")

CASES_FILE = "evals/retrieval_pairs.jsonl"


@pytest.fixture(scope="module")
def cases():
    try:
        loaded = read_cases()
    except FileNotFoundError:
        pytest.skip(f"{CASES_FILE} not generated")
    if not loaded:
        pytest.skip("no cases")
    return loaded


# --- No vocabulary leakage (the rule that keeps the ablation fair) --------------

def test_every_query_is_a_verbatim_template() -> None:
    """Queries must never be derived from the text they should retrieve.

    A query built from its target section inherits that section's vocabulary, and
    lexical retrieval then wins by construction — the table would report a leak as a
    result. Checking the queries are pure templates makes that impossible by design.
    """
    patterns = [
        re.compile("^" + re.escape(t).replace(r"\{ticker\}", r"[A-Z]+")
                   .replace(r"\{year\}", r"\d{4}") + "$")
        for templates in TEMPLATES.values() for t in templates
    ]
    loaded = read_cases()
    for case in loaded:
        assert any(p.match(case.query) for p in patterns), case.query


def test_no_ticker_gets_bespoke_phrasing(cases) -> None:
    """Fairness across companies: the same section is asked the same way everywhere.

    (Overlap between a template and its section *label* — "financial statements" — is
    not leakage; it is the vocabulary any analyst would use. The guard that matters is
    that queries are pure templates, which the test above enforces.)
    """
    by_section: dict[str, set[str]] = {}
    for case in cases:
        section = case.relevant[0][1]
        skeleton = case.query.replace(case.tickers[0], "{T}").replace(
            str(case.fiscal_years[0]), "{Y}"
        )
        by_section.setdefault(section, set()).add(skeleton)
    for section, skeletons in by_section.items():
        assert skeletons <= {
            t.replace("{ticker}", "{T}").replace("{year}", "{Y}") for t in TEMPLATES[section]
        }, section


# --- Stub sections excluded (D-0016) -------------------------------------------

def test_no_case_targets_an_allowlisted_stub_section(cases) -> None:
    """Asking retrieval to find a two-line cross-reference measures the corpus, not
    the retriever (JPM/XOM MD&A, NVDA financial statements)."""
    for case in cases:
        ticker = case.tickers[0]
        section = case.relevant[0][1]
        assert section not in STUB_SECTION_ALLOWLIST.get(ticker, frozenset()), case.case_id


# --- Sample balance ------------------------------------------------------------

def test_all_eight_tickers_are_represented(cases) -> None:
    """An earlier round-robin dropped XOM entirely — one of the by-reference filers,
    which is exactly the hard case the eval must cover."""
    assert len({c.tickers[0] for c in cases}) == 8


def test_every_section_is_represented(cases) -> None:
    """Section-order bias once collapsed MARKET_RISK to a single case."""
    covered = {c.relevant[0][1] for c in cases}
    assert covered == set(SECTION_ORDER)


def test_both_fiscal_years_are_covered(cases) -> None:
    assert {c.fiscal_years[0] for c in cases} == {2024, 2025}


def test_case_ids_and_queries_are_unique(cases) -> None:
    assert len({c.case_id for c in cases}) == len(cases)
    assert len({c.query for c in cases}) == len(cases)


def test_every_case_has_at_least_one_labelled_section(cases) -> None:
    for case in cases:
        assert case.relevant and all(a and s for a, s in case.relevant), case.case_id
