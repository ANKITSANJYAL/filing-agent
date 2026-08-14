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
