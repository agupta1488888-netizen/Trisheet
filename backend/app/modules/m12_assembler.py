"""m12 — output assembly.

Responsibility
    Render a verified report to its distributable forms: PDF and XLSX. Source
    references travel with the output — the provenance rail is not a screen-only
    feature.

Public interface
    assemble_pdf(report_id) -> bytes
    assemble_xlsx(report_id) -> bytes
"""

from __future__ import annotations


async def assemble_pdf(report_id: str) -> bytes:
    """Renders the report as PDF. Implemented in phase 1."""
    raise NotImplementedError


async def assemble_xlsx(report_id: str) -> bytes:
    """Renders the underlying figures as XLSX. Implemented in phase 1."""
    raise NotImplementedError
