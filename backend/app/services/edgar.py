"""Rate-limited SEC EDGAR client.

Every request to sec.gov and data.sec.gov goes through this module. Nothing
else in the codebase constructs an EDGAR URL or issues an EDGAR request.

Requirements, all enforced here:
    - Send `User-Agent: Tearsheet <contact-email>`; SEC returns 403 without it.
    - Respect a global ceiling of 10 requests/second across all workers, via a
      shared token bucket rather than a per-process sleep.
    - Retry 429 with exponential backoff, at most 3 attempts, honouring
      Retry-After when present.
    - Cache responses permanently — filings are immutable.

Public interface
    pad_cik(cik) -> str
    get_json(url) -> dict
"""

from __future__ import annotations

from typing import Any

from app.config import CIK_PAD_WIDTH


def pad_cik(cik: str | int) -> str:
    """Zero-pads a CIK to the 10 digits every EDGAR URL requires."""
    return str(cik).lstrip("0").zfill(CIK_PAD_WIDTH)


async def get_json(url: str) -> dict[str, Any]:
    """Fetches and caches a JSON document from EDGAR. Implemented in phase 1."""
    raise NotImplementedError
