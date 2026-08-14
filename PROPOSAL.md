# PROPOSAL — Eval-First SEC Filings Research Agent (+ Inference Latency Module)

**Codename:** `filing-agent`
**Author:** Ankit Sanjyal
**Status:** Context document — final. All previously-open questions are now **decided** (§8). Do NOT begin coding from this file. Workflow, explanation protocol, and tracker discipline live in `CLAUDE.md`. Progress state lives in `TRACKER.md` (spec in §10).

**Stakes, stated plainly:** the builder has ~8 weeks to convert this into AI Engineer interviews in NYC. This project is the portfolio centerpiece. Scope discipline is survival, not style. When in doubt: smaller, measured, shipped, explained.

---

## 1. What this project is

A financial research agent that answers analyst-style questions over SEC filings — e.g., *"How has NVIDIA's gross margin trended over the last 8 quarters, and what risks did management newly disclose in the latest 10-K?"* — returning a **structured memo where every claim carries a citation to the exact filing section, and every numeric claim is automatically verified against SEC structured (XBRL) data.**

It is simultaneously **two measured experiments**:
1. **Architecture ablation:** the same question set answered three ways — long-context stuffing vs. naive RAG vs. hybrid agentic retrieval — with published accuracy / cost / latency.
2. **Latency ladder:** a self-hosted open model served with vLLM, optimized rung by rung (quantization → prefix caching → speculative decoding), with **eval accuracy reported at every rung** — the quality-latency frontier on a real workload.

## 2. Why this exists (career rationale — the scope police)

This is a career artifact targeting NYC AI Engineer roles. Live JDs (Bain/Coro, YouTube FDE, Capital One, Citi, Radixx, Deloitte) converge on six line items:

1. LLM application engineering (Python, FastAPI, structured outputs)
2. Advanced retrieval — hybrid search, reranking, metadata design
3. Agentic patterns — LangGraph, tool use, typed state, MCP
4. **Evals & observability** — golden datasets, calibrated judges, CI regression, tracing, cost/latency dashboards *(the builder's #1 resume gap and the market's #1 differentiator)*
5. Production deployment — Docker, Kubernetes, AWS, Terraform, CI/CD
6. Cost/latency/security engineering — routing, caching, quantization tradeoffs, prompt-injection defense

**Rule:** every task must map to one of these six. A proposed feature that maps to none is scope creep — flag it and refuse.

### Why SEC filings
- **Free programmatic ground truth:** XBRL structured facts on EDGAR let numeric claims be verified automatically — no LLM judging needed. Almost no other document domain offers this. It is what makes the eval story real.
- **Recognized public benchmark:** FinanceBench (Stanford/Patronus; 150 expert-annotated questions over 84 filings) — we report a score anyone can contextualize.
- **Genuinely unsolved:** published results show frontier models under ~50% on realistic financial-agent benchmarks and a ~70-point gap between perfect retrieval and naive RAG. Retrieval is the bottleneck; that's an engineering problem.
- **The NYC market's problem:** Hebbia, Rogo, Fintool, AlphaSense, and bank GenAI teams build exactly this. Competitors prove demand; we demonstrate rigor, not novelty.

### The two headline experiments
**(a) Does long context kill RAG?** 1M-token windows fit a whole 10-K, but cost/latency scale with prompt length and context-rot is documented. Three arms, same model, same questions — so the *architecture* is the only variable:

| Arm | Description |
|---|---|
| A | Long-context: stuff full relevant filing(s) into the 1M-context model |
| B | Naive RAG: fixed 512-token chunks → dense top-k → answer |
| C | Hybrid agentic: structure-aware chunks → BM25 + dense + rerank → LangGraph agent with verification tools |

Reported per arm: accuracy (all eval tiers), cost/query, p50/p95 TTFT & total latency, citation-faithfulness rate.

**(b) The quality-latency frontier.** Serving-level optimization of a small open model, with accuracy measured at each rung (§4.8). Almost nobody publishes the accuracy column next to the speedup column; we can, because we built the harness.

## 3. Product behavior

- **Input:** natural-language analyst question, optionally scoped to ticker(s)/period(s).
- **Output (Pydantic-enforced, never freeform):**
  - `answer_summary: str`
  - `claims: list[Claim]` — `Claim = {text, value?, unit?, period?, citation{accession_no, section, chunk_id, quote_span}, verification{method: xbrl|extractive|judged, verified: bool}}`
  - `confidence_notes: str` · `trace_id: str`
- **Hard rule:** a numeric claim failing XBRL verification is corrected via re-plan or emitted with `verified: false` — never silently.
- **Interfaces:** FastAPI REST · MCP server (same tools, demoable in Claude Desktop) · minimal React chat with clickable citations (≤ 1 weekend).
- **Disclaimer in README and UI:** research/citation tool; not investment advice.

## 4. Architecture

### 4.1 Data layer
- **Corpus (locked):** 8 tickers — NVDA, AAPL, MSFT, JPM, XOM, PFE, WMT, COST — × FY2024–FY2025: all 10-K and 10-Q filings plus 8-Ks with financial exhibits. ≈ 90–110 filings, deliberately cross-sector. Earnings transcripts: **phase 2 only.**
- **Source:** EDGAR full-text **HTML** (never PDF/OCR) + EDGAR `companyfacts`/XBRL frames APIs for structured facts. Respect SEC rate limits (10 req/s) and declared User-Agent. Use `edgartools` if it saves time; hand-rolled fetcher acceptable.
- **Chunking:** by document structure — Item 1A, Item 7, Item 7A, Item 8, etc. Metadata per chunk: `{ticker, cik, form_type, fiscal_period, filing_date, item_section, accession_no}`.

### 4.2 Retrieval layer (Arm C)
- **Lexical:** Postgres full-text search (`tsvector`/`ts_rank`) — one database, honest tradeoff vs. dedicated BM25 noted in DECISIONS.md.
- **Dense:** **BGE-M3** embeddings (local, free, strong multilingual/hybrid support) in **pgvector**.
- **Fusion + rerank:** reciprocal-rank fusion → **bge-reranker-v2-m3** cross-encoder (local).
- **Retrieval eval:** 50 hand-labeled query→section pairs; recall@5 and MRR for lexical-only / dense-only / hybrid / hybrid+rerank. Table goes in the write-up.

### 4.3 Agent layer
- **LangGraph**, single agent (multi-agent banned for v1 — defending that choice is itself a JD line).
- Graph: `planner → retrieve(filters) → xbrl_lookup(concept, period) → calculator → memo_writer`, re-plan loop on low retrieval confidence or failed verification, bounded iterations (max 3 re-plans).
- All tool contracts Pydantic; tools also exposed via **MCP server**.

### 4.4 Evaluation layer (largest time budget)
1. **XBRL numeric set (~70 Qs, self-built):** exact-match vs. EDGAR facts. Zero LLM grading. Doubles as the **CI regression set**.
2. **Extractive set (~30 Qs):** e.g., newly-added risk factors YoY — graded by programmatic section diff.
3. **FinanceBench open subset:** all open-source questions whose underlying filings we can map to EDGAR HTML by accession/ticker-period (target ≥ 100; document exclusions). Report the score.
4. **Synthesis set (~50 Qs, LLM-judged):** judge calibrated first — builder hand-labels 40–50 outputs, report judge–human agreement (Cohen's κ) before trusting judge numbers; document any judge failure mode found.
- **Citation-faithfulness checker:** automated pass that a cited chunk actually supports its claim; unsupported citation = failure even when the number is right.
- **CI gate:** GitHub Actions runs tier-1 + a 15-question tier-4 sample on every prompt/model/retrieval change; below-threshold ⇒ red build. A red-build screenshot from an "innocent" prompt tweak is a first-class deliverable.

### 4.5 Observability & cost
- **Langfuse** (self-hosted, Docker) tracing every run: per-node tokens, cost, latency.
- Dashboard: cost/query, token breakdown by node, p50/p95.
- One documented optimization with before/after: **model routing** (Haiku-tier for planning/classification, Sonnet-tier for synthesis) and semantic caching.

### 4.6 Security
- Retrieved filing text = untrusted input. Instruction/data separation in prompts; eval set includes ≥ 1 deliberately poisoned filing proving the defense.
- API-key auth + rate limiting on FastAPI. No PII in corpus (by design; stated in docs).

### 4.7 Deployment
- Docker → **k3s on a single EC2 instance** → AWS, Terraform-provisioned, GitHub Actions CI/CD (see §8 for why not EKS).

### 4.8 Inference latency module (the new headline #2)
- **Serve:** Llama-3.1-8B-Instruct on **vLLM**, one rented **L4 or A10G** GPU (RunPod/Lambda, ~$0.5–0.8/hr, hard budget cap $60 total — batch the work, kill the box between sessions).
- **Ladder, in order, each rung measured on the tier-1 + tier-4-sample eval set:**
  1. Baseline BF16, default vLLM
  2. **FP8 quantization** (weights + KV cache)
  3. **Prefix caching** (shared system/tool prompts)
  4. **Speculative decoding** (draft: Llama-3.2-1B; fall back to vLLM n-gram spec-decode if draft-model quality disappoints)
- **Report per rung:** TTFT, TPOT, tokens/s, cost/query, **and eval accuracy**. Deliverable: one table + short analysis = second headline of the write-up ("the quality-latency frontier on a real workload").
- **Scope guard:** no custom kernels, no CUDA, no fine-tuning, no multi-GPU. Config-level engineering, honestly measured, is the goal.

## 5. Non-goals (refuse these)
❌ >8 tickers or >2 fiscal years · ❌ fine-tuning · ❌ multi-agent · ❌ PDF/OCR parsing · ❌ polished frontend (>1 weekend) · ❌ real-time market data or investment advice · ❌ custom CUDA/kernels · ❌ beating Fintool/Hebbia — rigor, not novelty.

## 6. Success criteria
The builder can truthfully say, with links:
> *"I built a financial research agent where every numeric claim is auto-verified against SEC structured data. It scores X% on FinanceBench at $Y/query. Calibrated LLM judge (κ=Z), automated citation-faithfulness checks, CI that fails on quality regressions. I measured long-context vs. naive RAG vs. hybrid agentic retrieval — here's the table. I self-hosted an 8B model on vLLM and walked it down the latency ladder — FP8, prefix caching, speculative decoding — with accuracy measured at every rung. Deployed on k3s/AWS/Terraform with full tracing."*

Every clause maps to a JD line item. The sentence, repo, and write-up ARE the product.

## 7. Timeline (5 weeks; latency module is Week 5 and is cuttable if the job pipeline demands time)
- **W1 Data+Retrieval** — done when the recall@5/MRR table exists for all 4 retrieval configs.
- **W2 Agent+MCP** — done when an end-to-end question returns a schema-valid, cited memo and tools work from an MCP client.
- **W3 Evals+Ablation** — done when all 4 tiers run, judge κ reported, CI goes red on an induced regression, and the A/B/C table exists.
- **W4 Ship+Write-up** — done when a stranger can hit the deployed URL and the post is live (ablation table, κ, red-CI screenshot, architecture diagram).
- **W5 Latency ladder** — done when the 4-rung table (speed + accuracy) is published as write-up part 2.

## 8. Decisions — LOCKED (rationale one line each; changes require a DECISIONS.md entry)

| # | Decision | Choice | Why |
|---|---|---|---|
| 1 | Database | **Postgres + pgvector** (chunks, metadata, XBRL facts, eval results — one DB) | One system to operate; pgvector is JD-recognized; Qdrant adds ops for no interview value |
| 2 | Embeddings | **BGE-M3** (local) | Free, strong, no API dependency; finance-heavy corpora reward its hybrid design |
| 3 | Reranker | **bge-reranker-v2-m3** (local cross-encoder) | Free, standard, good; Cohere adds cost + vendor for marginal gain |
| 4 | Lexical arm | **Postgres FTS** | Keeps one database; tradeoff vs. true BM25 documented — that nuance is interview material |
| 5 | Agent/ablation model | **Claude Sonnet 4.6 for all three arms** (1M context for Arm A) | Same model across arms isolates *architecture* as the variable — correct experimental design; strong cost/quality |
| 6 | Routing model | **Claude Haiku 4.5** for planner/classifier nodes | The documented cost optimization (§4.5) |
| 7 | Judge model | **GPT-5.2-tier (OpenAI)** | Different family from system-under-test to reduce self-preference bias; calibrated vs. hand labels regardless |
| 8 | Doc coverage | 8 tickers × FY24–25, ~90–110 filings | Enough for cross-sector + FinanceBench overlap; more = ingestion swamp |
| 9 | FinanceBench scope | Open-source subset mappable to EDGAR HTML, target ≥100 Qs, exclusions documented | Honest, reproducible, avoids PDF parsing |
| 10 | k3s vs. EKS | **k3s on one EC2 (t3.xlarge-class)** | Same manifests/story, ~$70+/mo cheaper than EKS control plane + nodes; say "identical manifests run on EKS" in interviews |
| 11 | Latency stack | vLLM + Llama-3.1-8B on rented L4/A10G, $60 cap | Industry-standard server; 8B fits one mid GPU; budget-safe |
| 12 | Spec-decode draft | Llama-3.2-1B (fallback: vLLM n-gram) | Same family = tokenizer compatibility; n-gram is the zero-risk fallback |
| 13 | Tracing | Langfuse self-hosted | Free, Docker-native, demoable; LangSmith fine but adds vendor |
| 14 | Python/tooling | Python 3.12, `uv`, `ruff`, `pytest` | Modern, fast, expected |

If a locked decision proves wrong in practice: write the DECISIONS.md entry (what failed, evidence, new choice), then change it. Evidence-driven reversals are good engineering; silent drift is not.

## 9. Risks & pre-agreed mitigations
- **FinanceBench↔EDGAR mapping is messier than expected** → cap effort at 1.5 days; ship with the subset that maps cleanly, document exclusions.
- **XBRL concept-mapping rabbit hole (custom tags)** → restrict tier-1 questions to standard us-gaap concepts (Revenues, GrossProfit, NetIncomeLoss, etc.).
- **Week overruns** → the cut order is fixed: React UI → transcripts (already phase-2) → latency module rungs 3–4 → never the evals.
- **GPU budget creep** → prepare all scripts locally against a tiny model first; GPU sessions are execute-and-measure only.

## 10. Tracker system (state lives in `TRACKER.md`, maintained per CLAUDE.md rules)

Statuses: `NOT_STARTED · IN_PROGRESS · BLOCKED · CODED · EXPLAINED · DONE`. **`DONE` requires `EXPLAINED` first** — code that works but wasn't walked through is not done.

- **T1 Data & Retrieval** — T1.1 EDGAR fetcher + rate limiting · T1.2 structure-aware chunker + metadata · T1.3 XBRL facts ingestion · T1.4 Postgres/pgvector schema · T1.5 lexical + dense + RRF + rerank pipeline · T1.6 50-pair retrieval eval + ablation table
- **T2 Agent & MCP** — T2.1 Pydantic schemas (Claim, Memo, tool I/O) · T2.2 LangGraph graph + typed state · T2.3 tools (retrieve, xbrl_lookup, calculator) · T2.4 verification/re-plan loop · T2.5 MCP server · T2.6 FastAPI service + auth/rate limit
- **T3 Evals & Ablation** — T3.1 tier-1 XBRL set (~70) · T3.2 tier-2 extractive set (~30) · T3.3 FinanceBench mapping + run · T3.4 judge + calibration (κ) · T3.5 citation-faithfulness checker · T3.6 CI regression gate · T3.7 three-arm ablation run + table · T3.8 injection defense + poisoned-filing test
- **T4 Ship & Tell** — T4.1 Langfuse + dashboard · T4.2 routing/caching optimization (before/after) · T4.3 Terraform + k3s + CI/CD deploy · T4.4 React UI · T4.5 write-up part 1 · T4.6 portfolio/resume update
- **T5 Latency Ladder** — T5.1 local dry-run scripts · T5.2 vLLM baseline on GPU · T5.3 FP8 rung · T5.4 prefix-caching rung · T5.5 spec-decode rung · T5.6 frontier table + write-up part 2

## 11. Working agreement
Implementation is governed by `CLAUDE.md`: explain **why and how before** code, walk through **what was written after**, tracker updated every session, decisions logged. The builder's purpose is to learn every layer well enough to defend it in interviews. **An unexplained diff is a failed deliverable, even if the code works.**