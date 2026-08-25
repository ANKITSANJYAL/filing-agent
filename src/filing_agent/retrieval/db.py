"""Postgres + pgvector schema and loaders — one database for the whole system.

Chunks, filing metadata, XBRL facts and (later) eval results live together
(PROPOSAL.md §8.1). One system to operate, and joins between text and ground truth are
free rather than an application-level merge.

Two invariants are enforced by the schema rather than by Python:

* `xbrl_facts` is UNIQUE on (ticker, concept, unit, period_start, period_end) — the
  database will not store two values for one economic fact, which is D-0020's
  "exactly one answer" rule made structural.
* `chunks.accession_no` is a foreign key to `filings`, so a chunk can never reference
  a filing that is not in the corpus. Citations cannot dangle.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Sequence
from typing import Any, Final

import psycopg
from pgvector.psycopg import register_vector

from ..evals.tier1 import EvalQuestion
from ..ingest.chunker import Chunk
from ..ingest.corpus import ManifestRow
from ..ingest.xbrl import XbrlFact

DEFAULT_DSN: Final[str] = "postgresql://filing:filing@localhost:5432/filing_agent"

# BGE-M3 dense output dimension (PROPOSAL.md §8.2).
EMBEDDING_DIM: Final[int] = 1024

SCHEMA: Final[str] = f"""
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS filings (
    accession_no   TEXT PRIMARY KEY,
    ticker         TEXT    NOT NULL,
    cik            INTEGER NOT NULL,
    form_type      TEXT    NOT NULL,
    fiscal_year    INTEGER NOT NULL,
    fiscal_period  DATE    NOT NULL,
    filing_date    DATE    NOT NULL,
    is_amendment   BOOLEAN NOT NULL DEFAULT FALSE,
    sha256         TEXT,
    local_path     TEXT
);

CREATE TABLE IF NOT EXISTS chunks (
    chunk_id       TEXT PRIMARY KEY,
    accession_no   TEXT    NOT NULL REFERENCES filings(accession_no) ON DELETE CASCADE,
    ticker         TEXT    NOT NULL,
    form_type      TEXT    NOT NULL,
    fiscal_year    INTEGER NOT NULL,
    fiscal_period  DATE    NOT NULL,
    item_section   TEXT    NOT NULL,
    start_line     INTEGER NOT NULL,
    end_line       INTEGER NOT NULL,
    text           TEXT    NOT NULL,
    tsv            tsvector GENERATED ALWAYS AS (to_tsvector('english', text)) STORED,
    embedding      vector({EMBEDDING_DIM})
);

-- Lexical arm (PROPOSAL.md §8.4): Postgres FTS rather than a dedicated BM25 engine.
CREATE INDEX IF NOT EXISTS chunks_tsv_idx  ON chunks USING GIN (tsv);
-- Metadata filters for Arm C: ticker / period / section.
CREATE INDEX IF NOT EXISTS chunks_meta_idx ON chunks (ticker, fiscal_year, item_section);

CREATE TABLE IF NOT EXISTS xbrl_facts (
    id             BIGSERIAL PRIMARY KEY,
    ticker         TEXT   NOT NULL,
    cik            INTEGER NOT NULL,
    concept        TEXT   NOT NULL,
    unit           TEXT   NOT NULL,
    value          DOUBLE PRECISION NOT NULL,
    period_start   DATE,
    period_end     DATE   NOT NULL,
    accession_no   TEXT   NOT NULL REFERENCES filings(accession_no),
    form           TEXT,
    filed_date     DATE   NOT NULL,
    restated       BOOLEAN NOT NULL DEFAULT FALSE,
    -- D-0020 as a database constraint: one economic fact, one value.
    --
    -- NULLS NOT DISTINCT is load-bearing. Plain UNIQUE treats NULLs as distinct, so
    -- instant facts (balance-sheet items, which have no period_start) were silently
    -- exempt — 439 of 2,010 rows. The constraint looked right and protected only
    -- duration facts.
    CONSTRAINT xbrl_facts_economic_fact_key
        UNIQUE NULLS NOT DISTINCT (ticker, concept, unit, period_start, period_end)
);
CREATE INDEX IF NOT EXISTS facts_lookup_idx ON xbrl_facts (ticker, concept, period_end);

-- Migration for databases created before the NULLS NOT DISTINCT fix.
ALTER TABLE xbrl_facts
    DROP CONSTRAINT IF EXISTS xbrl_facts_ticker_concept_unit_period_start_period_end_key;
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'xbrl_facts_economic_fact_key'
    ) THEN
        ALTER TABLE xbrl_facts ADD CONSTRAINT xbrl_facts_economic_fact_key
            UNIQUE NULLS NOT DISTINCT (ticker, concept, unit, period_start, period_end);
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS eval_questions (
    question_id       TEXT PRIMARY KEY,
    question          TEXT   NOT NULL,
    question_type     TEXT   NOT NULL,
    ticker            TEXT   NOT NULL,
    concept           TEXT   NOT NULL,
    expected_value    DOUBLE PRECISION,
    expected_text     TEXT,
    unit              TEXT   NOT NULL,
    rel_tolerance     DOUBLE PRECISION NOT NULL,
    abs_tolerance     DOUBLE PRECISION NOT NULL,
    source_accessions TEXT[] NOT NULL
);
"""


class DbError(RuntimeError):
    """Database state failed an expectation (D-0007)."""


def connect(dsn: str | None = None) -> psycopg.Connection:
    """Connect and make the `vector` type usable.

    The extension has to exist before pgvector can register its type adapter, so this
    creates it first — otherwise the first connection to a fresh database fails before
    `init_schema` ever gets a chance to run.
    """
    conn = psycopg.connect(dsn or os.environ.get("DATABASE_URL", DEFAULT_DSN))
    with conn.cursor() as cur:
        cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
    conn.commit()
    register_vector(conn)
    return conn


def init_schema(conn: psycopg.Connection) -> None:
    with conn.cursor() as cur:
        cur.execute(SCHEMA)
    conn.commit()


def _executemany(conn: psycopg.Connection, sql: str, rows: Sequence[tuple[Any, ...]]) -> int:
    if not rows:
        return 0
    with conn.cursor() as cur:
        cur.executemany(sql, rows)
    conn.commit()
    return len(rows)


def load_filings(conn: psycopg.Connection, manifest: Iterable[ManifestRow]) -> int:
    rows = [
        (m.accession_no, m.ticker, m.cik, m.form, m.fiscal_year, m.report_date,
         m.filing_date, m.is_amendment, m.sha256 or None, m.local_path or None)
        for m in manifest
    ]
    return _executemany(conn, """
        INSERT INTO filings (accession_no, ticker, cik, form_type, fiscal_year,
                             fiscal_period, filing_date, is_amendment, sha256, local_path)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT (accession_no) DO NOTHING
    """, rows)


def load_chunks(conn: psycopg.Connection, chunks: Iterable[Chunk]) -> int:
    rows = [
        (c.chunk_id, c.accession_no, c.ticker, c.form_type, c.fiscal_year,
         c.fiscal_period, c.item_section, c.start_line, c.end_line, c.text)
        for c in chunks
    ]
    return _executemany(conn, """
        INSERT INTO chunks (chunk_id, accession_no, ticker, form_type, fiscal_year,
                            fiscal_period, item_section, start_line, end_line, text)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT (chunk_id) DO NOTHING
    """, rows)


def load_facts(conn: psycopg.Connection, facts: Iterable[XbrlFact]) -> int:
    rows = [
        (f.ticker, f.cik, f.concept, f.unit, f.value, f.period_start, f.period_end,
         f.accession_no, f.form, f.filed_date, f.restated)
        for f in facts
    ]
    return _executemany(conn, """
        INSERT INTO xbrl_facts (ticker, cik, concept, unit, value, period_start,
                                period_end, accession_no, form, filed_date, restated)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT ON CONSTRAINT xbrl_facts_economic_fact_key DO NOTHING
    """, rows)


def load_questions(conn: psycopg.Connection, questions: Iterable[EvalQuestion]) -> int:
    rows = [
        (q.question_id, q.question, q.question_type, q.tickers[0], q.concept,
         q.expected_value if isinstance(q.expected_value, float) else None,
         q.expected_value if isinstance(q.expected_value, str) else None,
         q.unit, q.rel_tolerance, q.abs_tolerance, list(q.source_accessions))
        for q in questions
    ]
    return _executemany(conn, """
        INSERT INTO eval_questions (question_id, question, question_type, ticker, concept,
                                    expected_value, expected_text, unit, rel_tolerance,
                                    abs_tolerance, source_accessions)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT (question_id) DO NOTHING
    """, rows)


def counts(conn: psycopg.Connection) -> dict[str, int]:
    out: dict[str, int] = {}
    with conn.cursor() as cur:
        for table in ("filings", "chunks", "xbrl_facts", "eval_questions"):
            cur.execute(f"SELECT count(*) FROM {table}")  # noqa: S608 - fixed table list
            out[table] = cur.fetchone()[0]
    return out


def assert_loaded(
    conn: psycopg.Connection, expected: dict[str, int], source: str = "<db>"
) -> None:
    """Row counts must match what ingest produced (D-0009).

    A partial load is the database version of a truncated corpus: every query still
    works, and the answers are quietly incomplete.
    """
    actual = counts(conn)
    problems = [
        f"{table}: {actual.get(table, 0)} loaded, expected {want}"
        for table, want in expected.items()
        if actual.get(table, 0) != want
    ]
    if problems:
        raise DbError(f"{source}: " + "; ".join(problems))
