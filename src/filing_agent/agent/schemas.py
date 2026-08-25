"""The output contract (PROPOSAL.md §3). Pydantic at every boundary, never freeform.

The schema is where the project's central promise is enforced. §3 says a numeric claim
that fails XBRL verification is corrected or emitted with `verified: false` — *never
silently*. So the model makes the silent case unrepresentable: a claim carrying a number
cannot be constructed without a verification record, and a verification claiming the
`xbrl` method cannot be constructed without the concept and period it was checked against.

An LLM cannot be trusted to remember a rule like that on every generation. A validator
can.
"""

from __future__ import annotations

import datetime as dt
from typing import Final, Literal

from pydantic import BaseModel, Field, model_validator

VerificationMethod = Literal["xbrl", "extractive", "judged", "unverified"]

# Methods that constitute programmatic verification against ground truth. `judged` is an
# LLM opinion and `extractive` is a text match; neither is proof of a number.
PROGRAMMATIC_METHODS: Final[frozenset[str]] = frozenset({"xbrl"})


class Citation(BaseModel):
    """Where a claim came from. Every field is needed to resolve it back to source."""

    accession_no: str = Field(min_length=1)
    item_section: str = Field(min_length=1)
    chunk_id: str = Field(min_length=1)
    # The exact span supporting the claim — what the faithfulness checker (T3.5) tests.
    quote_span: str = Field(min_length=1)

    @model_validator(mode="after")
    def chunk_belongs_to_filing(self) -> Citation:
        """chunk_id is '<accession>:<index>', so a mismatch means a fabricated citation."""
        if not self.chunk_id.startswith(f"{self.accession_no}:"):
            raise ValueError(
                f"chunk_id {self.chunk_id!r} does not belong to filing {self.accession_no!r}"
            )
        return self


class Verification(BaseModel):
    """The outcome of checking a claim. Absence of proof is recorded, not omitted."""

    method: VerificationMethod
    verified: bool
    # Populated for method="xbrl": what was compared, and to what.
    concept: str | None = None
    period_end: dt.date | None = None
    expected_value: float | None = None
    detail: str | None = None

    @model_validator(mode="after")
    def xbrl_verification_names_what_it_checked(self) -> Verification:
        if self.method == "xbrl" and (self.concept is None or self.period_end is None):
            raise ValueError(
                "method='xbrl' requires concept and period_end — an XBRL check that "
                "cannot say what it compared is not a check"
            )
        if self.method == "unverified" and self.verified:
            raise ValueError("method='unverified' cannot report verified=True")
        return self


class Claim(BaseModel):
    """One assertion in the memo. Numeric claims carry stricter obligations than prose."""

    text: str = Field(min_length=1)
    value: float | None = None
    unit: str | None = None
    period: str | None = None
    citation: Citation
    verification: Verification

    @property
    def is_numeric(self) -> bool:
        return self.value is not None

    @model_validator(mode="after")
    def numeric_claims_are_fully_specified(self) -> Claim:
        """A bare number is not a claim: it needs a unit and a period to be checkable."""
        if self.is_numeric:
            if not self.unit:
                raise ValueError(f"numeric claim {self.text!r} has no unit")
            if not self.period:
                raise ValueError(f"numeric claim {self.text!r} has no period")
            if self.verification.method == "judged":
                raise ValueError(
                    f"numeric claim {self.text!r} cannot rest on an LLM judgement; "
                    "use method='xbrl', or 'unverified' if no fact was available"
                )
        elif self.value is None and self.unit:
            raise ValueError(f"claim {self.text!r} has a unit but no value")
        return self


class Memo(BaseModel):
    """The agent's answer. PROPOSAL.md §3's output shape."""

    answer_summary: str = Field(min_length=1)
    claims: list[Claim] = Field(default_factory=list)
    confidence_notes: str = ""
    trace_id: str = Field(min_length=1)

    @property
    def numeric_claims(self) -> list[Claim]:
        return [c for c in self.claims if c.is_numeric]

    @property
    def unverified_numeric_claims(self) -> list[Claim]:
        return [c for c in self.numeric_claims if not c.verification.verified]

    @property
    def verified_fraction(self) -> float:
        """Share of numeric claims that passed programmatic verification."""
        numeric = self.numeric_claims
        if not numeric:
            return 1.0
        passed = sum(
            1 for c in numeric
            if c.verification.verified and c.verification.method in PROGRAMMATIC_METHODS
        )
        return passed / len(numeric)

    @model_validator(mode="after")
    def unverified_numbers_must_be_disclosed(self) -> Memo:
        """§3's hard rule: never silent.

        If any numeric claim is unverified, `confidence_notes` must say so. The memo
        cannot present an unchecked figure with no caveat anywhere in the document.
        """
        if self.unverified_numeric_claims and not self.confidence_notes.strip():
            raise ValueError(
                f"{len(self.unverified_numeric_claims)} unverified numeric claim(s) "
                "but confidence_notes is empty — PROPOSAL.md §3 forbids emitting an "
                "unverified figure silently"
            )
        return self
