"""Item-anchored section detection for 10-K/10-Q filings.

Sections are located by their Item headings, choosing for each item the occurrence
that owns the most content — which discards table-of-contents entries (packed a few
lines apart) and cross-references without needing a TOC heuristic.

Measured across all 16 10-Ks: 48 of 64 section-slots are substantive. The 16
exceptions are four structural conventions, not parser failures, and are allowlisted
per ticker in `config.STUB_SECTION_ALLOWLIST` (D-0016). Content-anchored detection was
attempted and rejected — see that decision for why.
"""

from __future__ import annotations

import re
from itertools import pairwise
from typing import Final

from pydantic import BaseModel

BUSINESS: Final[str] = "BUSINESS"
RISK_FACTORS: Final[str] = "RISK_FACTORS"
MDA: Final[str] = "MDA"
MARKET_RISK: Final[str] = "MARKET_RISK"
FINANCIAL_STATEMENTS: Final[str] = "FINANCIAL_STATEMENTS"

# Item codes are unique within a 10-K, so no Part context is needed.
TENK_SECTIONS: Final[dict[str, str]] = {
    "1": BUSINESS, "1A": RISK_FACTORS, "7": MDA, "7A": MARKET_RISK, "8": FINANCIAL_STATEMENTS,
}
# A 10-Q reuses item numbers across Part I and Part II, so keys are Part-qualified.
TENQ_SECTIONS: Final[dict[tuple[str, str], str]] = {
    ("I", "1"): FINANCIAL_STATEMENTS, ("I", "2"): MDA,
    ("I", "3"): MARKET_RISK, ("II", "1A"): RISK_FACTORS,
}

REQUIRED_10K: Final[tuple[str, ...]] = (RISK_FACTORS, MDA, MARKET_RISK, FINANCIAL_STATEMENTS)
REQUIRED_10Q: Final[tuple[str, ...]] = (FINANCIAL_STATEMENTS, MDA)

# Separator class includes em/en dashes: COST writes "Item 7—Management's Discussion".
_ITEM_RE: Final[re.Pattern] = re.compile(
    r"^item\s+(\d{1,2}[A-Z]?)\s*[.:)\-–—]?\s*(.*)$", re.I
)
_PART_RE: Final[re.Pattern] = re.compile(r"^part\s+(I{1,3}V?|IV)\b", re.I)

# A body heading carries its title on the same line; a TOC entry is bare because the
# TOC is a table and the title lands in a separate cell. Not sufficient alone (51 of 64
# filings still had duplicates) but it cheaply removes most noise.
MIN_TITLE_CHARS: Final[int] = 4

# Generous on purpose. A 160-char cap silently dropped COST's real MD&A heading, which
# runs 171 chars because of a parenthetical about units. Prose almost never *begins*
# with "Item N", so the leading anchor does the discriminating, not the length.
MAX_HEADING_CHARS: Final[int] = 400
DEFAULT_MIN_LINES: Final[int] = 12


class SectionError(AssertionError):
    """A filing's sections failed a structural expectation (D-0007)."""


class Section(BaseModel):
    name: str
    start_line: int
    end_line: int  # exclusive
    heading: str
    item: str

    @property
    def n_lines(self) -> int:
        return self.end_line - self.start_line


def _headings(lines: list[str]) -> list[tuple[int, str, str, str]]:
    """Every titled Item heading as (line, part, item_code, heading_text)."""
    out: list[tuple[int, str, str, str]] = []
    part = ""
    for i, raw in enumerate(lines):
        line = raw.strip()
        if not line or len(line) > MAX_HEADING_CHARS:
            continue
        if (pm := _PART_RE.match(line)) is not None:
            part = pm.group(1).upper()
            continue
        if (m := _ITEM_RE.match(line)) is None:
            continue
        if len(m.group(2).strip(" .|")) < MIN_TITLE_CHARS:
            continue  # bare "Item 1A." — table-of-contents entry
        out.append((i, part, m.group(1).upper(), line))
    return out


def find_sections(lines: list[str], form: str = "10-K") -> list[Section]:
    """Locate sections by Item heading, keeping the occurrence that owns most content.

    A section ends at the next titled Item heading of any different item — not at the
    next *tracked* section — so RISK_FACTORS stops at Item 1B rather than swallowing
    Items 1B through 6.
    """
    heads = _headings(lines)
    is_10q = form.upper().startswith("10-Q")
    best: dict[str, tuple[int, str, str, int]] = {}
    for idx, (line_no, part, item, heading) in enumerate(heads):
        name = (
            TENQ_SECTIONS.get((part, item)) if is_10q else TENK_SECTIONS.get(item)
        )
        if name is None:
            continue
        end = next((h[0] for h in heads[idx + 1:] if h[2] != item), len(lines))
        span = end - line_no
        if name not in best or span > best[name][3]:
            best[name] = (line_no, heading, item, span)

    return [
        Section(name=name, start_line=start, end_line=start + span, heading=heading, item=item)
        for name, (start, heading, item, span) in sorted(best.items(), key=lambda kv: kv[1][0])
    ]


def assert_sections_ordered(sections: list[Section], source: str = "<filing>") -> None:
    """Sections must not overlap; an overlap means two items resolved to one region."""
    for a, b in pairwise(sections):
        if a.end_line > b.start_line:
            raise SectionError(
                f"{source}: {a.name} (ends {a.end_line}) overlaps "
                f"{b.name} (starts {b.start_line})"
            )


def assert_sections_substantive(
    sections: list[Section],
    required: tuple[str, ...],
    source: str = "<filing>",
    allowed_stubs: frozenset[str] = frozenset(),
    min_lines: int = DEFAULT_MIN_LINES,
) -> None:
    """Hard-fail on a required section that is missing or is a cross-reference stub.

    Presence is not substance. A section that exists and owns two lines reads as
    successfully chunked while containing nothing — the same shape of defect as an
    empty corpus passing a period check (D-0009). Known by-reference cases are
    allowlisted per ticker so they stay visible; anything new stops the run.
    """
    by_name = {s.name: s for s in sections}
    problems: list[str] = []
    for name in required:
        if name in allowed_stubs:
            continue
        section = by_name.get(name)
        if section is None:
            problems.append(f"{name}: not found")
        elif section.n_lines < min_lines:
            problems.append(
                f"{name}: only {section.n_lines} lines (Item {section.item}) — "
                "likely incorporated by reference; allowlist it or investigate"
            )
    if problems:
        raise SectionError(f"{source}: " + "; ".join(problems))
