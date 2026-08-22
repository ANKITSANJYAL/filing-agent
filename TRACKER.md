# TRACKER.md

Single source of truth for progress. Structure mirrors `PROPOSAL.md` §10.

**Statuses:** `NOT_STARTED` · `IN_PROGRESS` · `BLOCKED` · `CODED` · `EXPLAINED` · `DONE`
**`DONE` requires `EXPLAINED` first.** Code that works but wasn't walked through is not done.

---

## ⚠️ Explanation Debt

*Items that are `CODED` but not yet `EXPLAINED`. Nag until empty.*

- `T1.1a` — EDGAR transport layer. Walkthrough delivered 2026-08-14; **recap unanswered**.
- `T1.1b` — Filing index + assertions. Walkthrough delivered 2026-08-19; **recap unanswered**.
- `T1.1c` — Corpus download + manifest. Walkthrough delivered 2026-08-19; **recap unanswered**.
- `T1.2a` — HTML extraction. Walkthrough delivered 2026-08-21; **recap unanswered**.
- `T1.2b` — Section detection. Walkthrough delivered 2026-08-21; **recap unanswered**.
- `T1.2c` — Chunker. Walkthrough delivered 2026-08-21; **recap unanswered**.
- `T1.3` — XBRL facts. Walkthrough delivered 2026-08-21; **recap unanswered**.
- `T3.1` — Tier-1 eval set. Walkthrough delivered 2026-08-22; **recap unanswered**.

All eight stay `CODED` rather than `DONE` until the recap questions are answered (CLAUDE.md §1.5).

---

## 🚧 Blocked — environment

*Visible rather than remembered. Both are human install steps; neither blocks T1.1.*

| ID | Item | Status | Blocking | Unblock with |
|---|---|---|---|---|
| E1 | Docker Desktop not installed | `BLOCKED` | T1.4 (Postgres/pgvector), T4.1 (Langfuse) | Install Docker Desktop for Mac; verify `docker --version` |
| E2 | `uv` not installed | `BLOCKED` | any dependency install / test run | `curl -LsSf https://astral.sh/uv/install.sh \| sh` then `uv sync --extra dev` |
| E3 | Python 3.12 not present (system has 3.14) | `BLOCKED` | pinned interpreter per §8.14 | resolved automatically by `uv sync` (pyproject pins `==3.12.*`) |
| E4 | `psql` client not installed | `BLOCKED` | T1.4 manual DB inspection | ships with Docker Postgres image; or `brew install libpq` |

---

## T1 — Data & Retrieval

| ID | Item | Status | Notes |
|---|---|---|---|
| T1.1a | EDGAR transport: token bucket, UA validation, cache-first client | `CODED` | 15 tests pass; live SEC smoke OK. Awaiting recap. |
| T1.1b | Filing index: CIK resolution, submissions parsing, fiscal + completeness assertions | `CODED` | 40 tests pass. Live: 64 filings, 8x(2 10-K + 6 10-Q), both assertions green. |
| T1.1c | Document download + manifest + integrity gate | `CODED` | 53 tests pass. 64 filings / 230.8 MB on disk; re-run costs 0 requests. |
| T1.2a | HTML extraction: iXBRL stripping, table geometry | `CODED` | 67 tests pass. 231 MB -> 17.0 MB text in 7.4s; both assertions green on all 64. |
| T1.2b | Item-anchored section detection + substance assertion | `CODED` | 79 tests pass. 64/64 filings pass both assertions. |
| T1.2c | Chunking + per-chunk metadata | `CODED` | 92 tests pass. 9,449 chunks, median ~477 tok, all pass assert_chunks_valid. |
| T1.3 | XBRL facts ingestion | `CODED` | 107 tests pass. 2,010 resolved facts, 8 tickers, all 3 assertions green. |
| T3.1 | *(pulled forward — D-0003)* tier-1 XBRL set | `CODED` | 127 tests pass. 72 frozen questions committed; 564-question pool. |
| T1.4 | Postgres/pgvector schema | `BLOCKED` | needs E1 (Docker) |
| T1.5 | Lexical + dense + RRF + rerank pipeline | `NOT_STARTED` | |
| T1.6 | 50-pair retrieval eval + ablation table | `NOT_STARTED` | W1 done-condition |

## T2 — Agent & MCP

| ID | Item | Status | Notes |
|---|---|---|---|
| T2.1 | Pydantic schemas (Claim, Memo, tool I/O) | `NOT_STARTED` | |
| T2.2 | LangGraph graph + typed state | `NOT_STARTED` | |
| T2.3 | Tools (retrieve, xbrl_lookup, calculator) | `NOT_STARTED` | |
| T2.4 | Verification / re-plan loop | `NOT_STARTED` | max 3 re-plans |
| T2.5 | MCP server | `NOT_STARTED` | |
| T2.6 | FastAPI service + auth/rate limit | `NOT_STARTED` | |

## T3 — Evals & Ablation

| ID | Item | Status | Notes |
|---|---|---|---|
| T3.1 | Tier-1 XBRL set (72) | `CODED` | **moved to T1 block** (D-0003). Frozen set committed. |
| T3.2 | Tier-2 extractive set (~30) | `NOT_STARTED` | |
| T3.3 | FinanceBench mapping + run | `NOT_STARTED` | 1.5-day effort cap (§9) |
| T3.4 | Judge + calibration (κ) | `NOT_STARTED` | |
| T3.5 | Citation-faithfulness checker | `NOT_STARTED` | |
| T3.6 | CI regression gate | `NOT_STARTED` | red-build screenshot is a deliverable |
| T3.7 | Three-arm ablation run + table | `NOT_STARTED` | model frozen at first run (D-0002b) |
| T3.8 | Injection defense + poisoned-filing test | `NOT_STARTED` | |

## T4 — Ship & Tell

| ID | Item | Status | Notes |
|---|---|---|---|
| T4.1 | Langfuse + dashboard | `BLOCKED` | needs E1 (Docker) |
| T4.2 | Routing/caching optimization (before/after) | `NOT_STARTED` | |
| T4.3 | Terraform + k3s + CI/CD deploy | `NOT_STARTED` | |
| T4.4 | React UI | `NOT_STARTED` | first on the cut list (§9) |
| T4.5 | Write-up part 1 | `NOT_STARTED` | |
| T4.6 | Portfolio / resume update | `NOT_STARTED` | |

## T5 — Latency Ladder

| ID | Item | Status | Notes |
|---|---|---|---|
| T5.1 | Local dry-run scripts | `NOT_STARTED` | prepare before renting GPU (§9) |
| T5.2 | vLLM baseline on GPU | `NOT_STARTED` | $60 hard cap total |
| T5.3 | FP8 rung | `NOT_STARTED` | lossy — accuracy may shift |
| T5.4 | Prefix-caching rung | `NOT_STARTED` | |
| T5.5 | Spec-decode rung | `NOT_STARTED` | lossless — flat accuracy is the sanity check |
| T5.6 | Frontier table + write-up part 2 | `NOT_STARTED` | |

---

## Session Log

| Date | Item | Outcome |
|---|---|---|
| 2026-08-14 | Session 0 — scaffold | Repo initialized per CLAUDE.md §5; TRACKER + DECISIONS created; D-0001..D-0004 logged; env gaps E1–E4 recorded as BLOCKED. |
| 2026-08-19 | T1.1a/b | Transport + filing index coded. Live run exposed two silent corpus defects (XOM successor CIK, JPM overflow window); both fixed, completeness assertion added, 8-Ks dropped. Corpus: 64 filings. |
| 2026-08-19 | T1.1c | Corpus downloaded: 64 filings, 230.8 MB, fingerprint 4c6b9df2. Manifest committed (27 KB) as the reproducibility artifact; raw HTML gitignored. |
| 2026-08-21 | T1.2a | Failure demo first: naive stripping made 33% of NVDA text into iXBRL metadata, and 512-word chunks severed table rows. Built extractor dropping ix: subtrees + preserving row geometry. |
| 2026-08-21 | T1.2b | Item headings proven unreliable as content boundaries (48/64 slots substantive). Content-anchoring attempted, failed to converge, rejected with evidence. Shipped Item anchors + per-ticker stub allowlist: 64/64 pass. |
| 2026-08-21 | T1.2c | Chunker shipped: 9,449 chunks across 64 filings, table rows structurally unsplittable, whole-document coverage so NVDA's displaced statements stay retrievable. T1.2 complete. |
| 2026-08-21 | T1.3 | XBRL ingested: 2,971 raw -> 2,010 resolved facts. Found NVDA 10:1 split restating all per-share figures, and that no revenue concept is common to all 8 tickers. T3.1 now unblocked. |
| 2026-08-22 | T3.1 | Tier-1 eval set generated from XBRL facts: 564 pool, 72 frozen + committed. Two grading bugs found by tests (tolerance too loose, then float dust from binary subtraction); fixed with Decimal arithmetic. |
