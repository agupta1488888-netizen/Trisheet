"""m06 — fact persistence and provenance enforcement.

Responsibility
    The write gate. Every fact in the system passes through here, and this is
    where provenance is enforced in code:

    - A fact missing tier, source_type, source_url, accession_no or filed_date
      is discarded, with the rejection logged. It is never stored blank.
    - A Tier 3 or Tier 4 fact offered for a section 3 metric is hard-blocked
      and the rejection is logged.

Why this accepts raw mappings
    A `Fact` cannot be constructed without provenance, so a gate that only
    accepted `Fact` objects would never reject anything — the wall would be
    somewhere else, in whichever module built the object. Upstream modules that
    assemble candidate facts from loose data (m04's narrative parsing, m10's
    prose) hand their output here as mappings, and this is where they are
    refused. That is what makes this the gate rather than a formality.

Public interface
    store_facts(report_id, facts) -> StoreResult
    load_facts(report_id) -> list[Fact]
    load_facts_by_section(report_id, section) -> list[Fact]
    load_facts_by_metric(report_id, metric) -> list[Fact]
    load_facts_by_period(report_id, ...) -> list[Fact]
"""

from __future__ import annotations

import datetime as dt
import logging
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, ValidationError

from app.config import (
    FACT_CONFLICT_COLUMNS,
    FACT_INSERT_BATCH_SIZE,
    SECTION_3_ALLOWED_TIERS,
    SECTION_METRIC_PREFIXES,
    SOURCE_TYPE_TIERS,
)
from app.models import Fact
from app.services import db

if TYPE_CHECKING:
    from supabase import Client

logger = logging.getLogger(__name__)

FACTS_TABLE = "facts"

#: The section whose tier rule is enforced here. Section 3 is the financial
#: highlights table, where a market-data figure must never appear.
FINANCIAL_HIGHLIGHTS_SECTION = 3

#: Provenance fields with no acceptable empty value.
REQUIRED_PROVENANCE_FIELDS = (
    "tier",
    "source_type",
    "source_url",
    "accession_no",
    "filed_date",
)

#: Columns selected when reading facts back. Explicit so a schema addition
#: cannot silently start arriving as an unexpected key.
_FACT_COLUMNS = (
    "metric,label,value,display_value,unit,"
    "period_start,period_end,fiscal_year,"
    "segment_axis,segment_member,segment_label,"
    "tier,source_type,source_url,accession_no,filed_date,"
    "extraction_method,confidence,resolved_tag,taxonomy,is_calculated,formula"
)


class RejectionCode:
    """Why a fact was refused. Stable strings — these are logged and counted."""

    MISSING_PROVENANCE = "missing_provenance"
    TIER_MISMATCH = "tier_mismatch"
    SECTION_TIER_BLOCKED = "section_tier_blocked"
    MALFORMED = "malformed"


class FactRejection(BaseModel):
    """One refused fact, with enough detail to fix whatever produced it."""

    model_config = ConfigDict(frozen=True)

    code: str
    metric: str | None
    reason: str


class StoreResult(BaseModel):
    """Outcome of a write. Rejections are reported, never silently swallowed."""

    model_config = ConfigDict(frozen=True)

    stored: int
    rejected: int
    rejections: tuple[FactRejection, ...] = ()
    #: The facts that passed the gate. Returned so a caller can carry on with
    #: exactly what was admitted rather than with what it offered — running the
    #: gate a second time to find out would let the two answers drift.
    accepted: tuple[Fact, ...] = ()
    #: False when the facts passed the gate but there was nowhere to write
    #: them. Reported rather than implied: "admitted 340 facts" and "persisted
    #: 340 facts" are different claims and the caller must be able to tell
    #: which one it is entitled to make.
    persisted: bool = True

    @property
    def rejection_reasons(self) -> list[str]:
        """Flat reasons, for logging and for the run report."""
        return [rejection.reason for rejection in self.rejections]


class FactStoreError(Exception):
    """A write or read could not be completed."""


# --- Write path -------------------------------------------------------------


def admit_facts(
    report_id: str, facts: Sequence[Fact | Mapping[str, Any]]
) -> tuple[list[Fact], list[FactRejection]]:
    """Runs the provenance gate over candidates, without touching the database.

    This is the gate itself, separated from the write so that enforcement does
    not depend on persistence being available. A report generated with no
    database configured is still a report in which every fact carried full
    provenance and every Tier 3 figure was refused entry to section 3 — the
    difference is only that nothing was written down.

    Returns:
        The facts that may be used, and the rejections explaining the rest.
        Every rejection is logged here, so a caller that ignores the second
        element still leaves a trail.
    """
    accepted: list[Fact] = []
    rejections: list[FactRejection] = []

    for candidate in facts:
        fact, rejection = _admit(candidate)
        if rejection is not None:
            rejections.append(rejection)
            _log_rejection(report_id, rejection)
            continue
        if fact is not None:
            accepted.append(fact)

    return accepted, rejections


async def store_facts(
    report_id: str, facts: Sequence[Fact | Mapping[str, Any]]
) -> StoreResult:
    """Persists facts that carry full provenance, refusing those that do not.

    Args:
        report_id: The report these facts belong to.
        facts: Facts, or mappings to be validated into facts. Anything that
            fails validation is rejected and logged rather than raising, so one
            malformed candidate cannot take down an otherwise sound report.

    Returns:
        A `StoreResult` counting what was admitted and detailing what was not.
        `persisted` is False when the gate ran but no database was configured —
        the facts are usable, they were simply not written down.

    Raises:
        FactStoreError: the database is configured and refused the write.
            Rejected facts are not an error; a database that will not accept
            the accepted ones is.
    """
    accepted, rejections = admit_facts(report_id, facts)

    persisted = db.is_configured()
    if accepted and persisted:
        _write(report_id, accepted)

    logger.info(
        "Fact store write complete",
        extra={
            "report_id": report_id,
            "stored": len(accepted),
            "rejected": len(rejections),
            "persisted": persisted,
        },
    )
    return StoreResult(
        stored=len(accepted),
        rejected=len(rejections),
        rejections=tuple(rejections),
        accepted=tuple(accepted),
        persisted=persisted,
    )


def _admit(candidate: object) -> tuple[Fact | None, FactRejection | None]:
    """Validates one candidate against every provenance rule.

    Takes `object` rather than the declared input type on purpose: candidates
    reaching this gate may have come from JSON or from an LLM, where the type
    annotation upstream is a statement of intent rather than a guarantee. The
    gate checks what actually arrived.

    Returns the fact to store, or the rejection explaining why there is none.
    """
    fact, rejection = _coerce(candidate)
    if rejection is not None:
        return None, rejection
    if fact is None:
        return None, None

    blank = _blank_provenance_fields(fact)
    if blank:
        return None, FactRejection(
            code=RejectionCode.MISSING_PROVENANCE,
            metric=fact.metric,
            reason=(
                f"{fact.metric}: provenance field(s) {', '.join(blank)} are "
                "blank. A fact that cannot name its source is not stored."
            ),
        )

    expected_tier = SOURCE_TYPE_TIERS.get(str(fact.source_type))
    if expected_tier is not None and int(fact.tier) != expected_tier:
        return None, FactRejection(
            code=RejectionCode.TIER_MISMATCH,
            metric=fact.metric,
            reason=(
                f"{fact.metric}: source type {fact.source_type} is tier "
                f"{expected_tier}, but the fact claims tier {int(fact.tier)}."
            ),
        )

    section = section_for_metric(fact.metric)
    if (
        section == FINANCIAL_HIGHLIGHTS_SECTION
        and int(fact.tier) not in SECTION_3_ALLOWED_TIERS
    ):
        return None, FactRejection(
            code=RejectionCode.SECTION_TIER_BLOCKED,
            metric=fact.metric,
            reason=(
                f"{fact.metric}: tier {int(fact.tier)} is blocked from section "
                f"{FINANCIAL_HIGHLIGHTS_SECTION}, which accepts tiers "
                f"{', '.join(str(t) for t in SECTION_3_ALLOWED_TIERS)} only."
            ),
        )

    return fact, None


def _coerce(candidate: object) -> tuple[Fact | None, FactRejection | None]:
    """Turns a candidate into a Fact, or explains why it is not one."""
    if isinstance(candidate, Fact):
        return candidate, None

    if not isinstance(candidate, Mapping):
        return None, FactRejection(
            code=RejectionCode.MALFORMED,
            metric=None,
            reason=f"Not a fact: {type(candidate).__name__}.",
        )

    metric = candidate.get("metric")
    metric_name = metric if isinstance(metric, str) else None

    missing = [
        field for field in REQUIRED_PROVENANCE_FIELDS if candidate.get(field) is None
    ]
    if missing:
        return None, FactRejection(
            code=RejectionCode.MISSING_PROVENANCE,
            metric=metric_name,
            reason=(
                f"{metric_name or 'unnamed fact'}: missing provenance field(s) "
                f"{', '.join(missing)}."
            ),
        )

    try:
        return Fact.model_validate(dict(candidate)), None
    except ValidationError as cause:
        return None, FactRejection(
            code=RejectionCode.MALFORMED,
            metric=metric_name,
            reason=(
                f"{metric_name or 'unnamed fact'}: {_first_error(cause)}"
            ),
        )


def _first_error(error: ValidationError) -> str:
    """The first validation failure, phrased for a log line."""
    errors = error.errors()
    if not errors:
        return "failed validation."
    first = errors[0]
    location = ".".join(str(part) for part in first.get("loc", ())) or "fact"
    return f"{location}: {first.get('msg', 'is invalid')}."


def _blank_provenance_fields(fact: Fact) -> list[str]:
    """Provenance fields present on the model but empty in substance.

    The model already refuses a missing field. This catches the whitespace
    string that satisfies the type but names no source.
    """
    blank: list[str] = []
    if not fact.accession_no.strip():
        blank.append("accession_no")
    if not str(fact.source_url).strip():
        blank.append("source_url")
    return blank


def _log_rejection(report_id: str, rejection: FactRejection) -> None:
    """Records a refusal. Every rejection is logged; none is swallowed."""
    logger.warning(
        "Fact rejected at write time",
        extra={
            "report_id": report_id,
            "metric": rejection.metric,
            "rejection_code": rejection.code,
            "reason": rejection.reason,
        },
    )


def _write(report_id: str, facts: Sequence[Fact]) -> None:
    """Upserts accepted facts in batches.

    Upsert rather than insert because m03 may legitimately re-extract a metric
    for a period already stored — a re-run of the same report must converge,
    not collide with its own earlier write.
    """
    client = _client()
    conflict = ",".join(FACT_CONFLICT_COLUMNS)

    for start in range(0, len(facts), FACT_INSERT_BATCH_SIZE):
        batch = facts[start : start + FACT_INSERT_BATCH_SIZE]
        rows = [_to_row(report_id, fact) for fact in batch]
        try:
            client.table(FACTS_TABLE).upsert(rows, on_conflict=conflict).execute()
        except Exception as cause:  # noqa: BLE001 — surfaced as a typed failure
            logger.error(
                "Fact batch could not be written",
                extra={
                    "report_id": report_id,
                    "batch_start": start,
                    "batch_size": len(rows),
                },
                exc_info=cause,
            )
            message = f"Could not store facts for report {report_id}: {cause}"
            raise FactStoreError(message) from cause


def _to_row(report_id: str, fact: Fact) -> dict[str, Any]:
    """Maps a Fact onto its database row."""
    return {
        "report_id": report_id,
        "metric": fact.metric,
        "label": fact.label,
        "value": fact.value,
        "display_value": fact.display_value,
        "unit": fact.unit,
        "period_start": _date_str(fact.period_start),
        "period_end": _date_str(fact.period_end),
        "fiscal_year": fact.fiscal_year,
        "segment_axis": fact.segment_axis,
        "segment_member": fact.segment_member,
        "segment_label": fact.segment_label,
        "tier": int(fact.tier),
        "source_type": str(fact.source_type),
        "source_url": str(fact.source_url),
        "accession_no": fact.accession_no,
        "filed_date": _date_str(fact.filed_date),
        "extraction_method": str(fact.extraction_method),
        "confidence": fact.confidence,
        "resolved_tag": fact.resolved_tag,
        "taxonomy": str(fact.taxonomy) if fact.taxonomy is not None else None,
        "is_calculated": fact.is_calculated,
        "formula": fact.formula,
    }


def _date_str(value: dt.date | None) -> str | None:
    return value.isoformat() if value is not None else None


# --- Read path --------------------------------------------------------------


async def load_facts(report_id: str) -> list[Fact]:
    """Loads every stored fact for a report, newest period first."""
    return _query(report_id)


async def load_facts_by_section(report_id: str, section: int) -> list[Fact]:
    """Loads the facts belonging to one report section.

    Section membership is decided by metric prefix, so a metric that belongs to
    no section returns nothing rather than leaking into every section.
    """
    prefixes = SECTION_METRIC_PREFIXES.get(section, ())
    if not prefixes:
        logger.warning(
            "No metrics are mapped to this section",
            extra={"report_id": report_id, "section": section},
        )
        return []

    facts = _query(report_id)
    return [
        fact
        for fact in facts
        if any(fact.metric.startswith(prefix) for prefix in prefixes)
    ]


async def load_facts_by_metric(report_id: str, metric: str) -> list[Fact]:
    """Loads every period stored for one metric, newest first."""
    return _query(report_id, metric=metric)


async def load_facts_by_period(
    report_id: str,
    *,
    fiscal_year: int | None = None,
    period_end: dt.date | None = None,
) -> list[Fact]:
    """Loads facts for one period, addressed by fiscal year or by end date.

    Raises:
        ValueError: neither selector was given. Returning every fact for an
            unspecified period would be a silently wrong answer.
    """
    if fiscal_year is None and period_end is None:
        message = "Specify fiscal_year or period_end to select a period."
        raise ValueError(message)
    return _query(report_id, fiscal_year=fiscal_year, period_end=period_end)


def _query(
    report_id: str,
    *,
    metric: str | None = None,
    fiscal_year: int | None = None,
    period_end: dt.date | None = None,
) -> list[Fact]:
    """Runs one filtered read and rebuilds Facts from the rows."""
    client = _client()

    try:
        query = (
            client.table(FACTS_TABLE)
            .select(_FACT_COLUMNS)
            .eq("report_id", report_id)
        )
        if metric is not None:
            query = query.eq("metric", metric)
        if fiscal_year is not None:
            query = query.eq("fiscal_year", fiscal_year)
        if period_end is not None:
            query = query.eq("period_end", period_end.isoformat())
        response = query.order("period_end", desc=True).execute()
    except Exception as cause:  # noqa: BLE001 — surfaced as a typed failure
        logger.error(
            "Facts could not be read",
            extra={"report_id": report_id, "metric": metric},
            exc_info=cause,
        )
        message = f"Could not read facts for report {report_id}: {cause}"
        raise FactStoreError(message) from cause

    rows = getattr(response, "data", None) or []
    return _rows_to_facts(report_id, rows)


def _rows_to_facts(report_id: str, rows: Sequence[Any]) -> list[Fact]:
    """Rebuilds Facts from rows, skipping any that no longer validate.

    A stored row that will not rebuild is a schema drift, not a fact. It is
    logged and dropped rather than crashing a read of the other twenty.
    """
    facts: list[Fact] = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        try:
            facts.append(Fact.model_validate(dict(row)))
        except ValidationError as cause:
            logger.warning(
                "Stored row could not be read back as a fact",
                extra={
                    "report_id": report_id,
                    "metric": row.get("metric"),
                    "reason": _first_error(cause),
                },
            )
    return facts


def section_for_metric(metric: str) -> int | None:
    """The section a metric belongs to, or None when it belongs to none.

    Public because m11 applies the same rule when it verifies prose, and the
    two must not be able to disagree about what section 3 contains.
    """
    for section, prefixes in SECTION_METRIC_PREFIXES.items():
        if any(metric.startswith(prefix) for prefix in prefixes):
            return section
    return None


def _client() -> Client:
    """The Supabase client, with persistence failures typed for the caller."""
    try:
        return db.get_client()
    except db.DatabaseError as cause:
        raise FactStoreError(str(cause)) from cause
