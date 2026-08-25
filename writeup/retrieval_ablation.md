# T1.6 — Retrieval ablation (W1 done-condition)

Four retrieval configurations over the same 50 labelled query→section pairs, the same
corpus (9,449 chunks from 64 filings), and the same scoring harness.

| Configuration | recall@5 | MRR | latency (50 queries) |
|---|---:|---:|---:|
| lexical — Postgres FTS | 0.140 | 0.107 | 1.3 s |
| dense — BGE-M3 + pgvector | 0.380 | 0.246 | 0.1 s |
| hybrid — RRF fusion | 0.400 | 0.265 | 1.3 s |
| **hybrid + bge-reranker-v2-m3** | **0.500** | **0.357** | 126.4 s |

Each stage improves on the last, and the reranker is the largest single gain
(+0.10 recall@5, +0.09 MRR) at ~100× the latency — the cost/quality tradeoff the
agent's design has to make explicitly.

## How to read these numbers

- **The task is hard by construction.** A case is scored correct only if the right
  *(filing, section)* appears in the top 5 chunks, out of ~320 section-instances
  across 64 filings. Chunk-level near-misses inside the right document score zero.
- **Relevance is section-level.** MD&A is ~140 chunks; requiring one exact chunk would
  penalise a retriever for returning an adjacent paragraph that answers just as well.
- **No metadata filtering on any arm.** Filtering by ticker/period is an agent-level
  capability (Arm C at query time); applying it here would measure the filter rather
  than the retriever, and is unavailable to the baselines.
- **Queries are template-generated, not third-party annotations.** They use analyst
  vocabulary and never reuse the target section's wording — see D-0029 and the
  provenance note below.

## Two bugs this table caught before it was believed

1. **Lexical scored exactly 0.000** in the first run. `plainto_tsquery` ANDs every term,
   and filings write "Apple Inc." rather than "AAPL", so the conjunction matched nothing.
   Fixed to OR semantics (D-0028). An arm scoring exactly zero is a defect report.
2. **Queries used ticker symbols.** Switching to company names — identical labels,
   before any tuning — moved dense recall@5 from 0.220 to 0.380 (D-0029). The eval had
   been measuring a vocabulary mismatch.

## Reproduce

```bash
docker compose -f infra/docker/docker-compose.yml up -d
uv run python scripts/embed_corpus.py        # ~14 min on Apple MPS
uv run python scripts/run_retrieval_eval.py
```
