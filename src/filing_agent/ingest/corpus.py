"""Downloads indexed filings and writes the manifest every downstream step reads.

The manifest is the contract between ingest and the rest of the system: chunker,
XBRL loader, and eval authoring all key off these rows. It carries `report_date` so
any downstream step can re-assert the fiscal-period invariant (D-0007) without
re-deriving it, and a content hash so corruption is detectable rather than assumed away.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Final

from ..config import FISCAL_YEARS, TICKERS
from .edgar_client import EdgarClient
from .filing_index import FilingRef, FiscalPeriodError, assert_corpus_complete

MANIFEST_NAME: Final[str] = "manifest.jsonl"
DEFAULT_ROOT: Final[Path] = Path("data/raw")
_HASH_CHUNK: Final[int] = 1 << 20


class ManifestRow(FilingRef):
    """A filing plus what we actually have on disk.

    Subclasses FilingRef so the completeness assertion runs unchanged against manifest
    rows — the invariant is enforced against disk reality, not index intent.
    """

    local_path: str = ""
    sha256: str = ""
    size_bytes: int = 0
    downloaded: bool = False


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(_HASH_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def local_path_for(ref: FilingRef, root: Path | str = DEFAULT_ROOT) -> Path:
    """Layout: <root>/<TICKER>/<accession>/<primary document>.

    Keyed on accession number because that is EDGAR's own primary key and what a
    citation ultimately resolves to.
    """
    return Path(root) / ref.ticker / ref.accession_no / ref.primary_document


def download_corpus(
    client: EdgarClient,
    refs: Sequence[FilingRef],
    root: Path | str = DEFAULT_ROOT,
) -> list[ManifestRow]:
    """Fetch every non-amended filing; record amendments without fetching them (D-0006).

    Cache-first via EdgarClient, so re-running costs nothing and hits SEC zero times.
    """
    rows: list[ManifestRow] = []
    for ref in refs:
        if ref.is_amendment:
            rows.append(ManifestRow(**ref.model_dump(), downloaded=False))
            continue
        path = local_path_for(ref, root)
        client.download(ref.archive_url, path)
        rows.append(
            ManifestRow(
                **ref.model_dump(),
                local_path=str(path),
                sha256=sha256_of(path),
                size_bytes=path.stat().st_size,
                downloaded=True,
            )
        )
    return rows


def corpus_fingerprint(rows: Sequence[ManifestRow]) -> str:
    """One hash over the whole corpus, stable under ordering.

    Pairs with the config hash from D-0002: together they pin *which filings* and
    *which model* produced any published number.
    """
    digest = hashlib.sha256()
    for accession, file_hash in sorted((r.accession_no, r.sha256) for r in rows if r.downloaded):
        digest.update(f"{accession}:{file_hash}\n".encode())
    return digest.hexdigest()


def write_manifest(rows: Sequence[ManifestRow], root: Path | str = DEFAULT_ROOT) -> Path:
    path = Path(root) / MANIFEST_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for row in sorted(rows, key=lambda r: (r.ticker, r.report_date, r.form)):
            fh.write(row.model_dump_json() + "\n")
    return path


def read_manifest(root: Path | str = DEFAULT_ROOT) -> list[ManifestRow]:
    path = Path(root) / MANIFEST_NAME
    with path.open(encoding="utf-8") as fh:
        return [ManifestRow(**json.loads(line)) for line in fh if line.strip()]


def assert_manifest_matches_disk(
    rows: Sequence[ManifestRow], root: Path | str = DEFAULT_ROOT
) -> None:
    """Every row claiming a download must have an intact file behind it (D-0007).

    Re-hashing rather than trusting the recorded value: a manifest that agrees with
    itself proves nothing. This catches truncation, partial writes, and edits made
    to the corpus after the fact.
    """
    problems: list[str] = []
    for row in rows:
        if not row.downloaded:
            if row.local_path or row.sha256:
                problems.append(f"{row.accession_no}: not downloaded but claims a file")
            continue
        path = Path(row.local_path)
        if not path.exists():
            problems.append(f"{row.accession_no}: missing file {path}")
        elif path.stat().st_size != row.size_bytes:
            problems.append(f"{row.accession_no}: size {path.stat().st_size} != {row.size_bytes}")
        elif sha256_of(path) != row.sha256:
            problems.append(f"{row.accession_no}: content hash mismatch at {path}")
    if problems:
        raise FiscalPeriodError(
            "manifest does not match disk:\n  " + "\n  ".join(problems[:10])
        )


def assert_corpus_on_disk(
    rows: Sequence[ManifestRow],
    tickers: tuple[str, ...] = TICKERS,
    fiscal_years: tuple[int, ...] = FISCAL_YEARS,
    root: Path | str = DEFAULT_ROOT,
) -> None:
    """The full disk-side gate: completeness (D-0009) plus integrity."""
    assert_corpus_complete(list(rows), tickers, fiscal_years)
    assert_manifest_matches_disk(rows, root)
