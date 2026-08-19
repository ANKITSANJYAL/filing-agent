"""Corpus/manifest tests. No network — MockTransport serves fake filing bytes.

The load-bearing tests are the tamper ones: a manifest that only agrees with itself
proves nothing, so integrity is re-derived from disk rather than trusted.
"""

import datetime as dt
from pathlib import Path

import httpx
import pytest

from filing_agent.ingest.edgar_client import EdgarClient
from filing_agent.ingest.corpus import (
    ManifestRow,
    assert_corpus_on_disk,
    assert_manifest_matches_disk,
    corpus_fingerprint,
    download_corpus,
    local_path_for,
    read_manifest,
    write_manifest,
)
from filing_agent.ingest.filing_index import FilingRef, FiscalPeriodError

UA = "Ankit Sanjyal asanjyal56@gmail.com"
BODY = b"<html><body>Item 7. MD&A</body></html>"


def _ref(**kw) -> FilingRef:
    base = dict(ticker="NVDA", cik=1045810, form="10-K",
                accession_no="0001045810-25-000023", report_date=dt.date(2025, 1, 26),
                filing_date=dt.date(2025, 2, 26), fiscal_year=2025,
                primary_document="nvda-20250126.htm")
    return FilingRef(**{**base, **kw})


def _both_years() -> list[FilingRef]:
    return [
        _ref(),
        _ref(accession_no="0001045810-24-000029", report_date=dt.date(2024, 1, 28),
             filing_date=dt.date(2024, 2, 21), fiscal_year=2024,
             primary_document="nvda-20240128.htm"),
    ]


def _client() -> EdgarClient:
    return EdgarClient(
        user_agent=UA,
        transport=httpx.MockTransport(lambda r: httpx.Response(200, content=BODY)),
    )


# --- Layout and download -------------------------------------------------------

def test_layout_is_keyed_on_accession_number(tmp_path) -> None:
    path = local_path_for(_ref(), tmp_path)
    assert path == tmp_path / "NVDA" / "0001045810-25-000023" / "nvda-20250126.htm"


def test_download_records_hash_and_size(tmp_path) -> None:
    with _client() as client:
        rows = download_corpus(client, [_ref()], tmp_path)
    row = rows[0]
    assert row.downloaded is True
    assert row.size_bytes == len(BODY)
    assert len(row.sha256) == 64
    assert Path(row.local_path).read_bytes() == BODY


def test_amendments_are_recorded_but_never_fetched(tmp_path) -> None:
    """D-0006: existence in the manifest, no bytes on disk."""
    with _client() as client:
        rows = download_corpus(client, [_ref(form="10-K/A", is_amendment=True)], tmp_path)
        assert client.network_requests == 0
    assert rows[0].downloaded is False and rows[0].local_path == ""


def test_rerun_is_free(tmp_path) -> None:
    with _client() as client:
        download_corpus(client, _both_years(), tmp_path)
        first = client.network_requests
        download_corpus(client, _both_years(), tmp_path)
        assert client.network_requests == first == 2


# --- Manifest round-trip -------------------------------------------------------

def test_manifest_round_trips_and_preserves_report_date(tmp_path) -> None:
    """report_date must survive so downstream can re-assert the period invariant."""
    with _client() as client:
        rows = download_corpus(client, _both_years(), tmp_path)
    write_manifest(rows, tmp_path)
    loaded = read_manifest(tmp_path)
    assert [r.report_date for r in loaded] == [dt.date(2024, 1, 28), dt.date(2025, 1, 26)]
    assert {r.accession_no for r in loaded} == {r.accession_no for r in rows}


# --- Integrity: the tamper tests ----------------------------------------------

def test_truncated_file_is_caught(tmp_path) -> None:
    with _client() as client:
        rows = download_corpus(client, [_ref()], tmp_path)
    Path(rows[0].local_path).write_bytes(BODY[:5])
    with pytest.raises(FiscalPeriodError, match="size"):
        assert_manifest_matches_disk(rows, tmp_path)


def test_same_length_edit_is_caught_by_the_hash(tmp_path) -> None:
    """Size alone would pass here — this is why the hash is re-derived, not trusted."""
    with _client() as client:
        rows = download_corpus(client, [_ref()], tmp_path)
    tampered = BODY.replace(b"Item 7.", b"Item 8.")
    assert len(tampered) == len(BODY)
    Path(rows[0].local_path).write_bytes(tampered)
    with pytest.raises(FiscalPeriodError, match="content hash mismatch"):
        assert_manifest_matches_disk(rows, tmp_path)


def test_missing_file_is_caught(tmp_path) -> None:
    with _client() as client:
        rows = download_corpus(client, [_ref()], tmp_path)
    Path(rows[0].local_path).unlink()
    with pytest.raises(FiscalPeriodError, match="missing file"):
        assert_manifest_matches_disk(rows, tmp_path)


def test_row_claiming_a_file_it_did_not_download_is_caught() -> None:
    bogus = ManifestRow(**_ref().model_dump(), downloaded=False, local_path="/x/y.htm")
    with pytest.raises(FiscalPeriodError, match="claims a file"):
        assert_manifest_matches_disk([bogus])


# --- Fingerprint ---------------------------------------------------------------

def test_fingerprint_is_order_independent(tmp_path) -> None:
    with _client() as client:
        rows = download_corpus(client, _both_years(), tmp_path)
    assert corpus_fingerprint(rows) == corpus_fingerprint(list(reversed(rows)))


def test_fingerprint_changes_when_content_changes(tmp_path) -> None:
    with _client() as client:
        rows = download_corpus(client, _both_years(), tmp_path)
    before = corpus_fingerprint(rows)
    rows[0].sha256 = "0" * 64
    assert corpus_fingerprint(rows) != before


# --- The combined disk-side gate ----------------------------------------------

def test_disk_gate_rejects_an_incomplete_corpus(tmp_path) -> None:
    """Only FY2025 downloaded: files are intact, corpus is not."""
    with _client() as client:
        rows = download_corpus(client, [_ref()], tmp_path)
    assert_manifest_matches_disk(rows, tmp_path)  # integrity fine
    with pytest.raises(FiscalPeriodError, match="1 of 2 annual reports"):
        assert_corpus_on_disk(rows, ("NVDA",), (2024, 2025), tmp_path)


def test_disk_gate_passes_on_a_complete_intact_corpus(tmp_path) -> None:
    with _client() as client:
        rows = download_corpus(client, _both_years(), tmp_path)
    assert_corpus_on_disk(rows, ("NVDA",), (2024, 2025), tmp_path)
