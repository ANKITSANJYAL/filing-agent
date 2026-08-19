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
