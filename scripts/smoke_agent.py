"""5-question smoke run before any full eval spend (DECISIONS.md D-0004)."""
import time

import anthropic

from filing_agent.agent.graph import answer
from filing_agent.evals.tier1 import read_tier1
from filing_agent.retrieval import db
from filing_agent.retrieval.embed import load_encoder

N = 5
questions = read_tier1()[:N]
conn = db.connect()
client = anthropic.Anthropic()
encoder = load_encoder()

t0 = time.time()
for q in questions:
    try:
        memo = answer(q.question, conn, client, encoder)
    except Exception as exc:  # noqa: BLE001 - smoke run must report, not abort
        print(f"\n[{q.question_id}] ERROR: {type(exc).__name__}: {exc}", flush=True)
        continue
    graded = [c for c in memo.numeric_claims]
    print(f"\n[{q.question_id}] {q.question}")
    print(f"  expected : {q.expected_value:,.4f} {q.unit}")
    print(f"  summary  : {memo.answer_summary[:150]}")
    print(f"  claims   : {len(memo.claims)} ({len(graded)} numeric, "
          f"verified_fraction={memo.verified_fraction:.2f})")
    for c in memo.claims[:2]:
        v = c.verification
        print(f"     - [{v.method}/{'ok' if v.verified else 'FAIL'}] {c.text[:80]}")
        print(f"       cite {c.citation.chunk_id} §{c.citation.item_section}")
    if memo.confidence_notes:
        print(f"  notes    : {memo.confidence_notes[:110]}")
print(f"\n{N} questions in {time.time()-t0:.0f}s")
conn.close()
