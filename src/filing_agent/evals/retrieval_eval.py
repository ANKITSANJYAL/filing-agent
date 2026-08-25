"""Retrieval scoring: recall@k and MRR over query -> section pairs (PROPOSAL.md §4.2).

Relevance is judged at **section** granularity, not chunk. A 10-K's MD&A becomes ~140
chunks; demanding one specific chunk would score a retriever wrong for returning an
adjacent paragraph that answers the question just as well. The section is the unit a
citation names, so it is the unit relevance is defined on.

The four configurations under comparison share this harness, so a difference in the
W1 table is a difference in retrieval and not in how it was measured.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Final

from pydantic import BaseModel

from ..retrieval.search import Hit

RETRIEVAL_CASES_PATH: Final[Path] = Path("evals/retrieval_pairs.jsonl")
DEFAULT_K: Final[int] = 5


class RetrievalCase(BaseModel):
    """One labelled query and the filing sections that should answer it."""

    case_id: str
    query: str
    # (accession_no, item_section) pairs. More than one is allowed: a question about a
    # trend legitimately spans both fiscal years.
    relevant: list[tuple[str, str]]
    tickers: tuple[str, ...] = ()
    fiscal_years: tuple[int, ...] = ()

    @property
    def relevant_keys(self) -> set[tuple[str, str]]:
        return {(a, s) for a, s in self.relevant}


class RetrievalMetrics(BaseModel):
    config: str
    n_cases: int
    recall_at_k: float
    mrr: float
    k: int = DEFAULT_K

    def as_row(self) -> str:
        return (f"{self.config:<22}{self.recall_at_k:>10.3f}{self.mrr:>10.3f}"
                f"{self.n_cases:>8}")


def _keys(hits: Sequence[Hit]) -> list[tuple[str, str]]:
    return [(h.accession_no, h.item_section) for h in hits]


def recall_at_k(hits: Sequence[Hit], case: RetrievalCase, k: int = DEFAULT_K) -> float:
    """Fraction of a case's relevant sections that appear in the top k hits.

    Set-based rather than binary: a trend question with two relevant sections should
    score 0.5 when one is found, not 1.0.
    """
    if not case.relevant_keys:
        return 0.0
    found = set(_keys(hits[:k])) & case.relevant_keys
    return len(found) / len(case.relevant_keys)


def reciprocal_rank(hits: Sequence[Hit], case: RetrievalCase) -> float:
    """1/rank of the first relevant section, or 0 if none is retrieved."""
    for rank, key in enumerate(_keys(hits), start=1):
        if key in case.relevant_keys:
            return 1.0 / rank
    return 0.0


def evaluate(
    name: str,
    retrieve: Callable[[RetrievalCase], Sequence[Hit]],
    cases: Sequence[RetrievalCase],
    k: int = DEFAULT_K,
) -> RetrievalMetrics:
    """Score one configuration. `retrieve` is the only thing that varies across arms."""
    if not cases:
        raise ValueError("no retrieval cases to evaluate")
    recalls, rrs = [], []
    for case in cases:
        hits = list(retrieve(case))
        recalls.append(recall_at_k(hits, case, k))
        rrs.append(reciprocal_rank(hits, case))
    return RetrievalMetrics(
        config=name, n_cases=len(cases), k=k,
        recall_at_k=sum(recalls) / len(recalls),
        mrr=sum(rrs) / len(rrs),
    )


def format_table(results: Sequence[RetrievalMetrics]) -> str:
    """The W1 done-condition table (PROPOSAL.md §7)."""
    k = results[0].k if results else DEFAULT_K
    header = f"{'CONFIG':<22}{f'recall@{k}':>10}{'MRR':>10}{'cases':>8}"
    lines = [header, "-" * len(header)]
    lines += [r.as_row() for r in results]
    return "\n".join(lines)


def write_cases(cases: Sequence[RetrievalCase], path: Path = RETRIEVAL_CASES_PATH) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for case in sorted(cases, key=lambda c: c.case_id):
            fh.write(case.model_dump_json() + "\n")
    return path


def read_cases(path: Path = RETRIEVAL_CASES_PATH) -> list[RetrievalCase]:
    with path.open(encoding="utf-8") as fh:
        return [RetrievalCase(**json.loads(line)) for line in fh if line.strip()]
