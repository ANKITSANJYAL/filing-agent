"""Postgres integration tests. Skipped when no database is reachable.

They must skip rather than fail so `pytest` stays green on a machine without Docker —
CI runs the unit suite; these run where the container is up.
"""

import datetime as dt

import pytest

psycopg = pytest.importorskip("psycopg")

from filing_agent.retrieval import db  # noqa: E402


@pytest.fixture(scope="module")
def conn():
    try:
        connection = db.connect()
    except Exception as exc:  # noqa: BLE001 - any connection failure means "skip"
        pytest.skip(f"no Postgres reachable: {exc}")
    db.init_schema(connection)
    yield connection
    connection.close()


@pytest.fixture
def tx(conn):
    """Each test runs in a transaction that is rolled back, so the corpus is untouched."""
    yield conn
    conn.rollback()


def _any_accession(conn) -> str:
    with conn.cursor() as cur:
        cur.execute("SELECT accession_no FROM filings LIMIT 1")
        row = cur.fetchone()
    if row is None:
        pytest.skip("database has no filings loaded")
    return row[0]


# --- Schema invariants (the point of using a database at all) -------------------

def test_instant_fact_cannot_have_two_values(tx) -> None:
    """D-0020 enforced by the schema, not by remembering to call an assertion."""
    with tx.cursor() as cur:
        # An INSTANT fact (period_start IS NULL) on purpose: plain UNIQUE treats
        # NULLs as distinct, so these were the rows the constraint silently missed.
        cur.execute("SELECT ticker, concept, unit, period_start, period_end, value "
                    "FROM xbrl_facts WHERE period_start IS NULL LIMIT 1")
        row = cur.fetchone()
    if row is None:
        pytest.skip("database has no facts loaded")
    ticker, concept, unit, start, end, value = row
    with pytest.raises(psycopg.errors.UniqueViolation), tx.cursor() as cur:
        cur.execute(
            "INSERT INTO xbrl_facts (ticker, cik, concept, unit, value, period_start,"
            " period_end, accession_no, filed_date) VALUES (%s,1,%s,%s,%s,%s,%s,%s,%s)",
            (ticker, concept, unit, value * 2, start, end, _any_accession(tx),
             dt.date(2025, 1, 1)),
        )


def test_chunk_cannot_cite_a_filing_outside_the_corpus(tx) -> None:
    """A dangling citation is unresolvable; the FK makes it unstorable."""
    with pytest.raises(psycopg.errors.ForeignKeyViolation), tx.cursor() as cur:
        cur.execute(
            "INSERT INTO chunks (chunk_id, accession_no, ticker, form_type, fiscal_year,"
            " fiscal_period, item_section, start_line, end_line, text)"
            " VALUES ('t:0','0000000000-00-000000','X','10-K',2025,'2025-01-01','MDA',0,1,'t')"
        )


def test_generated_tsvector_is_populated(tx) -> None:
    """Lexical arm (PROPOSAL §8.4) depends on the generated column being non-empty."""
    with tx.cursor() as cur:
        cur.execute("SELECT count(*) FROM chunks WHERE tsv IS NULL OR tsv = ''::tsvector")
        assert cur.fetchone()[0] == 0


def test_lexical_search_returns_ranked_hits(tx) -> None:
    with tx.cursor() as cur:
        cur.execute(
            "SELECT ticker, ts_rank(tsv, q) FROM chunks, plainto_tsquery('english', %s) q"
            " WHERE tsv @@ q ORDER BY 2 DESC LIMIT 5",
            ("data center revenue growth",),
        )
        hits = cur.fetchall()
    if not hits:
        pytest.skip("database has no chunks loaded")
    assert all(rank > 0 for _, rank in hits)
    assert [r for _, r in hits] == sorted((r for _, r in hits), reverse=True)


# --- Loader behaviour ----------------------------------------------------------

def test_schema_init_is_idempotent(conn) -> None:
    db.init_schema(conn)
    db.init_schema(conn)


def test_reloading_the_same_rows_inserts_nothing(conn) -> None:
    """Re-running ingest must not duplicate; loaders are ON CONFLICT DO NOTHING."""
    from filing_agent.ingest.corpus import read_manifest
    manifest = read_manifest()
    before = db.counts(conn)["filings"]
    db.load_filings(conn, manifest)
    assert db.counts(conn)["filings"] == before


def test_assert_loaded_flags_a_partial_load(conn) -> None:
    """A partial load answers every query and is quietly incomplete (D-0009)."""
    with pytest.raises(db.DbError, match="expected"):
        db.assert_loaded(conn, {"filings": 10**9})


def test_assert_loaded_passes_on_actual_counts(conn) -> None:
    db.assert_loaded(conn, db.counts(conn))
