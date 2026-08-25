"""Retrieval-metric tests. Pure functions — the W1 table is only as trustworthy as these."""

import pytest

from filing_agent.evals.retrieval_eval import (
    RetrievalCase,
    evaluate,
    format_table,
    read_cases,
    recall_at_k,
    reciprocal_rank,
    write_cases,
)
from filing_agent.retrieval.search import Hit

ACC_A, ACC_B = "0001045810-25-000023", "0001045810-24-000029"


def _hit(accession: str = ACC_A, section: str = "MDA", chunk: str = "c1") -> Hit:
    return Hit(chunk_id=chunk, accession_no=accession, ticker="NVDA", fiscal_year=2025,
               item_section=section, text="t", score=1.0)


def _case(relevant=None, **kw) -> RetrievalCase:
    base = dict(case_id="r-001", query="data center revenue growth",
                relevant=relevant if relevant is not None else [(ACC_A, "MDA")])
    return RetrievalCase(**{**base, **kw})


# --- recall@k ------------------------------------------------------------------

def test_relevance_is_section_level_not_chunk_level() -> None:
    """Any chunk of the right section counts — a neighbouring paragraph still answers."""
    hits = [_hit(chunk="a-totally-different-chunk-id")]
    assert recall_at_k(hits, _case(), k=5) == 1.0


def test_partial_credit_when_only_one_of_two_sections_is_found() -> None:
    """A trend question spans two years; finding one is half the answer, not all of it."""
    case = _case(relevant=[(ACC_A, "MDA"), (ACC_B, "MDA")])
    assert recall_at_k([_hit(ACC_A)], case, k=5) == 0.5


def test_hits_below_k_do_not_count() -> None:
    hits = [_hit(section="RISK_FACTORS") for _ in range(5)] + [_hit()]
    assert recall_at_k(hits, _case(), k=5) == 0.0
    assert recall_at_k(hits, _case(), k=6) == 1.0


def test_no_relevant_hits_scores_zero() -> None:
    assert recall_at_k([_hit(section="BUSINESS")], _case(), k=5) == 0.0


def test_empty_result_scores_zero_rather_than_erroring() -> None:
    assert recall_at_k([], _case(), k=5) == 0.0


# --- MRR -----------------------------------------------------------------------

def test_reciprocal_rank_uses_the_first_relevant_position() -> None:
    hits = [_hit(section="BUSINESS"), _hit(section="RISK_FACTORS"), _hit()]
    assert reciprocal_rank(hits, _case()) == pytest.approx(1 / 3)


def test_reciprocal_rank_is_one_when_first_hit_is_relevant() -> None:
    assert reciprocal_rank([_hit()], _case()) == 1.0


def test_reciprocal_rank_is_zero_when_nothing_relevant_is_retrieved() -> None:
    assert reciprocal_rank([_hit(section="BUSINESS")], _case()) == 0.0


def test_mrr_ignores_relevant_hits_after_the_first() -> None:
    """MRR measures how soon the first useful result appears, not how many there are."""
    one = [_hit(), _hit(ACC_B)]
    two = [_hit()]
    assert reciprocal_rank(one, _case()) == reciprocal_rank(two, _case())


# --- Harness -------------------------------------------------------------------

def test_evaluate_averages_across_cases() -> None:
    cases = [_case(case_id="a"), _case(case_id="b", relevant=[(ACC_B, "MDA")])]

    def retrieve(case):  # finds the first case only
        return [_hit()] if case.case_id == "a" else [_hit(section="BUSINESS")]

    metrics = evaluate("lexical", retrieve, cases, k=5)
    assert metrics.recall_at_k == 0.5 and metrics.mrr == 0.5 and metrics.n_cases == 2


def test_evaluate_rejects_an_empty_case_set() -> None:
    """Scoring zero cases would report a perfect-looking 0.0 for every arm."""
    with pytest.raises(ValueError, match="no retrieval cases"):
        evaluate("lexical", lambda c: [], [], k=5)


def test_same_harness_scores_every_arm() -> None:
    """Arms must differ only in the retrieve function, or the table compares harnesses."""
    cases = [_case()]
    perfect = evaluate("hybrid", lambda c: [_hit()], cases)
    useless = evaluate("lexical", lambda c: [], cases)
    assert perfect.recall_at_k == 1.0 and useless.recall_at_k == 0.0
    assert perfect.k == useless.k


def test_table_lists_every_configuration() -> None:
    rows = [evaluate(n, lambda c: [_hit()], [_case()]) for n in ("lexical", "dense")]
    table = format_table(rows)
    assert "lexical" in table and "dense" in table and "recall@5" in table


def test_cases_round_trip(tmp_path) -> None:
    cases = [_case(), _case(case_id="r-002", relevant=[(ACC_B, "RISK_FACTORS")])]
    path = write_cases(cases, tmp_path / "pairs.jsonl")
    assert [c.model_dump() for c in read_cases(path)] == [c.model_dump() for c in cases]
