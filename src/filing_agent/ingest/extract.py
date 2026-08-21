"""HTML -> clean text for SEC inline-XBRL filings.

Two things a naive tag-stripper gets wrong, both measured on NVDA's FY2025 10-K:

1. It ingests the `<ix:header>` block — 125,940 of 381,767 extracted characters (33%)
   were XBRL context definitions (`us-gaap:...Member`, entity IDs, axis dates) that no
   human reader ever sees. Those became the two densest "numeric" chunks in the file.
2. It flattens tables, so a row's cells lose their row and a chunk boundary can sever
   a figure from its label.

This module drops the machine-only subtrees and preserves table geometry as text.
"""

from __future__ import annotations

from html.parser import HTMLParser
from typing import Final

# Subtrees that carry no reader-visible content. `ix:header`/`ix:hidden` hold the
# inline-XBRL context registry; the rendered document never shows them.
SKIP_SUBTREES: Final[frozenset[str]] = frozenset(
    {"script", "style", "ix:header", "ix:hidden"}
)
BLOCK_TAGS: Final[frozenset[str]] = frozenset(
    {"p", "div", "br", "h1", "h2", "h3", "h4", "h5", "h6", "li", "table", "tbody"}
)
ROW_TAG: Final[str] = "tr"
CELL_TAGS: Final[frozenset[str]] = frozenset({"td", "th"})

CELL_SEP: Final[str] = " | "

# Any token carrying an XBRL namespace prefix is machine metadata that leaked through.
_XBRL_PREFIXES: Final[tuple[str, ...]] = (
    "us-gaap:", "xbrli:", "dei:", "ix:", "iso4217:", "srt:", "utr:",
)


class ExtractionError(AssertionError):
    """Extracted text failed a structural expectation (D-0007)."""


class _FilingTextExtractor(HTMLParser):
    """Streams filing HTML into text, preserving row/cell structure."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._skip_depth = 0
        self._skip_tag: str | None = None
        self._in_cell = False

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if self._skip_depth:
            # Only count nesting of the tag we are already skipping, so an unrelated
            # <div> inside <ix:header> cannot end the skip early.
            if tag == self._skip_tag:
                self._skip_depth += 1
            return
        if tag in SKIP_SUBTREES:
            self._skip_tag, self._skip_depth = tag, 1
            return
        if tag in CELL_TAGS:
            if self._in_cell:
                self._parts.append(CELL_SEP)
            self._in_cell = True
        elif tag == ROW_TAG:
            self._parts.append("\n")
            self._in_cell = False
        elif tag in BLOCK_TAGS:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if self._skip_depth:
            if tag == self._skip_tag:
                self._skip_depth -= 1
                if self._skip_depth == 0:
                    self._skip_tag = None
            return
        if tag in CELL_TAGS:
            self._in_cell = True  # next cell in this row gets a separator
        elif tag == ROW_TAG:
            self._parts.append("\n")
            self._in_cell = False
        elif tag in BLOCK_TAGS:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        if data.strip():
            self._parts.append(data)

    def text(self) -> str:
        return "".join(self._parts)


def _tidy_row(line: str) -> str:
    """Drop layout-spacer cells and reattach orphaned currency/sign symbols.

    SEC tables carry empty spacer cells for visual alignment, and render `$` in its
    own cell. Left alone, a three-figure row reads
    `Gross profit | 97,858 | | | 44,301 | | | 15,356` — burning chunk budget and
    obscuring the value alignment. Genuine nulls are em dashes, not blanks, so
    dropping empty cells does not lose data.
    """
    cells = [c.strip() for c in line.split(CELL_SEP.strip())]
    cells = [c for c in cells if c]
    merged: list[str] = []
    for cell in cells:
        if merged and merged[-1] in {"$", "(", "$("}:
            merged[-1] += cell
        else:
            merged.append(cell)
    return " | ".join(merged)


def _normalise(raw: str) -> str:
    """Collapse whitespace without destroying line or row structure."""
    lines: list[str] = []
    for line in raw.replace("\xa0", " ").split("\n"):
        cleaned = " ".join(line.split())
        if not cleaned.strip(" |").strip():
            continue
        lines.append(_tidy_row(cleaned) if "|" in cleaned else cleaned)
    return "\n".join(lines)


def extract_text(html: str) -> str:
    """Reader-visible text of a filing, one line per block or table row."""
    parser = _FilingTextExtractor()
    parser.feed(html)
    parser.close()
    return _normalise(parser.text())


def assert_no_xbrl_metadata(text: str, source: str = "<text>") -> None:
    """Hard-fail if machine-only XBRL tokens survived extraction (D-0007).

    Written because the naive extractor silently produced a corpus that was one-third
    context registry. Nothing errored; the text simply had a third more "content"
    than the document contains.
    """
    found = sorted({p for p in _XBRL_PREFIXES if p in text})
    if found:
        sample = next(
            (ln for ln in text.split("\n") if any(p in ln for p in found)), ""
        )
        raise ExtractionError(
            f"{source}: XBRL metadata leaked into extracted text {found}: {sample[:160]!r}"
        )


def assert_extraction_plausible(
    text: str, html: str, source: str = "<text>", min_ratio: float = 0.02
) -> None:
    """Hard-fail if extraction produced implausibly little text for the input size.

    Catches a parser that silently bailed — an empty or near-empty result otherwise
    flows downstream as "this filing just had nothing in it".
    """
    ratio = len(text) / max(len(html), 1)
    if ratio < min_ratio:
        raise ExtractionError(
            f"{source}: extracted {len(text):,} chars from {len(html):,} "
            f"({ratio:.3%}) — below the {min_ratio:.0%} floor; parser likely failed"
        )
