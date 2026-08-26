# DECISIONS.md

Dated log of decisions made or reversed. Locked decisions live in `PROPOSAL.md` §8;
this file records every departure from them, with evidence.

Format: **ID · date · decision · why · alternative rejected**.

---

## D-0001 · 2026-08-14 · Repo scaffolded per CLAUDE.md §5

**Decision:** `src/`-layout Python package, `uv` + Python 3.12, `ruff`, `pytest`;
`.env` gitignored in the first commit, `.env.example` committed alongside it.

**Why:** Matches locked decision §8.14. `src/` layout prevents tests from accidentally
importing a half-installed package from the working directory — a class of bug that
would otherwise surface as mysterious eval failures.

**Alternative rejected:** flat layout (fewer files, but the import-shadowing footgun is
real and this project's credibility rests on eval results being trustworthy).

---

## D-0002 · 2026-08-14 · Ablation model: Sonnet 4.6 → **Claude Sonnet 5**

**Reverses:** PROPOSAL.md §8, decision #5.

**Decision:** All three ablation arms use `claude-sonnet-5`.

**Why:**
- Current model in the Sonnet tier; Sonnet 4.6 is prior-generation.
- 1M context window, so Arm A (long-context stuffing of whole filings) still works —
  this was the binding constraint on any model swap.
- Same $3/$15 per MTok list price, with introductory pricing at $2/$10 per MTok running
  through 2026-08-31, which covers most of the build window.
- Materially stronger on agentic and coding work, which is what Arm C exercises.

**Conditions attached (builder's, accepted):**

(a) **Pin the exact model string in config, not a floating alias.**
    ⚠️ *Partially satisfiable — read this.* Anthropic publishes **no dated snapshot
    variant** for Sonnet 5: `claude-sonnet-5` is itself the complete, exact identifier,
    and appending a date suffix returns a 404. There is no `claude-sonnet-5-2026XXXX`
    to pin to. The intent of the condition is preserved a different way:
      1. The string is a single `Final` constant in `src/filing_agent/config.py` — one
         definition, read by all three arms, no per-arm override.
      2. Every eval run records the `model` field echoed back by the API response,
         plus a SHA-256 of `config.py`, into its run metadata. If Anthropic silently
         reprovisions the alias mid-experiment, the run metadata shows it.
    By contrast `claude-haiku-4-5` *does* have a dated snapshot
    (`claude-haiku-4-5-20251001`), so the router model is pinned to the dated form.

(b) **Model frozen once the ablation starts.** Changing `MODEL_ABLATION` after the
    first ablation run invalidates all three arms; every arm is re-run from scratch and
    the prior table is discarded, not patched. `tests/test_config.py` asserts the value
    so an accidental edit fails CI rather than silently corrupting a comparison.

**Alternative rejected:** stay on `claude-sonnet-4-6`. It works and is still available,
but starting a fresh project on a prior-generation model buys nothing and costs the same.

**Unchanged:** §8.6 (Haiku 4.5 for the router) and §8.7 (judge from a different model
family — deliberate, for self-preference bias).

---

## D-0003 · 2026-08-14 · Tier-1 eval set moves from W3 to immediately after T1.3

**Amends:** PROPOSAL.md §7 timeline (not a §8 locked decision — sequencing only).

**Decision:** Author T3.1 (~70 XBRL questions) right after T1.3 (XBRL ingestion),
before retrieval tuning begins.

**Why:** T3.1 depends only on XBRL facts, not on the agent existing. Writing it early
gives a numeric scoreboard *before* retrieval is tuned, and moves work out of W3, the
heaviest week. The project is titled "eval-first"; the original ordering was
retrieval-first.

**Condition attached (builder's, accepted):** questions are authored from the filings
and XBRL facts **directly, blind to what the retrieval system can currently answer**,
then frozen before any retrieval tuning. Writing questions against observed system
strengths would contaminate the scoreboard before it exists. Freeze is enforced by
committing the question set and treating edits as a DECISIONS.md-worthy event.

**Alternative rejected:** keep T3.1 in W3 as written. Defensible, but concentrates risk
in the week that already carries judge calibration, CI gating, and the ablation run.

---

## D-0004 · 2026-08-14 · Eval runs require a smoke sample before full spend

**Extends:** CLAUDE.md §4 cost guardrail.

**Decision:** Before any full eval run, state the estimated cost **and** run a
5-question smoke sample first. Only on a clean smoke result does the full run proceed.

**Why:** Prompt and parsing bugs are cheap to find on 5 questions and expensive to find
on 150. This is a spend guard, not a quality guard.

**Alternative rejected:** cost estimate alone. An estimate catches "too expensive"; it
does not catch "expensive and wrong".

---

## D-0005 · 2026-08-14 · Hand-roll the EDGAR fetcher rather than use `edgartools`

**Decision:** Write the fetcher ourselves. ~300 lines across two increments.

**Why:** Rate-limit design and cache-invalidation strategy are precisely what gets
probed when claiming "I built a pipeline against a rate-limited public API." A library
would still require learning its failure modes to debug it, so the savings are smaller
than they appear. Against CLAUDE.md §0 (defend every layer), a dependency here costs
more than it saves.

**Alternative rejected:** `edgartools`, explicitly permitted by PROPOSAL.md §4.1.
**Cost of this choice: roughly half a day.** Accepted deliberately.

---

## D-0006 · 2026-08-14 · Corpus scope refinements (8-K filter, amended filings)

**8-K filter:** narrow to **Item 2.02 (Results of Operations)** rather than the vaguer
"8-Ks with financial exhibits" in PROPOSAL.md §4.1 — a description, not a queryable
field. What it selects will be shown before committing. **If Item 2.02 8-Ks add little
beyond the 10-K/10-Q set, drop 8-Ks entirely** and log that here; corpus simplicity beats
marginal coverage in week one.

**Amended filings:** exclude `10-K/A` from v1, but **record their existence in the
manifest with a flag**. One numeric question must have exactly one answer; a documented
exclusion beats a silent ambiguity. Revisit with evidence if a tier-1 question lands on
a restated figure.

**Alternative rejected:** include amendments and let the agent reconcile. That converts
a corpus-scope decision into a per-question ambiguity, which is the wrong place to
resolve it.

---

## D-0007 · 2026-08-14 · Data assertions at every external boundary

**Extends:** CLAUDE.md §1.3.

**Decision:** *Tests verify code; assertions verify data.* Every boundary where external
data enters the project carries an explicit, hard-failing assertion about what we believe
is true of it. Not a warning — a raise.

**Why:** The fiscal-year failure mode (see below) has no exception, no red test, and no
error message. Every downstream step is self-consistent with a corrupt corpus, so the
error survives all the way into a published table and is only detectable from outside
the system. The class of bug that self-consistency hides can only be caught at the
boundary where the data arrives.

**Worked example — the motivating case.** NVIDIA's FY2025 ended January 2025; Apple's
fiscal year ends late September. Filtering filings by *filing date* while claiming
*FY2024–FY2025* silently builds the wrong corpus with roughly the right filing count.
Confirmed live: SEC's submissions API reports `fiscalYearEnd: "0131"` for NVIDIA, so the
assertion can anchor on SEC's own metadata rather than our assumptions. T1.1b will read
`period_of_report` per filing, cross-check against `DocumentFiscalYearFocus`, hard-fail
outside the target set, store `period_of_report` in the manifest for downstream
re-assertion, and print a per-ticker table for human eyeball against two anchors
(NVDA ending January, AAPL ending late September).

**Applies to, at minimum:** EDGAR filing metadata (T1.1b), XBRL concept units and periods
(T1.3), FinanceBench↔EDGAR accession mapping (T3.3), and eval-set answer keys (T3.1).

---

## D-0008 · 2026-08-19 · Pin XOM to the predecessor CIK 34088

**Decision:** `CIK_OVERRIDES = {"XOM": 34088}` in `config.py`.

**Evidence:** SEC's `company_tickers.json` maps `XOM` → CIK **2115436**
("ExxonMobil Holdings Corp"), an entity whose entire filing history is 28 documents
beginning 2026-07, including form **8-K12B** — the form filed when securities are
registered on a successor issuer in a holding-company reorganization. All FY2024–FY2025
10-Ks and 10-Qs were filed by the predecessor, CIK **34088** ("EXXON MOBIL CORP").

Ticker→CIK resolution was *correct per SEC's current map* and still produced a company
with zero filings in our window, with no error raised. Found only because the per-ticker
table showed `XOM 0 0 0`.

**Scope limit:** the override is correct **for the FY2024–FY2025 window only**. A future
FY2026 corpus needs the successor CIK, and possibly both. Re-check on any scope change.

**Alternative rejected:** auto-follow predecessors via `formerNames` / 8-K12B chains.
More general, but it silently rewrites which company you are studying — the wrong
default for a corpus whose scope is a published claim.

---

## D-0009 · 2026-08-19 · Assert corpus *completeness*, not just period correctness

**Extends:** D-0007.

**Decision:** `assert_corpus_complete()` hard-fails unless every ticker has exactly one
non-amended 10-K per fiscal year.

**Why:** D-0007 asserts that the filings we have are from the right periods. It cannot
see filings we never retrieved — **an empty result set satisfies it vacuously**. Two
real defects shipped past it on the first live run:

1. **XOM** resolved to a successor entity → 0 filings (D-0008).
2. **JPM** overflowed SEC's inline `filings.recent` window. That cap is measured in
   *documents*, not years: JPM files ~25k/year (mostly structured-note prospectuses),
   so `recent` held 25,806 rows reaching back only to 2025-08-19, and all of FY2024 sat
   in 69 unread `filings.files[]` overflow files. Fixed by `iter_all_filings()`, which
   fetches overflow files whose date range overlaps the corpus window.

Combined result: 13 of an expected 16 annual reports, reported as success.

**Generalization:** a correctness assertion over a set says nothing about the set being
complete. Every boundary needs both — *"is what I have right?"* and *"do I have all of
it?"* Carry this into T1.3 (XBRL facts per concept/period) and T3.3 (FinanceBench
mapping coverage).

---

## D-0010 · 2026-08-19 · Drop 8-Ks from the corpus entirely

**Reverses:** D-0006's Item 2.02 filter. Exercises the pre-authorized exit condition
("if it adds little beyond the 10-K/10-Q set, drop 8-Ks entirely and log it").

**Evidence from the live index** — the Item 2.02 filter worked and selected 49 8-Ks, but:

1. **Wrong date semantics.** For 10-K/10-Q, `reportDate` is the fiscal period end. For
   8-K it is the *event date*. `assert_fiscal_periods` caught this in production on
   `JPM 8-K ends 2024-01-12` — JPM's Q4-**2023** earnings release, which our rule
   labelled FY2024. Every 8-K was potentially mis-assigned by a year.
2. **Not XBRL-tagged.** EX-99 earnings exhibits are press releases, so they cannot
   participate in tier-1 numeric verification — the project's headline eval story.
3. **Non-GAAP contamination.** Their figures can *contradict* the XBRL facts we grade
   against, which is worse than absent coverage.

**Corpus effect:** 99 → **64 filings** (8 tickers × 2 10-K × 6 10-Q). This is below
PROPOSAL.md §4.1's "≈90–110 filings" estimate, which assumed 8-Ks were included. §5 caps
the corpus from above, not below; 64 GAAP, XBRL-backed filings is the right shape for an
eval-first project.

**Alternative rejected:** keep 8-Ks with a form-specific fiscal-year rule. Buys back
non-GAAP press releases that still cannot be XBRL-verified — cost without the benefit.

**Reversible:** re-add `8-K` to `PERIODIC_FORMS` and give 8-Ks their own period rule.
Revisit only if a tier-2 or FinanceBench question demonstrably needs earnings-release
text.

---

## D-0011 · 2026-08-19 · Commit the manifest; gitignore the filings

**Decision:** `data/raw/manifest.jsonl` (27 KB) is committed. The 64 HTML filings
(230.8 MB) are not.

**Why:** The manifest is the corpus *definition* — ticker, accession, period, and a
SHA-256 per filing. Committing it means a published number can be traced to exactly
which documents produced it, and a clone can verify byte-identity after re-fetching.
Committing 230 MB of regenerable HTML buys nothing and bloats every clone.

**Implementation gotcha worth remembering:** the first attempt used
`data/raw/` + `!data/raw/manifest.jsonl` and silently failed. Git does not descend into
an excluded *directory*, so a negation inside one is never evaluated. Excluding the
contents (`data/raw/*`) keeps the directory walkable and lets the negation apply.

**Pairs with:** D-0002 (config hash) — corpus fingerprint + config hash together pin
which filings and which model produced any result.

**Fingerprint at time of writing:** `4c6b9df256978e8c37d1317dd8b02ef2b38d44b55d694582d32b021d69806ab4`

---

## D-0012 · 2026-08-19 · Known gap: submissions JSON is not cached

**Status:** accepted for now, not fixed.

`EdgarClient.download()` is cache-first, but `get_json()` is not — so re-running the
pipeline costs **36 requests to re-index** even though 0 filings are re-downloaded.
Harmless at this scale and well inside rate limits.

**Not fixed now because** submissions JSON is the one input that legitimately changes
(new filings appear), so caching it needs a TTL or explicit invalidation, and that is a
real design decision rather than a one-liner. Revisit if indexing cost becomes annoying
or if CI starts re-indexing on every run.

---

## D-0013 · 2026-08-21 · Extract with stdlib `html.parser`, not lxml/BeautifulSoup

**Decision:** HTML extraction uses Python's stdlib `html.parser`. No new dependency.

**Why:** Measured, not assumed — 64 filings (231 MB) extract in **7.4s**. lxml would be
faster, but nothing here is latency-bound: extraction runs once, offline. Zero
dependencies also keeps the module trivially portable into CI and the k3s image, and is
consistent with D-0005 (hand-roll what you must defend).

**Secondary reason:** `uv` was still uninstalled (E2), so lxml could not be added without
touching system Python. That forced the measurement, which then justified the choice on
its merits.

**Alternative rejected:** lxml. Revisit only if extraction becomes a bottleneck — it is
7 seconds for the entire corpus.

---

## D-0014 · 2026-08-21 · Drop iXBRL machine subtrees before extraction

**Decision:** `<ix:header>`, `<ix:hidden>`, `<script>`, `<style>` subtrees are dropped
wholesale. `assert_no_xbrl_metadata` hard-fails if any XBRL namespace prefix
(`us-gaap:`, `xbrli:`, `dei:`, `ix:`, `iso4217:`, `srt:`, `utr:`) survives.

**Evidence (NVDA FY2025 10-K, measured):** naive tag-stripping yielded 381,767 chars, of
which the `<ix:header>` block was **125,940 — 33% of all extracted "text"**. It produced
the two densest numeric chunks in the document: entity IDs, axis members, and context
dates that no human reader ever sees. Embedded, those chunks would be pure retrieval
noise competing with real content.

**Corpus-wide result:** 231 MB markup → 17.0 MB text (**92.4% of the file was markup**).
All 64 filings pass both extraction assertions.

**Calibration note — read before trusting the plausibility floor.** `min_ratio` is 2%.
Observed text ratios across the corpus run from **2.7% (MSFT 10-Q)** to **17.2% (NVDA
10-K)**. The floor therefore sits only ~26% below the observed minimum — thin margin. It
is a bail-out detector, not a quality gate; a future filing tripping it should be
inspected by hand rather than assumed broken.

---

## D-0015 · 2026-08-21 · Preserve table rows as single lines; tidy spacer cells

**Decision:** Table rows are emitted one per line with ` | ` between cells. Empty
layout-spacer cells are dropped and orphaned `$`/`(` symbols reattached to their figure.

**Why:** Fixed-size chunking severed rows mid-cell — chunk 101 ended
`...Deferred Restricted Stock`, chunk 102 opened `Unit Agreement (2016) 10-K 10.26`.
A figure separated from its label is unusable for numeric verification, which is the
project's headline claim.

Untidied, a three-year row read `Gross profit | 97,858 | | | 44,301 | | | 15,356`.
Tidied: `Revenue | $130,497 | $60,922 | Up 114%` — a single line sufficient to answer a
growth question. Genuine nulls are em dashes, not blanks, so dropping empty cells loses
no data (test asserts this).

**Still open for T1.2b:** row-level integrity does not survive *chunk* boundaries yet.
The chunker must not split inside a table.

---

## D-0016 · 2026-08-21 · Item-anchored sections + allowlist; content-anchoring rejected

**Decision:** Sections are located by Item headings, choosing per item the occurrence
that owns the most content. Required sections that are cross-reference stubs are
allowlisted per ticker in `config.STUB_SECTION_ALLOWLIST`; anything unlisted hard-fails.

**Result:** 64/64 filings pass both assertions (ordering + substance), 7.3s.

### The finding that forced this

PROPOSAL.md §4.1 says "chunk by document structure — Item 1A, Item 7, Item 7A, Item 8",
which assumes an Item heading owns its content. Measured across all 16 10-Ks, **48 of 64
section-slots are substantive**; the 16 exceptions are four structural conventions:

| Ticker | Stub sections | Cause |
|---|---|---|
| JPM | MD&A, 7A, Item 8 | incorporated by reference to page ranges |
| XOM | MD&A, 7A, Item 8 | same |
| NVDA | Item 8 | statements filed under Part IV, Item 15 |
| PFE | 7A | genuinely brief, cross-references the MD&A |

Nothing errors in the naive case: JPM's MD&A chunk would simply be *"Refer to pages
133-142."* Tier-1 XBRL evals would still pass (XBRL is independent of sectioning), so the
scoreboard would look healthy while tier-2 and FinanceBench failed on three tickers —
and the likely misdiagnosis is "weak retrieval", costing a week of chunker tuning.

### Why content-anchoring was attempted and rejected

Two resolvers were built and both failed to converge:

1. **Independent max-span per section** — rescued JPM (MD&A 2 → 3,673 lines) but broke
   COST/WMT (MD&A 140 → 3 lines): nothing forced anchors into document order, so a later
   section's heading could be selected before an earlier one.
2. **Order-constrained DP** maximising substantive-section count — fixed NVDA and
   AAPL/MSFT, still left COST/WMT/PFE MD&A at 2-3 lines and cut JPM FY2025 MD&A to 52.

Root cause: these documents repeat section names in the table of contents, in running
page headers (JPM prints "Management's discussion and analysis" **41 times**), and in
cross-references. A short-line heuristic cannot separate them. Each iteration traded one
filing's correctness for another's — a sign of overfitting to 16 documents by eye.

**Deferred, not abandoned.** If T1.6's retrieval eval shows JPM/XOM MD&A questions
failing, we will have a measured reason to invest and a scoreboard to verify against.

**Cost accepted:** JPM and XOM MD&A are not section-filterable in v1. Both tickers still
contribute Risk Factors (JPM: 668-712 lines) and financial statements via other sections;
all tier-1 XBRL questions are unaffected.

### Two of my own bugs this surfaced

- **A 160-char heading cap silently dropped COST's real MD&A heading**, which runs 171
  chars because of a parenthetical about units. The cap is now 400 — prose almost never
  *begins* with "Item N", so the leading anchor does the discriminating, not the length.
- **The separator class omitted em/en dashes.** COST writes `Item 7—Management's
  Discussion`. Both are regression-tested.

**Design note worth keeping:** a section ends at the next titled Item heading of any
different item, *not* at the next tracked section — otherwise RISK_FACTORS swallows
Items 1B through 6.

---

## D-0017 · 2026-08-21 · Chunk the whole document, not just detected sections

**Decision:** Every line is chunked. Lines outside a detected section are tagged
`UNSECTIONED` rather than dropped.

**Why:** D-0016 leaves three tickers with stub sections — NVDA's financial statements
sit outside Item 8, JPM's and XOM's MD&A outside Item 7. Chunking only inside detected
sections would silently discard that content, so a question about NVDA's revenue would
have nothing to retrieve.

**Verified:** NVDA FY2025's revenue figure (130,497) appears in 6 chunks, one of them
`UNSECTIONED`. That filing has 1 `FINANCIAL_STATEMENTS` chunk (the allowlisted stub) and
81 `UNSECTIONED` chunks carrying the actual statements. The content is retrievable by
lexical and dense search; only the section *filter* is unavailable for it.

**Corpus-wide:** 9,449 chunks, 46.5% `UNSECTIONED`. That is expected — a 10-K also
contains Items 2-6, 9-16, exhibits, and signatures, none of which are tracked sections.

**Alternative rejected:** chunk only within sections. Smaller, tidier index that quietly
loses the financial statements of one of the eight tickers.

---

## D-0018 · 2026-08-21 · Size chunks in characters; calibrate against count_tokens later

**Decision:** Chunks target 512 tokens approximated at **4 chars/token**, with a 768-token
ceiling that a table run may use to stay whole.

**Why not `tiktoken`:** it is OpenAI's tokenizer and mis-sizes Claude inputs; the correct
tool is Anthropic's `count_tokens` endpoint. No API key is configured yet (E2/.env), so
sizing by characters is honest arithmetic rather than a wrong tokenizer dressed up as a
right one.

**Calibration owed:** once a key exists, run `count_tokens` over a sample of real chunks
and correct `CHARS_PER_TOKEN`. Recorded here so it is not forgotten.

**Measured:** median 477 estimated tokens, mean 447, p95 546. Max 1,171 — every chunk
above the ceiling is a *single* table row (largest 4,684 chars). The assertion permits
oversize single-line chunks deliberately: splitting a row separates a figure from its
label, which is the failure the whole extraction design exists to prevent.

**Known minor waste:** 16 of 9,449 chunks (0.2%) are under 40 chars — section-heading
fragments stranded at span boundaries. Not worth merging logic yet; revisit if retrieval
eval shows them polluting results.

---

## D-0019 · 2026-08-21 · Concept set measured in-window; no universal revenue tag

**Decision:** Tier-1 concepts are the 17 us-gaap concepts reported by all eight tickers
**within our 64 filings**, plus a per-ticker revenue concept.

**The measurement error worth remembering:** my first pass measured concept availability
across each company's *entire* EDGAR history and concluded `Revenues` was universal
(8/8). Measured within our corpus window it is not — AAPL and MSFT reported `Revenues`
years ago but tag revenue differently in FY2024-25. Availability over all history is a
different question from availability in the corpus, and only the second one matters.

**PROPOSAL.md §9 is partly wrong for this corpus.** It names GrossProfit as a safe
standard concept; only 4 of 8 tickers report it, because a bank has no gross profit and
Exxon does not present one. This is the §9 concept-mapping risk, now quantified rather
than anticipated.

**No revenue concept is common to all eight** — the single most obvious metric in a
financial corpus requires an explicit per-ticker map:

| Concept | Tickers |
|---|---|
| `Revenues` | NVDA, XOM, PFE, WMT, COST |
| `RevenueFromContractWithCustomerExcludingAssessedTax` | AAPL, MSFT |
| `RevenuesNetOfInterestExpense` | JPM |

JPM's tag is the economically correct concept for a bank, not a workaround: revenue net
of interest expense is how a bank's top line is defined.

**Direct consequence of the locked cross-sector corpus (§4.1).** Sector diversity buys
generalisation and costs concept uniformity. Both belong in the write-up.

---

## D-0020 · 2026-08-21 · Restatements resolved to the most recently filed value

**Decision:** `resolve_restatements()` collapses each (ticker, concept, unit, period) to
one value, keeping the most recently filed, and marks it `restated=True`.
`assert_no_conflicting_values` hard-fails on unresolved conflicts, with known corporate
actions allowlisted in `config.XBRL_RESTATEMENT_ALLOWLIST`.

**What forced it:** NVIDIA's **10-for-1 stock split (June 2024)**. FY2023 diluted EPS is
`11.93` in the original 10-K and `1.19` as a later comparative; diluted share count is
2,507,000,000 vs 25,070,000,000 — exactly 10x. Both are correct as filed.

**Why this is load-bearing:** D-0006 requires that one numeric question have exactly one
answer. Without resolution, *"what was NVDA's FY2023 diluted EPS?"* has two defensible
answers depending on which filing the retriever happened to surface — and the eval would
score the agent wrong for being right. That failure would be near-impossible to diagnose
from the eval output alone.

**Why most-recently-filed:** a split is a real economic event and the adjusted figure is
the current basis — the answer an analyst gives today. The alternative (prefer the
original filing) is equally defensible in principle but would make every per-share
question in the corpus answer on a stale basis.

**Measured:** 2,971 raw facts → 2,010 resolved; 9 restated, all NVDA per-share/share-count.

---

## D-0021 · 2026-08-21 · XBRL facts restricted to accessions in the corpus manifest

**Decision:** `load_facts` keeps a fact only if its `accn` is a filing in
`manifest.jsonl`.

**Why:** it makes traceability structural rather than aspirational — every verified
number points at a document we hold and can cite, so a tier-1 verification can always
produce a citation. It also removes a whole class of ambiguity for free, by excluding
restatements that arrive in filings outside the corpus window.

**Measured:** 2,010 facts across 8 tickers (238-286 each), all passing
`assert_facts_traceable`.

---

## D-0022 · 2026-08-22 · Tier-1 eval set generated from facts, frozen at 72 questions

**Decision:** Questions are generated *from* the resolved XBRL fact set, not authored by
hand. `evals/tier1_pool.jsonl` holds all 564; `evals/tier1.jsonl` holds a frozen,
committed 72-question CI set. Both are committed.

**Why generation satisfies D-0003's blindness condition better than hand-authoring:**
the answer key is XBRL by construction. There is no step at which a human could see what
the retrieval system answers well and drift the questions toward it.

**Frozen set shape:** 9 per ticker, 24 per question type (point / yoy_change / yoy_pct),
weighted by `CONCEPT_PRIORITY` toward revenue, net income, diluted EPS and total assets.
Selection is a stable sort with no RNG, so regeneration is byte-identical on any machine
— which is what makes "frozen and committed" meaningful.

**Restated facts generate no questions.** An agent reading NVDA's FY2024 10-K correctly
reports diluted EPS of 11.93 while the resolved key says 1.19 (D-0020). Grading that
wrong would penalise a correct answer, so those 9 facts are excluded.

---

## D-0023 · 2026-08-22 · Exact grading, but derived values computed in Decimal

**Decision:** Point and change questions grade on **exact equality**. Percentage
questions are rounded to 2dp and graded with an absolute tolerance of 0.005 — any answer
that rounds to the same 2dp is correct.

**Two bugs found and fixed here, in sequence:**

1. **Too loose.** The initial 1e-6 *relative* tolerance allowed **$72,880 of error** on a
   $72.88B figure, while PROPOSAL.md §4.4.1 promises "exact-match vs EDGAR facts". Tests
   caught it: an off-by-one-dollar answer graded correct.
2. **Then too strict.** Tightening to exact equality broke 30 of 72 keys, because
   subtracting decimal amounts in binary float injects artifacts that are not in the
   data — `7.09 - 5.71` yields `1.3800000000000008`, so the key rejected the correct
   answer of `1.38`. Fixed by computing derived values with `decimal.Decimal`.

**After the fix:** 0 keys carry float dust, every question grades its own key, and none
accepts an off-by-one answer. EPS change keys read `1.38`, `1.65`, `0.27`, `-0.05`.

**Worth remembering:** "exact match" is a claim about the *comparison*, but it only holds
if the *arithmetic producing the key* is exact too. The first fix addressed the
comparison and silently broke the arithmetic.

---

## D-0024 · 2026-08-24 · One Postgres for chunks, facts, and eval results

**Decision:** Single Postgres 17 + pgvector instance (`infra/docker/docker-compose.yml`),
holding `filings`, `chunks`, `xbrl_facts`, and `eval_questions`. Implements §8.1.

**Two invariants moved from Python into the schema**, so they hold regardless of which
code path writes:

- `chunks.accession_no` is a foreign key to `filings` — a chunk cannot cite a filing
  outside the corpus, so a citation can never dangle.
- `xbrl_facts` is UNIQUE on the economic fact — D-0020's "exactly one answer" rule
  enforced by the database rather than by remembering to call an assertion.

**Loaded:** 64 filings, 9,449 chunks, 2,010 facts, 72 eval questions. Lexical search over
the generated `tsvector` returns sensible hits (NVDA MD&A chunks top the ranking for
"data center revenue growth").

**Also extracted `ingest/pipeline.py`.** The extract → section → chunk → facts sequence
had been rebuilt inline in six separate verification runs; that is precisely how two
callers drift apart about what the corpus contains. It now has one definition.

---

## D-0025 · 2026-08-24 · `UNIQUE NULLS NOT DISTINCT` — the constraint that looked right

**Decision:** The economic-fact constraint uses `UNIQUE NULLS NOT DISTINCT`
(Postgres 15+), with a migration for databases created before the fix.

**The bug:** plain `UNIQUE` treats `NULL`s as **distinct**. Instant facts — every
balance-sheet item, which has no `period_start` — therefore satisfied the constraint
trivially no matter how many conflicting values were inserted. **439 of 2,010 rows were
unprotected** while the constraint definition read as if it covered them.

**How it was found, and why that matters more than the fix.** My manual verification
inserted a duplicate and saw `UniqueViolation` — apparently confirming the constraint.
It happened to pick `NetIncomeLoss`, a *duration* fact. The integration test picked a row
with `LIMIT 1` and drew an instant fact instead, and failed. A hand-picked example
confirmed the behaviour I expected; an arbitrary one found the hole.

The test now deliberately selects `WHERE period_start IS NULL`, so the previously
unprotected case is the one under test.

**Generalises:** the same NULL semantics apply to any uniqueness rule over optional
columns. `ON CONFLICT` inherits it too — the old upsert clause silently never matched
for instant facts, so re-running the loader could have duplicated them.

---

## D-0026 · 2026-08-24 · BGE-M3 embeddings in fp16; Postgres FTS as the lexical arm

**Decision:** BGE-M3 (local, 1024-dim, L2-normalised) via sentence-transformers, loaded
in **fp16** on GPU backends. Lexical retrieval is Postgres full-text search.

**fp16 is measured, not assumed.** Apple MPS, batch 64, this corpus:
fp32 **8.2 chunks/s**, fp16 **9.9 chunks/s** — 21% faster. Retrieval ranks by cosine
similarity and fp16's reduced mantissa sits far below the margin separating hits;
vectors are stored back as float32 either way.

**A measurement mistake worth recording.** The first throughput reading was
**2.1 chunks/s**, implying a 60-minute run. That figure was an artifact: a test I ran
concurrently loaded a *second* BGE-M3 onto the same MPS device, and the two jobs
contended. Measured without contention the same code does 8.2 — 4x higher. Benchmarks
taken while other GPU work is running measure the contention, not the code.

**`max_seq_length` deliberately left at default.** The obvious optimisation looked like
capping BGE-M3's 8192-token window, since our chunks are ~500 tokens (p50 433, p99 914,
max 1179; 99.5% under 1024). But sentence-transformers pads each batch to its own
longest item, not to the model ceiling — so lowering it would only truncate the tail
without buying speed.

**Lexical arm: Postgres FTS rather than a dedicated BM25 engine** (§8.4). `ts_rank_cd`
is not BM25 — no tunable k1/b, and term saturation is handled differently. The tradeoff
is one database instead of two, and the retrieval ablation (T1.6) will show what it
costs against dense and hybrid on the same 50 labelled pairs.

---

## D-0027 · 2026-08-24 · RRF fuses ranks, not scores

**Decision:** Hybrid retrieval fuses lexical and dense with reciprocal rank fusion,
k=60, over a candidate pool 5x the requested top-k.

**Why rank-based:** `ts_rank_cd` and cosine similarity are on incomparable scales. Score
fusion would require normalising them, which introduces a weighting parameter that has
to be justified and tuned — and tuned on what, before the eval set exists? Ranks need no
calibration, so the hybrid arm has no free parameter that could be quietly fitted to the
questions it is later scored on.

**k=60** is the value from the original Cormack et al. paper. It damps the influence of
very high ranks, so a chunk ranked 1st by one retriever cannot alone outrank a chunk
ranked well by both — which is the property the ablation is testing for.

**Candidate depth 5x top-k:** fusion must see more candidates than it returns, or
agreement between the retrievers below the cut is invisible.

**Uniform score direction.** Dense search returns `1 - cosine_distance`, so higher is
better in *every* retriever. Mixing conventions is how a fusion step silently inverts
one of its inputs while still producing plausible-looking output.

---

## D-0028 · 2026-08-25 · The lexical arm scored 0.000 because of a query-operator bug

**Decision:** Lexical retrieval builds an **OR** query — the question is lexemised and
joined with `|` — instead of `plainto_tsquery`, which ANDs every term.

**What happened.** The first four-arm run produced:

```
lexical (FTS)              0.000     0.000      50
dense (BGE-M3)             0.220     0.133      50
hybrid (RRF)               0.220     0.130      50
hybrid + rerank            0.360     0.227      50
```

An arm scoring **exactly** zero on all 50 cases is not a finding, it is a bug. Diagnosis:
`plainto_tsquery('english', "What are the principal risks AAPL disclosed for fiscal
2024?")` yields `'princip' & 'risk' & 'aapl' & 'disclos' & 'fiscal' & '2024'` — every
stem required. Filings write "Apple Inc.", not the ticker: 10 chunks contain "AAPL"
against 292 containing "Apple". The conjunction matched nothing, in every case.

**Why this was nearly a published falsehood.** The write-up's headline is a retrieval
ablation. "Postgres full-text search achieves 0.000 recall@5" would have been stated as
a measured result about lexical retrieval, when it was a statement about my choice of
query operator. It is also exactly the kind of number an interviewer probes.

**The fix is not a tweak.** BM25 ranks any document containing *any* query term, weighted
by term rarity; conjunctive matching is a different retrieval model, not a weaker one.
D-0026 noted `ts_rank_cd` is not BM25 and named the *ranking function* as the compromise
— the larger discrepancy was in the query operator, and I had not looked.

**What caught it:** the number itself. 0.220 for dense and 0.000 for lexical is not a
plausible spread between two reasonable retrievers over the same 50 questions. A
suspicious result is evidence about the harness before it is evidence about the system.

**Generalises:** an arm that scores exactly zero, exactly one, or exactly equal to
another arm should be treated as a defect report until proven otherwise.

---

## D-0029 · 2026-08-25 · Eval queries use company names, not ticker symbols

**Decision:** Retrieval-eval queries name the company as its filings do ("Apple",
"JPMorgan Chase"), not by ticker. Labels are unchanged — only the query surface form.

**Measured on identical labels, before any retrieval tuning:**

| Query form | lexical recall@5 | dense recall@5 |
|---|---|---|
| `AAPL` | 0.100 | 0.220 |
| `Apple` | 0.140 | **0.380** |

Dense recall nearly doubles. Filings never use ticker symbols in body text — 10 chunks
contain "AAPL" against 292 containing "Apple" — so a ticker-phrased query was testing
whether a rare string happened to appear, not whether the retriever found the right
section. Analysts say "Apple" anyway, so the name form is also the more realistic query.

**Why changing a "frozen" set is legitimate here.** D-0003's freeze exists to stop
questions drifting toward what the system answers well. This changes the *query surface
form* to remove a measurement artifact, keeps every label identical, and was done
**before any retrieval tuning** — which is the only point at which such a change is
defensible. Doing it after seeing arm-level results would have been tuning to the test.

---

## D-0030 · 2026-08-25 · Correction: the cross-encoder is not slow

**Retracted claim.** The first four-arm run reported the rerank arm taking **21,322s
(5.9 hours)**, which I described as "~300x too slow".

**That was wrong.** Measured directly, `bge-reranker-v2-m3` on MPS runs at **0.16 s/pair**
— 1,250 pairs is roughly 200 seconds. The 21,322s figure was wall-clock on a backgrounded
process that spanned a system sleep; it measured elapsed time, not compute.

**Second instance of the same mistake this session.** D-0026 records an embedding
throughput reading of 2.1 chunks/s that was really 8.2 — that one was GPU contention from
a concurrently running test. Both readings came from a timer that was running while the
process was not.

**Rule going forward:** a wall-clock duration from a background or long-running process is
not a performance measurement. Time the operation directly, in the foreground, with
nothing else contending, before drawing any conclusion about speed.

---

## D-0031 · 2026-08-25 · Chunk sizing recalibrated: 2.71 chars/token, not 4

**Decision:** `CHARS_PER_TOKEN = 2.71`, measured against Anthropic's `count_tokens`
endpoint on 25 real chunks (median 2.71, mean 2.73, range 1.86–3.89). Settles the
calibration owed in D-0018.

**The error it corrects.** The placeholder was the usual ~4 chars/token heuristic. That
is a prose figure; SEC filings are dense with digits, currency symbols and table pipes
(`| $130,497 | $60,922 |`), which cost roughly one token per character. Every chunk was
therefore **~48% larger than intended** — a "512-token" chunk was really ~757 tokens.

**Why it mattered enough to rebuild.** PROPOSAL.md §4.4 defines Arm B as *"fixed
512-token chunks"*. Publishing an ablation whose naive-RAG arm used 757-token chunks
would have described the corpus incorrectly in the write-up's headline experiment, and
chunk size is a first thing an interviewer would ask about.

**After the fix:** 9,449 → 14,112 chunks. Verified against the real tokenizer: median
**461 tokens** against a 512 target (previously ~757), with estimates now tracking
reality within 1%. The residual −10% is chunks closing at line boundaries before
reaching the cap, which is the intended behaviour.

**Cost:** re-chunk, reload, re-embed (~26 min) and re-run the T1.6 ablation. Taken
because the numbers are the deliverable.

**Generalises:** a token-count heuristic is domain-specific. Any corpus that is not
mostly prose — tables, code, JSON, non-English — needs its own measurement, and the
measurement is free via `count_tokens`.

---

## D-0032 · 2026-08-26 · `xbrl_lookup` must anchor to the 10-K's period, not the calendar year

**Bug:** the lookup matched facts with `EXTRACT(YEAR FROM period_end) = fiscal_year` and
took the most recent. NVDA has 10-Q facts throughout calendar 2025, so "FY2025 net
income" returned **$77.1bn for the period ending 2025-10-26** — a *quarterly* figure —
instead of the annual **$72.88bn** ending 2025-01-26.

**Consequence had it shipped:** every annual claim would have been verified against a
quarterly fact and marked wrong. The project's headline is "every numeric claim is
auto-verified against XBRL"; a verifier checking the wrong period is worse than none,
because it produces confident false negatives.

**Fix:** join to `filings` and require `f.period_end = fl.fiscal_period` for the 10-K of
that fiscal year, plus a 350–380 day duration band for duration facts. Instant facts
(balance-sheet items) match the fiscal-year-end date directly.

**Found by:** a unit test that fetched "an NVDA FY2025 net income fact" the same wrong
way and got a value that did not match what the tool returned. Third time this session a
period/completeness assumption failed silently (D-0007, D-0020, this).

---

## D-0033 · 2026-08-26 · Verify against the claim's period, not the chunk's filing year

**Bug:** `verify_claim` used `hit.fiscal_year` — the fiscal year of the *filing the quote
came from*. Every 10-K prints prior-year comparatives, so a correct FY2024 figure quoted
from the FY2025 filing was checked against FY2025 data.

**Caught by the first smoke run**, not by a test:

```
[xbrl/FAIL] AAPL diluted EPS for fiscal year 2024 was $6.08
            cite 0000320193-25-000079:0124   (the FY2025 filing)
```

$6.08 is correct for FY2024. Verification compared it to FY2025's $7.46 and failed it.

**Fix:** `claim_fiscal_year()` reads the year from the claim's own `period` field, falling
back to the chunk's year only when absent, and ignores years outside the corpus.

**A second bug inside the fix.** The first regex was `\b(20\d{2})\b`, which does not match
`FY2024` — there is no word boundary between `Y` and `2`, both being word characters. The
most common phrasing silently failed. Now `(?<!\d)(20\d{2})(?!\d)`, with all four
phrasings regression-tested.

**Smoke result after both fixes:** 5/5 questions answered with traceable citations;
derived quantities (a year-over-year *change*) correctly remain `unverified` with
disclosure, since a computed delta is not itself an XBRL fact.
