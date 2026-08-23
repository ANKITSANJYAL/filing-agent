# filing-agent

A research agent over SEC filings where **every numeric claim is automatically verified
against EDGAR's structured XBRL data**, and every claim carries a citation to the exact
filing section it came from.

Most retrieval-augmented systems can only be graded by another model. SEC filings are
unusual: the same facts exist as free text *and* as machine-readable XBRL, so a numeric
answer can be checked programmatically with no LLM judge in the loop. This project is
built around that property.

> **Status: in progress.** The data layer (T1.1–T1.3) and the tier-1 evaluation set are
> built and measured. Retrieval, the agent, and the ablation are not yet built. Progress
> is tracked honestly in [`TRACKER.md`](TRACKER.md) — including what is unfinished.

---

## The two experiments this is designed to answer

**1. Does long context kill RAG?** The same question set answered three ways, with the
model held constant so *architecture* is the only variable:

| Arm | Approach |
|---|---|
| A | Long-context — stuff whole filings into a 1M-token window |
| B | Naive RAG — fixed 512-token chunks, dense top-k |
| C | Hybrid agentic — structure-aware chunks, BM25 + dense + rerank, verification tools |

**2. The quality–latency frontier.** A self-hosted 8B model on vLLM walked down an
optimisation ladder (FP8 → prefix caching → speculative decoding) with **evaluation
accuracy measured at every rung**, not just throughput.

---

## What is built and measured today

| Stage | Result |
|---|---|
| EDGAR fetch | 64 filings (8 tickers × FY2024–25), 230.8 MB, rate-limited and cache-first |
| Extraction | 231 MB markup → 17.0 MB text; iXBRL machine metadata removed |
| Sectioning | 64/64 filings pass ordering + substance assertions |
| Chunking | 9,449 chunks, median ~477 tokens, table rows never split |
| XBRL facts | 2,010 resolved facts, restatements collapsed |
| Tier-1 evals | 72 frozen questions, graded by exact match — zero LLM grading |
| Tests | 127 passing |

Corpus: NVDA, AAPL, MSFT, JPM, XOM, PFE, WMT, COST — deliberately cross-sector.

---

## Five things the data turned out to disagree with

Each of these produced **no error** — a naive pipeline would have shipped a corpus that
looked fine and was wrong. Full write-ups in [`DECISIONS.md`](DECISIONS.md).

**1. A ticker silently resolved to the wrong company.** SEC's ticker map points `XOM` at
a successor holding entity registered in 2026 with no filing history. Every FY2024–25
Exxon filing sits under the predecessor CIK. Resolution was *correct per SEC's map* and
returned zero filings. → [D-0008]

**2. "Recent filings" is capped in documents, not years.** JPMorgan files ~25k documents
a year, so its inline window reached back only 12 months and all of FY2024 lived in 69
unread overflow files. → [D-0009]

**3. A third of extracted "text" was machine metadata.** Naive tag-stripping ingests the
inline-XBRL header block — 125,940 of 381,767 characters on NVIDIA's 10-K were entity
IDs and axis members no human reader ever sees. They formed the two densest "numeric"
chunks in the document. → [D-0014]

**4. Item headings often don't contain their content.** JPMorgan and Exxon incorporate
MD&A by reference to page ranges; NVIDIA files its statements under Item 15, leaving
Item 8 with two lines. Measured: 48 of 64 section-slots are substantive. → [D-0016]

**5. There is no revenue concept common to all eight companies.** `Revenues` for five,
`RevenueFromContractWithCustomerExcludingAssessedTax` for Apple and Microsoft,
`RevenuesNetOfInterestExpense` for JPMorgan — the correct concept for a bank, not a
workaround. And NVIDIA's 10-for-1 split means FY2023 EPS is *both* 11.93 and 1.19
depending on which filing you read. → [D-0019], [D-0020]

The recurring lesson, now the house pattern: **correctness assertions say nothing about
completeness.** An empty result set satisfies every "is this right?" check trivially, so
each ingest boundary asserts both.

---

## Architecture

```
EDGAR ──► fetch (rate-limited, cache-first, content-hashed)
            │
            ├──► HTML ──► extract ──► sections ──► chunks ──┐
            │                                               ├──► Postgres + pgvector
            └──► XBRL companyfacts ──► resolved facts ──────┘         │
                                            │                          ▼
                                            │              lexical + dense + RRF + rerank
                                            │                          │
                                            ▼                          ▼
                                    tier-1 answer key  ◄────  LangGraph agent ──► cited memo
                                    (exact-match grading)
```

Layout: [`src/filing_agent/`](src/filing_agent/) — `ingest/`, `retrieval/`, `agent/`,
`evals/`, `serving/`, `latency/`.

---

## Reproducing the corpus

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
uv sync --extra dev

cp .env.example .env      # set SEC_USER_AGENT — SEC returns 403 without one
uv run pytest -q          # 127 tests, no network required
```

The corpus itself is **not** committed (230 MB of regenerable HTML). What *is* committed
is [`data/raw/manifest.jsonl`](data/raw/manifest.jsonl) — every filing's accession,
period, and SHA-256 — so a re-fetch can be verified byte-for-byte against the corpus
that produced any published number.

---

## Design notes

- **Assertions over warnings.** Every external boundary hard-fails on violated
  expectations. Warnings get read once; raises stop the pipeline. Tests verify code,
  assertions verify data.
- **Table rows are structurally unsplittable.** Extraction emits one row per line and
  chunking is line-granular, so a figure can never be separated from its label — the
  failure that would undermine numeric verification.
- **Negative results are documented.** [D-0016] records a content-anchored sectioning
  approach that was built, measured, failed to converge, and was rejected with evidence.
- **Known exceptions are data, not folklore.** By-reference filings and restated facts
  are allowlisted in [`config.py`](src/filing_agent/config.py); anything unlisted
  hard-fails.

---

## Disclaimer

This is a **research and citation tool**, not investment advice. It summarises and cites
public SEC filings. Outputs may be incomplete or wrong. Do not use it to make investment
decisions. No affiliation with or endorsement by the SEC or any company in the corpus.

Filing data is retrieved from EDGAR under SEC's fair-access policy: a declared
User-Agent and a self-imposed 8 req/s ceiling against their 10 req/s limit.

---

Author: [Ankit Sanjyal](https://github.com/ANKITSANJYAL) ·
Decision log: [`DECISIONS.md`](DECISIONS.md) · Progress: [`TRACKER.md`](TRACKER.md)

[D-0008]: DECISIONS.md
[D-0009]: DECISIONS.md
[D-0014]: DECISIONS.md
[D-0016]: DECISIONS.md
[D-0019]: DECISIONS.md
[D-0020]: DECISIONS.md
