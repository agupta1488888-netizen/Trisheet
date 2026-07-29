"""m10 — prose generation.

Responsibility
    Hand the LLM a set of already-computed facts and ask it to write the
    narrative around them.

Constraints
    - The model receives figures. It never computes, recalls or estimates one.
    - It may only reference facts present in the payload it is given.
    - Output is prose; every figure in that prose must map back to a fact id,
      which is what makes m11's verification possible.

Public interface
    write_sections(company, facts) -> dict[str, str]
"""

from __future__ import annotations

from app.models import Company, Fact


async def write_sections(company: Company, facts: list[Fact]) -> dict[str, str]:
    """Generates prose per section. Implemented in phase 1."""
    raise NotImplementedError
