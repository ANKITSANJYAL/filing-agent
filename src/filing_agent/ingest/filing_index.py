"""Turns tickers into a scoped, asserted list of filings to download.

This is where EDGAR's schema lives: CIK resolution, the columnar submissions payload,
form/period filtering, and the fiscal-period assertion from DECISIONS.md D-0007.
It decides *what* to fetch; edgar_client decides *how*.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterator
from typing import Any, Final

from pydantic import BaseModel

from ..config import CIK_OVERRIDES, EXPECTED_ANNUAL_REPORTS_PER_TICKER, FISCAL_YEARS
from .edgar_client import EdgarClient

TICKER_MAP_URL: Final[str] = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL: Final[str] = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
OVERFLOW_URL: Final[str] = "https://data.sec.gov/submissions/{name}"
ARCHIVE_URL: Final[str] = "https://www.sec.gov/Archives/edgar/data/{cik}/{accn}/{doc}"

# 10-K/10-Q only. 8-Ks were evaluated and dropped in D-0010: their `reportDate` is an
# event date rather than a fiscal period end (so fiscal_year_for is the wrong function
# for them), and their EX-99 earnings exhibits are non-XBRL, non-GAAP press releases
# that cannot participate in tier-1 numeric verification.
PERIODIC_FORMS: Final[frozenset[str]] = frozenset({"10-K", "10-Q"})

# A period ending in the first days of January is labelled inconsistently across
# issuers — some call it the prior fiscal year. None of our 8 tickers should land
# here, so tripping this means an assumption broke.
FY_AMBIGUITY_WINDOW_DAYS: Final[int] = 14


class FiscalPeriodError(AssertionError):
    """A filing's declared period contradicts the locked corpus scope."""


class FilingRef(BaseModel):
    """One filing we intend to download. The manifest row, before any bytes move."""

    ticker: str
    cik: int
    form: str
    accession_no: str
    report_date: dt.date  # SEC's "reportDate" — the period of report, not the filing date
    filing_date: dt.date
    fiscal_year: int
    primary_document: str
    items: tuple[str, ...] = ()
    is_amendment: bool = False
    fy_ambiguous: bool = False

    @property
    def archive_url(self) -> str:
        return ARCHIVE_URL.format(
            cik=self.cik,
            accn=self.accession_no.replace("-", ""),
            doc=self.primary_document,
        )


def fiscal_year_for(report_date: dt.date) -> tuple[int, bool]:
    """Fiscal year = the calendar year the period *ends* in. Returns (fy, ambiguous).

    Holds for all 8 locked tickers: NVDA FY2025 ended 2025-01-26, AAPL FY2024 ended
    2024-09-28, WMT FY2025 ended 2025-01-31. Filing date would give the wrong answer
    for every one of them, which is the whole point of D-0007.
    """
    ambiguous = report_date.month == 1 and report_date.day <= FY_AMBIGUITY_WINDOW_DAYS
    return report_date.year, ambiguous


def resolve_ciks(client: EdgarClient, tickers: tuple[str, ...]) -> dict[str, int]:
    """Map tickers to CIKs, applying documented overrides.

    SEC's map points at the *current* registrant, which after a reorganization is not
    the filer that submitted the reports in our window. `CIK_OVERRIDES` pins the filer
    that actually holds them (D-0008).
    """
    raw = client.get_json(TICKER_MAP_URL)
    by_ticker = {row["ticker"].upper(): int(row["cik_str"]) for row in raw.values()}
    missing = sorted({
        t for t in tickers
        if t.upper() not in by_ticker and t.upper() not in CIK_OVERRIDES
    })
    if missing:
        raise LookupError(f"tickers absent from SEC's ticker map: {missing}")
    return {t: CIK_OVERRIDES.get(t.upper(), by_ticker.get(t.upper())) for t in tickers}


def _iter_columnar(block: dict[str, Any]) -> Iterator[dict[str, Any]]:
    """Transpose one of SEC's columnar filing blocks into rows.

    These are parallel arrays — accessionNumber[i] belongs with form[i] and
    reportDate[i]. `strict=True` turns a ragged payload into an immediate error rather
    than a silent off-by-one that would misattribute every field after it.
    """
    columns = [c for c, v in block.items() if isinstance(v, list)]
    if not columns or not block.get("accessionNumber"):
        return
    for row in zip(*(block[c] for c in columns), strict=True):
        yield dict(zip(columns, row, strict=True))


def iter_recent(submissions: dict[str, Any]) -> Iterator[dict[str, Any]]:
    """Rows from `filings.recent` only — the window SEC returns inline."""
    yield from _iter_columnar(submissions.get("filings", {}).get("recent", {}))


def iter_all_filings(
    client: EdgarClient, submissions: dict[str, Any], fiscal_years: tuple[int, ...]
) -> Iterator[dict[str, Any]]:
    """Rows from `filings.recent` **plus** any overflow files that overlap our window.

    `recent` is capped, and the cap is measured in documents, not years. JPM files
    ~25k documents a year (mostly structured-note prospectuses), so its inline window
    reaches back only ~12 months and FY2024 lives entirely in `filings.files[]`.
    Reading `recent` alone silently truncates the corpus for high-volume filers.
    """
    yield from iter_recent(submissions)
    # A filing for FY N can be submitted well into calendar N+1, so widen by a year
    # on each side rather than trusting a tight bound.
    lo, hi = f"{min(fiscal_years) - 1}-01-01", f"{max(fiscal_years) + 1}-12-31"
    for entry in submissions.get("filings", {}).get("files", []):
        if entry.get("filingTo", "") < lo or entry.get("filingFrom", "") > hi:
            continue
        yield from _iter_columnar(client.get_json(OVERFLOW_URL.format(name=entry["name"])))


def _parse_items(raw: str | None) -> tuple[str, ...]:
    return tuple(i.strip() for i in (raw or "").split(",") if i.strip())


def list_filings(
    client: EdgarClient,
    ticker: str,
    cik: int,
    fiscal_years: tuple[int, ...] = FISCAL_YEARS,
) -> list[FilingRef]:
    """In-scope filings for one ticker. Amendments are included but flagged (D-0006)."""
    submissions = client.get_json(SUBMISSIONS_URL.format(cik=cik))
    found: list[FilingRef] = []
    seen: set[str] = set()
    for row in iter_all_filings(client, submissions, fiscal_years):
        if row["accessionNumber"] in seen:
            continue
        seen.add(row["accessionNumber"])
        form: str = row["form"]
        base_form, is_amendment = form.removesuffix("/A"), form.endswith("/A")
        items = _parse_items(row.get("items"))
        if base_form not in PERIODIC_FORMS or not row.get("reportDate"):
            continue
        report_date = dt.date.fromisoformat(row["reportDate"])
        fiscal_year, fy_ambiguous = fiscal_year_for(report_date)
        if fiscal_year not in fiscal_years:
            continue
        found.append(
            FilingRef(
                ticker=ticker,
                cik=cik,
                form=form,
                accession_no=row["accessionNumber"],
                report_date=report_date,
                filing_date=dt.date.fromisoformat(row["filingDate"]),
                fiscal_year=fiscal_year,
                primary_document=row["primaryDocument"],
                items=items,
                is_amendment=is_amendment,
                fy_ambiguous=fy_ambiguous,
            )
        )
    return sorted(found, key=lambda f: (f.report_date, f.form))


def assert_fiscal_periods(
    filings: list[FilingRef], fiscal_years: tuple[int, ...] = FISCAL_YEARS
) -> None:
    """Hard-fail on a corpus that contradicts our claimed scope (D-0007).

    Raises rather than warns: every downstream step is self-consistent with a corrupt
    corpus, so this boundary is the only place the error is detectable from inside.
    """
    out_of_scope = [f for f in filings if f.fiscal_year not in fiscal_years]
    if out_of_scope:
        sample = ", ".join(
            f"{f.ticker} {f.form} {f.report_date} -> FY{f.fiscal_year}"
            for f in out_of_scope[:5]
        )
        raise FiscalPeriodError(
            f"{len(out_of_scope)} filing(s) outside FY{fiscal_years}: {sample}"
        )
    ambiguous = [f for f in filings if f.fy_ambiguous]
    if ambiguous:
        sample = ", ".join(f"{f.ticker} {f.form} ends {f.report_date}" for f in ambiguous[:5])
        raise FiscalPeriodError(
            "period ends in early January, where issuers disagree on the fiscal-year "
            f"label — resolve by hand before proceeding: {sample}"
        )


def assert_corpus_complete(
    filings: list[FilingRef],
    tickers: tuple[str, ...],
    fiscal_years: tuple[int, ...] = FISCAL_YEARS,
) -> None:
    """Hard-fail on a corpus that is silently *incomplete* (D-0009).

    D-0007 asserts the filings we have are from the right periods. It cannot detect
    filings we never retrieved — an empty result set trivially satisfies it. This
    encodes the count we expect: one 10-K per ticker per fiscal year.

    Written because two real defects shipped past the period assertion: a ticker that
    resolved to a successor entity with no history, and a high-volume filer whose older
    filings sat in overflow files we weren't reading. Both reported success.
    """
    expected = EXPECTED_ANNUAL_REPORTS_PER_TICKER
    problems: list[str] = []
    for ticker in tickers:
        annual = {f.fiscal_year for f in filings
                  if f.ticker == ticker and f.form == "10-K" and not f.is_amendment}
        if len(annual) != expected:
            missing = sorted(set(fiscal_years) - annual)
            problems.append(
                f"{ticker}: {len(annual)} of {expected} annual reports"
                + (f" (missing FY{missing})" if missing else "")
            )
    if problems:
        raise FiscalPeriodError(
            "corpus is incomplete — an empty or truncated result satisfies every other "
            "check, so this is the only place it surfaces:\n  " + "\n  ".join(problems)
        )


def summarize_table(filings: list[FilingRef]) -> str:
    """The human eyeball check: ticker x form x period x fiscal year x filing date."""
    header = f"{'TICKER':<7}{'FORM':<8}{'PERIOD END':<13}{'FY':<7}{'FILED':<13}FLAGS"
    lines = [header, "-" * len(header)]
    for f in sorted(filings, key=lambda f: (f.ticker, f.report_date)):
        flags = " ".join(
            x for x in ("AMENDED" if f.is_amendment else "",
                        "FY?" if f.fy_ambiguous else "",
                        f"items={'/'.join(f.items)}" if f.form.startswith("8-K") else "")
            if x
        )
        lines.append(
            f"{f.ticker:<7}{f.form:<8}{f.report_date.isoformat():<13}"
            f"{f.fiscal_year:<7}{f.filing_date.isoformat():<13}{flags}"
        )
    counts: dict[str, int] = {}
    for f in filings:
        counts[f.form] = counts.get(f.form, 0) + 1
    lines.append("-" * len(header))
    lines.append(
        f"{len(filings)} filings: "
        + ", ".join(f"{k}={v}" for k, v in sorted(counts.items()))
    )
    return "\n".join(lines)
