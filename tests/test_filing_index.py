"""Filing-index tests. No network — a synthetic submissions payload throughout.

The headline test is the fiscal-year one: it encodes the failure mode that would
otherwise reach a published table with nothing red along the way (D-0007).
"""

import datetime as dt

import httpx
import pytest

from filing_agent.ingest.edgar_client import EdgarClient
from filing_agent.ingest.filing_index import (
    FilingRef,
    FiscalPeriodError,
    assert_corpus_complete,
    assert_fiscal_periods,
    fiscal_year_for,
    iter_recent,
    list_filings,
    resolve_ciks,
    summarize_table,
)

UA = "Test User test@example.com"

# Columns mirror SEC's real payload shape: parallel arrays, not objects.
SUBMISSIONS = {
    "cik": "1045810",
    "name": "NVIDIA CORP",
    "fiscalYearEnd": "0131",
    "filings": {
        "recent": {
            "accessionNumber": ["0001045810-25-000023", "0001045810-24-000029",
                                "0001045810-25-000010", "0001045810-25-000099",
                                "0001045810-23-000017", "0001045810-25-000055"],
            "filingDate": ["2025-02-26", "2024-02-21", "2025-02-26",
                           "2025-05-28", "2023-02-24", "2025-03-01"],
            "reportDate": ["2025-01-26", "2024-01-28", "2025-01-26",
                           "2025-04-27", "2023-01-29", "2025-01-26"],
            "form": ["10-K", "10-K", "8-K", "10-Q", "10-K", "8-K"],
            "items": ["", "", "2.02,9.01", "", "", "7.01"],
            "primaryDocument": ["nvda-20250126.htm", "nvda-20240128.htm", "ex99-1.htm",
                                "nvda-20250427.htm", "nvda-20230129.htm", "ex99-2.htm"],
        }
    },
}


def _client(payload) -> EdgarClient:
    return EdgarClient(
        user_agent=UA,
        transport=httpx.MockTransport(lambda req: httpx.Response(200, json=payload)),
    )


# --- Fiscal-year derivation (the D-0007 failure mode) --------------------------

@pytest.mark.parametrize(
    ("period_end", "expected_fy"),
    [
        ("2025-01-26", 2025),  # NVDA FY2025 — filed Feb 2025, FYE January
        ("2024-09-28", 2024),  # AAPL FY2024 — FYE late September
        ("2025-01-31", 2025),  # WMT  FY2025 — FYE January 31
        ("2024-06-30", 2024),  # MSFT FY2024 — FYE June 30
    ],
)
def test_fiscal_year_anchors(period_end: str, expected_fy: int) -> None:
    fy, ambiguous = fiscal_year_for(dt.date.fromisoformat(period_end))
    assert (fy, ambiguous) == (expected_fy, False)


def test_early_january_period_is_flagged_not_guessed() -> None:
    _, ambiguous = fiscal_year_for(dt.date(2025, 1, 3))
    assert ambiguous is True


def test_filing_date_and_period_end_disagree_on_fiscal_year() -> None:
    """The bug this whole module exists to prevent: NVDA's FY2025 10-K was *filed* in
    Feb 2025 but its period ended Jan 2025 — using filing date happens to agree here,
    while the FY2024 10-K filed 2024-02-21 covers a period ending 2024-01-28."""
    with _client(SUBMISSIONS) as client:
        filings = list_filings(client, "NVDA", 1045810, fiscal_years=(2024,))
    assert [f.fiscal_year for f in filings] == [2024]
    assert filings[0].report_date == dt.date(2024, 1, 28)
    assert filings[0].filing_date == dt.date(2024, 2, 21)


# --- Scope filtering -----------------------------------------------------------

def test_out_of_scope_fiscal_years_are_dropped() -> None:
    with _client(SUBMISSIONS) as client:
        filings = list_filings(client, "NVDA", 1045810, fiscal_years=(2024, 2025))
    assert all(f.fiscal_year in (2024, 2025) for f in filings)
    assert dt.date(2023, 1, 29) not in [f.report_date for f in filings]


def test_8ks_are_excluded_entirely() -> None:
    """D-0010: 8-K reportDate is an event date, not a period end, and EX-99 earnings
    exhibits are non-XBRL press releases that can contradict our verification source."""
    with _client(SUBMISSIONS) as client:
        filings = list_filings(client, "NVDA", 1045810)
    assert [f.form for f in filings if f.form.startswith("8-K")] == []
    assert {f.form for f in filings} <= {"10-K", "10-Q", "10-K/A", "10-Q/A"}


def test_amendments_are_recorded_but_flagged() -> None:
    payload = {**SUBMISSIONS, "filings": {"recent": {
        **SUBMISSIONS["filings"]["recent"],
        "form": ["10-K/A", "10-K", "8-K", "10-Q", "10-K", "8-K"],
    }}}
    with _client(payload) as client:
        filings = list_filings(client, "NVDA", 1045810)
    amended = [f for f in filings if f.is_amendment]
    assert len(amended) == 1 and amended[0].form == "10-K/A"


# --- Payload handling ----------------------------------------------------------

def test_columnar_payload_is_transposed_to_rows() -> None:
    rows = list(iter_recent(SUBMISSIONS))
    assert len(rows) == 6
    assert rows[0]["accessionNumber"] == "0001045810-25-000023"
    assert rows[0]["form"] == "10-K"


def test_ragged_payload_raises_instead_of_misaligning() -> None:
    ragged = {"filings": {"recent": {"accessionNumber": ["a", "b"], "form": ["10-K"]}}}
    with pytest.raises(ValueError):
        list(iter_recent(ragged))


def test_empty_payload_yields_nothing() -> None:
    assert list(iter_recent({"filings": {"recent": {}}})) == []


def test_unknown_ticker_is_a_hard_error() -> None:
    with (
        _client({"0": {"ticker": "NVDA", "cik_str": 1045810}}) as client,
        pytest.raises(LookupError, match="NOPE"),
    ):
        resolve_ciks(client, ("NVDA", "NOPE"))


# --- The boundary assertion ----------------------------------------------------

def _ref(**kw) -> FilingRef:
    base = dict(ticker="NVDA", cik=1045810, form="10-K",
                accession_no="0001045810-25-000023", report_date=dt.date(2025, 1, 26),
                filing_date=dt.date(2025, 2, 26), fiscal_year=2025,
                primary_document="nvda-20250126.htm")
    return FilingRef(**{**base, **kw})


def test_assertion_passes_on_an_in_scope_corpus() -> None:
    assert_fiscal_periods([_ref()], fiscal_years=(2024, 2025))


def test_assertion_hard_fails_on_out_of_scope_period() -> None:
    stale = _ref(report_date=dt.date(2023, 1, 29), fiscal_year=2023)
    with pytest.raises(FiscalPeriodError, match="outside"):
        assert_fiscal_periods([_ref(), stale], fiscal_years=(2024, 2025))


def test_assertion_hard_fails_on_ambiguous_fiscal_label() -> None:
    with pytest.raises(FiscalPeriodError, match="early January"):
        assert_fiscal_periods([_ref(fy_ambiguous=True)], fiscal_years=(2024, 2025))


def test_archive_url_strips_dashes_and_pads_nothing() -> None:
    assert _ref().archive_url == (
        "https://www.sec.gov/Archives/edgar/data/1045810/"
        "000104581025000023/nvda-20250126.htm"
    )


def test_summary_table_reports_counts_by_form() -> None:
    table = summarize_table([_ref(), _ref(form="10-Q")])
    assert "2 filings" in table and "10-K=1" in table and "10-Q=1" in table


# --- Completeness (D-0009): the two defects that shipped past D-0007 -----------

def test_complete_corpus_passes() -> None:
    both_years = [_ref(fiscal_year=2024, report_date=dt.date(2024, 1, 28)), _ref()]
    assert_corpus_complete(both_years, ("NVDA",), fiscal_years=(2024, 2025))


def test_empty_corpus_fails_completeness_though_it_passes_period_check() -> None:
    """The XOM defect: a ticker resolving to a successor entity yields zero filings.
    assert_fiscal_periods is trivially satisfied by an empty list; this is not."""
    assert_fiscal_periods([], fiscal_years=(2024, 2025))  # vacuously true
    with pytest.raises(FiscalPeriodError, match="0 of 2 annual reports"):
        assert_corpus_complete([], ("XOM",), fiscal_years=(2024, 2025))


def test_truncated_history_names_the_missing_year() -> None:
    """The JPM defect: overflow files unread, so only the most recent FY survives."""
    with pytest.raises(FiscalPeriodError, match=r"missing FY\[2024\]"):
        assert_corpus_complete([_ref(ticker="JPM")], ("JPM",), fiscal_years=(2024, 2025))


def test_amendments_do_not_count_toward_completeness() -> None:
    """A 10-K/A must not paper over a missing original."""
    refs = [_ref(), _ref(fiscal_year=2024, form="10-K/A", is_amendment=True)]
    with pytest.raises(FiscalPeriodError, match="1 of 2"):
        assert_corpus_complete(refs, ("NVDA",), fiscal_years=(2024, 2025))


# --- Overflow files + CIK overrides --------------------------------------------

def test_overflow_files_are_fetched_when_they_overlap_the_window() -> None:
    overflow_rows = {
        "accessionNumber": ["0000019617-24-000123"], "filingDate": ["2024-02-16"],
        "reportDate": ["2023-12-31"], "form": ["10-K"], "items": [""],
        "primaryDocument": ["jpm-20231231.htm"],
    }
    payload = {"filings": {
        "recent": {"accessionNumber": [], "filingDate": [], "reportDate": [],
                   "form": [], "items": [], "primaryDocument": []},
        "files": [{"name": "CIK0000019617-submissions-001.json",
                   "filingFrom": "2024-01-01", "filingTo": "2024-06-30"}],
    }}

    def handler(request: httpx.Request) -> httpx.Response:
        body = overflow_rows if "submissions-001" in str(request.url) else payload
        return httpx.Response(200, json=body)

    with EdgarClient(user_agent=UA, transport=httpx.MockTransport(handler)) as client:
        filings = list_filings(client, "JPM", 19617, fiscal_years=(2023,))
    assert [f.accession_no for f in filings] == ["0000019617-24-000123"]


def test_overflow_files_outside_the_window_are_skipped() -> None:
    calls: list = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, json={"filings": {
            "recent": {"accessionNumber": [], "form": [], "filingDate": [],
                       "reportDate": [], "items": [], "primaryDocument": []},
            "files": [{"name": "old.json", "filingFrom": "1994-01-26",
                       "filingTo": "2015-06-02"}],
        }})

    with EdgarClient(user_agent=UA, transport=httpx.MockTransport(handler)) as client:
        list_filings(client, "AAPL", 320193, fiscal_years=(2024, 2025))
    assert len(calls) == 1  # submissions only; the 1994-2015 file was never fetched


def test_cik_override_wins_over_sec_ticker_map() -> None:
    """XOM must resolve to the predecessor filer that holds FY2024-25 reports."""
    ticker_map = {"0": {"ticker": "XOM", "cik_str": 2115436}}
    with _client(ticker_map) as client:
        assert resolve_ciks(client, ("XOM",))["XOM"] == 34088
