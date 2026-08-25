# T1.6 — Retrieval ablation (W1 done-condition)

Four retrieval configurations over the same 50 labelled query→section pairs, the same
corpus (**14,112 chunks** from 64 filings, median 461 tokens), and the same scoring
harness. Only the retrieval function varies.

| Configuration | recall@5 | MRR | latency (50 q) |
|---|---:|---:|---:|
| lexical — Postgres FTS | 0.100 | 0.059 | 1.4 s |
| dense — BGE-M3 + pgvector | 0.400 | **0.352** | 0.1 s |
| hybrid — RRF fusion | **0.420** | 0.280 | 1.7 s |
| hybrid + bge-reranker-v2-m3 | **0.420** | 0.315 | 92.4 s |

## The result that isn't what you'd expect

**Dense alone has the best MRR (0.352) — better than hybrid (0.280) and better than
hybrid + rerank (0.315).**

Fusing a weak retriever *degrades* ranking. Lexical here is poor (MRR 0.059), and RRF
gives every input list equal weight, so 25 mediocre lexical candidates pull good dense
results down the fused ranking. Hybrid buys +0.02 recall@5 and costs −0.07 MRR.

The reranker recovers part of that (0.280 → 0.315) but does not get back to dense alone.
It reorders the top-5 in **8 of 8** sampled cases — this is a real effect, not a no-op.

**Practical reading:** for this corpus and these queries, dense retrieval alone is the
best rank-quality-per-millisecond choice, and hybrid is only justified if the marginal
recall matters more than precision at rank 1. That is the opposite of the usual
"hybrid always wins" assumption, and it is what the ablation exists to find out.

## The two arms that tie at 0.420

`hybrid` and `hybrid + rerank` scoring identically on recall@5 was treated as a suspected
bug (D-0028: an arm scoring exactly equal to another is a defect report until proven
otherwise). Verified as coincidence — the reranker changes the top-5 in every sampled
case, and the MRR difference (0.280 vs 0.315) confirms the orderings differ.

## Chunk size is itself a variable

These numbers come from **461-token chunks**. An earlier run on ~757-token chunks scored
the top arm at 0.500 recall@5 versus 0.420 here — larger chunks were *better* on this
metric, because a bigger chunk is more likely to contain the labelled section.

The corpus was rebuilt to 461 tokens because PROPOSAL.md §4.4 specifies 512-token chunks
for Arm B and the previous figure was a mis-calibration, not a choice (D-0031). But the
comparison suggests **chunk size deserves its own ablation row** rather than being fixed
by fiat. Logged as future work; not run here to avoid tuning retrieval against the eval
set before the agent exists.

## How to read these numbers

- **Hard by construction.** A case scores only if the right *(filing, section)* appears in
  the top 5 chunks, out of ~320 section-instances across 64 filings.
- **Relevance is section-level.** MD&A is hundreds of chunks; demanding one exact chunk
  would penalise a retriever for returning an adjacent paragraph that answers just as well.
- **No metadata filtering on any arm.** Filtering by ticker/period is an agent capability
  (Arm C at query time); applying it here would measure the filter, not the retriever.
- **Queries are template-generated, not third-party annotations** — analyst vocabulary,
  never reusing the target section's wording (D-0029).

## Two bugs this table caught before it was believed

1. **Lexical scored exactly 0.000** on the first run. `plainto_tsquery` ANDs every term and
   filings write "Apple Inc." rather than "AAPL", so the conjunction matched nothing.
   Fixed to OR semantics (D-0028).
2. **Queries used ticker symbols.** Switching to company names — identical labels, before
   any tuning — moved dense recall@5 from 0.220 to 0.380 (D-0029).

## Reproduce

```bash
docker compose -f infra/docker/docker-compose.yml up -d
uv run python scripts/rebuild_index.py       # ~20 min on Apple MPS
uv run python scripts/run_retrieval_eval.py
```
