"""m07 — analysis. All arithmetic in the system happens here.

Responsibility
    Compute growth rates, margins, ratios and bridges from extracted facts,
    using pandas and numpy. The LLM never performs arithmetic; it receives
    what this module produces and writes prose about it.

Constraints
    - Pure functions only. No I/O, no globals, no side effects.
    - Every derived value carries its formula so the interface can label it
      "calculated" and show how it was reached.
    - Unit tests are required for this module.

Public interface
    compute_derived_metrics(facts) -> list[Fact]
"""

from __future__ import annotations

from app.models import Fact


def compute_derived_metrics(facts: list[Fact]) -> list[Fact]:
    """Derives metrics from reported facts. Implemented in phase 1.

    Pure: the input list is not mutated and nothing outside is touched.
    """
    raise NotImplementedError
