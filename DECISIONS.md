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
