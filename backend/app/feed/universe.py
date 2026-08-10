"""The set of companies the feed tracks.

Responsibility
    Answer one question, cheaply and often: is this CIK one we care about?
    Both polling lanes read the whole of EDGAR and discard almost all of it,
    so this lookup runs against thousands of filings per cycle and must be a
    set membership test, not a query.

Two sources, merged
    The seed file — S&P 500 constituents, checked in beside this module. It is
    a static list because there is no free authoritative constituent API, and a
    scraper that silently rots is worse than a file with a date on it. A stale
    seed costs nothing structural: the feed tracks last quarter's index until
    someone refreshes it.

    The `companies` table — every filer a user has ever run a report on. This
    is what stops the feed being a fixed list. Report a small-cap and it joins
    the feed permanently, so the surface grows towards what this deployment's
    readers actually look at.

Degradation
    The database half is optional. If Supabase is unreachable the universe is
    the seed alone, which is 500 companies and a perfectly good feed. The seed
    half is not optional — a missing or malformed seed is a packaging error and
    raises at import, because a feed tracking nothing would otherwise look like
    a feed that is merely quiet.

Public interface
    load(refresh) -> Universe
    Universe.contains(cik) / .entry(cik) / .ciks / .size
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from app.services import db
from app.services.edgar import pad_cik

logger = logging.getLogger(__name__)

#: Anchored to this module rather than the working directory, for the same
#: reason config.py anchors the .env path: a relative path only resolves when
#: the process happens to have been started from the right directory.
_SEED_PATH: Final[Path] = Path(__file__).resolve().parent / "universe_seed.json"

COMPANIES_TABLE = "companies"


class UniverseSeedError(RuntimeError):
    """The checked-in seed is missing or malformed. A packaging error."""


@dataclass(frozen=True, slots=True)
class UniverseEntry:
    """One tracked filer. Ticker and name are display only; CIK is the key."""

    cik: str
    ticker: str | None
    name: str


@dataclass(frozen=True, slots=True)
class Universe:
    """An immutable snapshot of the tracked filers.

    Frozen because the poller holds one across a whole cycle and filters
    thousands of filings through it. A set that could change underneath that
    loop would make a cycle's output depend on its timing.
    """

    entries: dict[str, UniverseEntry]

    @property
    def ciks(self) -> frozenset[str]:
        return frozenset(self.entries)

    @property
    def size(self) -> int:
        return len(self.entries)

    def contains(self, cik: str) -> bool:
        """True when a padded or unpadded CIK is tracked."""
        return pad_cik(cik) in self.entries

    def entry(self, cik: str) -> UniverseEntry | None:
        """The tracked filer for a CIK, or None when it is not tracked."""
        return self.entries.get(pad_cik(cik))


def _read_seed() -> dict[str, UniverseEntry]:
    """Loads the checked-in constituent list.

    Raises:
        UniverseSeedError: the file is absent, unparseable, or carries no
            companies — each of which means the package was built wrong.
    """
    try:
        raw = _SEED_PATH.read_text(encoding="utf-8")
    except OSError as cause:
        message = f"Feed universe seed is missing at {_SEED_PATH}"
        raise UniverseSeedError(message) from cause

    try:
        document: Any = json.loads(raw)
        companies = document["companies"]
    except (json.JSONDecodeError, KeyError, TypeError) as cause:
        message = f"Feed universe seed at {_SEED_PATH} is malformed"
        raise UniverseSeedError(message) from cause

    entries: dict[str, UniverseEntry] = {}
    for company in companies:
        try:
            cik = pad_cik(str(company["cik"]))
            name = str(company["name"])
        except (KeyError, TypeError, ValueError):
            # One bad row is not worth failing 500 good ones over.
            logger.warning("Skipping malformed seed entry", extra={"entry": company})
            continue
        ticker = company.get("ticker")
        entries[cik] = UniverseEntry(
            cik=cik,
            ticker=str(ticker) if ticker else None,
            name=name,
        )

    if not entries:
        message = f"Feed universe seed at {_SEED_PATH} lists no usable companies"
        raise UniverseSeedError(message)

    return entries


def _read_reported_companies() -> dict[str, UniverseEntry]:
    """Every filer a report has been run on, from the `companies` table.

    Returns an empty mapping rather than raising when the database is absent
    or refuses: the seed alone is a working universe, and a feed that stopped
    because a supplementary source was down would be a worse failure than one
    that tracks 500 companies instead of 512.
    """
    if not db.is_configured():
        return {}

    try:
        client = db.get_client()
        response = client.table(COMPANIES_TABLE).select("cik,ticker,name").execute()
    except db.DatabaseError:
        logger.warning("Could not read reported companies for the feed universe")
        return {}
    except Exception:  # noqa: BLE001 — supabase-py raises untyped transport errors
        logger.warning(
            "Could not read reported companies for the feed universe",
            exc_info=True,
        )
        return {}

    entries: dict[str, UniverseEntry] = {}
    for row in getattr(response, "data", None) or []:
        cik = row.get("cik")
        name = row.get("name")
        if not cik or not name:
            continue
        padded = pad_cik(str(cik))
        ticker = row.get("ticker")
        entries[padded] = UniverseEntry(
            cik=padded,
            ticker=str(ticker) if ticker else None,
            name=str(name),
        )
    return entries


#: Cached across cycles. Rebuilding this means a round trip to Supabase, and
#: the answer changes only when someone runs a report on a new company.
_cached: Universe | None = None


def load(*, refresh: bool = False) -> Universe:
    """The tracked universe, built on first use and cached after.

    The seed is the base and the database layers over it, so a company that
    appears in both is described by the row a report actually resolved — which
    carries the name and ticker EDGAR itself returned, rather than the seed's.

    Raises:
        UniverseSeedError: the checked-in seed could not be read.
    """
    global _cached  # noqa: PLW0603 — one process-wide snapshot by design
    if _cached is not None and not refresh:
        return _cached

    entries = _read_seed()
    seed_size = len(entries)
    entries.update(_read_reported_companies())

    _cached = Universe(entries=entries)
    logger.info(
        "Feed universe loaded",
        extra={
            "total": len(entries),
            "from_seed": seed_size,
            "from_reports": len(entries) - seed_size,
        },
    )
    return _cached


def reset() -> None:
    """Drops the cached universe. Used by tests and on configuration reload."""
    global _cached  # noqa: PLW0603 — mirrors load
    _cached = None
