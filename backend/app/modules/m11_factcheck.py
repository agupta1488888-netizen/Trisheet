"""m11 — verification gate. Blocking.

Responsibility
    Verify generated prose against the fact store before anything is assembled
    or shown. A report that fails this gate does not render.

Checks
    - Every figure appearing in prose matches a stored fact exactly.
    - No figure appears that has no corresponding fact.
    - Section 3 contains no Tier 3 or Tier 4 sourced figure.
    - Segment figures sum to the reported total within tolerance.

Constraints
    Unit tests are required for this module.

Public interface
    verify(sections, facts) -> VerificationResult
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from app.models import Fact

#: Segment figures may miss the reported total by at most this fraction before
#: the report is flagged. Rounding in filings is the reason this is not zero.
SEGMENT_SUM_TOLERANCE = 0.005


class Violation(BaseModel):
    model_config = ConfigDict(frozen=True)

    check: str
    section: str
    message: str


class VerificationResult(BaseModel):
    """A report is assembled only when `passed` is True."""

    model_config = ConfigDict(frozen=True)

    passed: bool
    violations: list[Violation]


def verify(sections: dict[str, str], facts: list[Fact]) -> VerificationResult:
    """Verifies prose against stored facts. Implemented in phase 1."""
    raise NotImplementedError
