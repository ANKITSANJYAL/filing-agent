"""The T1.6 four-arm retrieval ablation (PROPOSAL.md §4.2 / §7 W1 done-condition).

No metadata filters on any arm. Filtering by ticker/period is an agent-level capability
(Arm C uses it at query time); applying it here would measure the filter rather than
the retriever, and would not be available to the lexical and dense baselines.
"""
import time

from filing_agent.evals.retrieval_eval import evaluate, format_table, read_cases
from filing_agent.retrieval import db
from filing_agent.retrieval.embed import load_encoder
from filing_agent.retrieval.search import (
    CANDIDATE_MULTIPLIER, dense_search, hybrid_search, lexical_search,
    load_reranker, rerank,
)

K = 5
cases = read_cases()
conn = db.connect()
encoder = load_encoder()
vectors = {c.case_id: v for c, v in zip(
    cases, encoder.encode([c.query for c in cases], normalize_embeddings=True), strict=True)}
reranker = load_reranker()

arms = {
    "lexical (FTS)":     lambda c: lexical_search(conn, c.query, K),
    "dense (BGE-M3)":    lambda c: dense_search(conn, vectors[c.case_id], K),
    "hybrid (RRF)":      lambda c: hybrid_search(conn, c.query, vectors[c.case_id], K),
    "hybrid + rerank":   lambda c: rerank(
        reranker, c.query,
        hybrid_search(conn, c.query, vectors[c.case_id], K * CANDIDATE_MULTIPLIER), K),
}

results = []
for name, fn in arms.items():
    t0 = time.time()
    results.append(evaluate(name, fn, cases, k=K))
    print(f"  {name:<20} {time.time()-t0:6.1f}s", flush=True)

print()
print(format_table(results))
conn.close()
