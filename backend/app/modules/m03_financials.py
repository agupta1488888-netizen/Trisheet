"""m03 — XBRL extraction.

Responsibility
    Pull reported figures out of the XBRL company facts API and emit `Fact`
    objects carrying full provenance and the tag that actually resolved.

Rules
    - Never hardcode a single tag. Each metric has an ordered fallback list,
      defined in `config.METRIC_SPECS` and nowhere else.
    - Try us-gaap first, fall back to ifrs-full for foreign filers.
    - Deduplicate on (start, end, form); keep the latest filed date.
    - Prefer amendments over originals for the same period.
    - A metric that resolves nowhere produces a NOT_DISCLOSED marker. It is
      never estimated, never interpolated, never left blank.

What the numbers mean
    The `fy` field EDGAR attaches to a company-facts row is the fiscal year of
    the *filing* that reported it, not of the period the row covers: the FY2025
    10-K restates FY2024 revenue with fy=2025. Fiscal years here are therefore
    derived from the filer's own annual reports — see `_build_fiscal_year_index`
    — rather than read off the row, which would mislabel every comparative.

Public interface
    extract_financials(company, manifest) -> list[Fact]
    extract_segments(company, manifest) -> list[Fact]
"""

from __future__ import annotations

import datetime as dt
import logging
import re
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, TypeAlias

from app.config import (
    AMENDMENT_FORM_SUFFIX,
    ANNUAL_PERIOD_MAX_DAYS,
    ANNUAL_PERIOD_MIN_DAYS,
    CONFIDENCE_EXACT,
    CONFIDENCE_FLOOR,
    CONFIDENCE_NOT_DISCLOSED,
    CONFIDENCE_PENALTY_PER_TAG_FALLBACK,
    CONFIDENCE_PENALTY_TAXONOMY_FALLBACK,
    MAX_ANNUAL_PERIODS,
    MAX_INSTANCE_DOCUMENT_BYTES,
    METRIC_SPECS,
    NON_CURRENCY_UNIT_KEYS,
    NOT_DISCLOSED_TEXT,
    SEGMENT_AXES,
    SEGMENT_EXCLUDED_MEMBERS,
    SEGMENT_METRIC,
    SEGMENT_METRIC_LABEL,
    SEGMENT_NEUTRAL_QUALIFIERS,
    SEGMENT_SOURCE_METRIC,
    XBRL_LINKBASE_SUFFIXES,
    XBRL_TAXONOMY_PREFERENCE,
    XBRLDI_NAMESPACE,
    XBRLI_NAMESPACE,
    MetricSpec,
)
from app.models import (
    Company,
    ExtractionMethod,
    Fact,
    FilerType,
    Filing,
    SourceTier,
    SourceType,
    Taxonomy,
)
from app.services import edgar

logger = logging.getLogger(__name__)

#: Taxonomy tried first, by filer type. Everything else follows in the order
#: `XBRL_TAXONOMY_PREFERENCE` gives, so a domestic filer that happens to tag in
#: IFRS still resolves — it just costs a confidence step.
_PREFERRED_TAXONOMY: dict[FilerType, str] = {
    FilerType.DOMESTIC: "us-gaap",
    FilerType.FOREIGN: "ifrs-full",
    FilerType.CANADIAN: "ifrs-full",
}

#: An element of a parsed XBRL instance. lxml ships no type information, so the
#: type checker cannot see past this; the alias at least names what is held.
XmlElement: TypeAlias = Any

#: Splits "us-gaap:StatementBusinessSegmentsAxis" style QNames.
_QNAME_SPLIT = re.compile(r"[:_]")

#: Inserts a space at each lower-to-upper transition so "GreaterChinaSegment"
#: reads "Greater China Segment".
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


# --- Internal value types ---------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Observation:
    """One reported figure, as EDGAR presents it, before any selection."""

    value: float
    unit: str
    period_start: dt.date | None
    period_end: dt.date
    form: str
    filed_date: dt.date
    accession_no: str

    @property
    def base_form(self) -> str:
        """The form with any amendment suffix removed: "10-K/A" -> "10-K"."""
        return self.form.split(AMENDMENT_FORM_SUFFIX, 1)[0]

    @property
    def is_amendment(self) -> bool:
        return AMENDMENT_FORM_SUFFIX in self.form

    @property
    def period_key(self) -> tuple[dt.date | None, dt.date]:
        return (self.period_start, self.period_end)

    @property
    def dedup_key(self) -> tuple[dt.date | None, dt.date, str]:
        """The key CLAUDE.md dedupes on: (start, end, form)."""
        return (self.period_start, self.period_end, self.form)


@dataclass(frozen=True, slots=True)
class _Resolution:
    """The tag that answered for a metric, and what it cost to get there."""

    taxonomy: str
    tag: str
    observations: tuple[_Observation, ...]
    confidence: float


# --- Public interface -------------------------------------------------------


async def extract_financials(
    company: Company, manifest: list[Filing]
) -> list[Fact]:
    """Extracts reported figures as facts.

    Fetches company facts once and resolves every metric in `METRIC_SPECS`
    against it. A metric that no tag answers for yields a NOT_DISCLOSED marker
    so the gap is stated rather than silently dropped.

    Args:
        company: The filer, whose type decides which taxonomy is tried first.
        manifest: Filings from m02, used to point provenance at the exact
            document. An empty manifest is fine — provenance then addresses the
            filing index, which is derivable from the accession number alone.

    Raises:
        edgar.EdgarError: EDGAR is the one hard dependency; if company facts
            cannot be fetched there is nothing to extract and the caller must
            fail the report rather than render an empty one.
    """
    client = edgar.get_client()
    url = edgar.company_facts_url(company.cik)
    payload = await client.get_json(url)

    taxonomies = _taxonomy_order(company.filer_type)
    facts_by_taxonomy = _facts_root(payload)
    filings_by_accession = {filing.accession_no: filing for filing in manifest}
    fiscal_years = _build_fiscal_year_index(facts_by_taxonomy)

    facts: list[Fact] = []
    for spec in METRIC_SPECS:
        resolution = _resolve_metric(
            spec, facts_by_taxonomy, taxonomies, company.reporting_currency
        )

        if resolution is None:
            marker = _not_disclosed_fact(spec, company, manifest)
            if marker is not None:
                facts.append(marker)
            logger.info(
                "Metric not disclosed",
                extra={
                    "cik": company.cik,
                    "metric": spec.metric,
                    "tags_tried": _all_tags(spec, taxonomies),
                    "optional": spec.optional,
                },
            )
            continue

        selected = _select_periods(
            resolution.observations, spec, fiscal_years.keys()
        )
        for observation in selected:
            facts.append(
                _build_fact(
                    spec=spec,
                    company=company,
                    observation=observation,
                    resolution=resolution,
                    fiscal_years=fiscal_years,
                    filings_by_accession=filings_by_accession,
                )
            )

        logger.debug(
            "Metric resolved",
            extra={
                "cik": company.cik,
                "metric": spec.metric,
                "resolved_tag": resolution.tag,
                "taxonomy": resolution.taxonomy,
                "periods": len(selected),
                "confidence": resolution.confidence,
            },
        )

    logger.info(
        "Financial extraction complete",
        extra={
            "cik": company.cik,
            "ticker": company.ticker,
            "facts": len(facts),
            "metrics": len(METRIC_SPECS),
        },
    )
    return facts


async def extract_segments(
    company: Company, manifest: list[Filing]
) -> list[Fact]:
    """Extracts the segment breakdown from the latest annual filing.

    Segment figures are dimensional and the company-facts API carries only
    consolidated ones, so this reads the filing's XBRL instance document
    directly.

    Degrades to an empty list on any failure. A report without its segment
    table is a smaller report; a report that will not generate is not a report.
    Nothing here raises.
    """
    annual = _latest_annual_filing(manifest, company.filer_type)
    if annual is None:
        logger.info(
            "No annual filing in the manifest; segments skipped",
            extra={"cik": company.cik, "manifest_size": len(manifest)},
        )
        return []

    try:
        return await _extract_segments_from_filing(company, annual)
    except Exception as cause:  # noqa: BLE001 — degradation is the whole point
        logger.warning(
            "Segment extraction failed; the report continues without it",
            extra={
                "cik": company.cik,
                "accession_no": annual.accession_no,
                "error": str(cause),
            },
        )
        return []


# --- Metric resolution ------------------------------------------------------


def _taxonomy_order(filer_type: FilerType) -> tuple[str, ...]:
    """Taxonomies to try, most likely first for this filer type."""
    preferred = _PREFERRED_TAXONOMY.get(filer_type, "us-gaap")
    rest = tuple(t for t in XBRL_TAXONOMY_PREFERENCE if t != preferred)
    return (preferred, *rest)


def _facts_root(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    """The `facts` object of a company-facts document, or an empty mapping."""
    facts = payload.get("facts")
    if not isinstance(facts, Mapping):
        return {}
    return facts


def _all_tags(spec: MetricSpec, taxonomies: Sequence[str]) -> list[str]:
    """Every tag that would be tried, for logging a genuine absence."""
    return [
        f"{taxonomy}:{tag}"
        for taxonomy in taxonomies
        for tag in spec.tags_for(taxonomy)
    ]


def _resolve_metric(
    spec: MetricSpec,
    facts_by_taxonomy: Mapping[str, Any],
    taxonomies: Sequence[str],
    reporting_currency: str | None,
) -> _Resolution | None:
    """Walks the fallback ladder and returns the tag that answers for today.

    A tag counts as resolved only when it yields at least one observation of
    the right period type. A tag present but empty, or present with only
    instant facts when a duration is wanted, falls through to the next.

    Ladder order alone is not enough. Filers migrate between tags — NIKE
    reported cost of sales under `CostOfRevenue` until 2011 and under
    `CostOfGoodsAndServicesSold` afterwards, and both remain populated in
    company facts forever. Taking the first tag that resolves would return a
    figure a decade stale, correctly sourced and completely wrong to print.

    So every rung is evaluated, and the winner is the earliest rung that
    reaches the most recent period any rung reaches. Ladder order still decides
    ties, which is what makes it a preference order rather than a search.
    """
    candidates: list[tuple[int, int, str, str, tuple[_Observation, ...]]] = []

    for taxonomy_index, taxonomy in enumerate(taxonomies):
        concepts = facts_by_taxonomy.get(taxonomy)
        if not isinstance(concepts, Mapping):
            continue

        for tag_index, tag in enumerate(spec.tags_for(taxonomy)):
            concept = concepts.get(tag)
            if not isinstance(concept, Mapping):
                continue

            observations = tuple(
                _read_observations(concept, spec, reporting_currency)
            )
            if observations:
                candidates.append(
                    (taxonomy_index, tag_index, taxonomy, tag, observations)
                )

    if not candidates:
        return None

    latest_available = max(
        max(o.period_end for o in observations)
        for _, _, _, _, observations in candidates
    )
    current = [
        candidate
        for candidate in candidates
        if max(o.period_end for o in candidate[4]) == latest_available
    ]

    taxonomy_index, tag_index, taxonomy, tag, observations = min(
        current, key=lambda c: (c[0], c[1])
    )
    return _Resolution(
        taxonomy=taxonomy,
        tag=tag,
        observations=observations,
        confidence=_confidence_for(taxonomy_index, tag_index),
    )


def _confidence_for(taxonomy_index: int, tag_index: int) -> float:
    """Scores how far down the ladder a figure was found.

    An exact hit on the preferred taxonomy's first tag scores 1.0. Every step
    away costs a little, floored so that a resolved tag never looks like a
    guess — it is still an exact figure from a filing, just not the one the
    metric prefers.
    """
    penalty = (
        tag_index * CONFIDENCE_PENALTY_PER_TAG_FALLBACK
        + taxonomy_index * CONFIDENCE_PENALTY_TAXONOMY_FALLBACK
    )
    return max(CONFIDENCE_FLOOR, CONFIDENCE_EXACT - penalty)


def _select_unit(
    units: Mapping[str, Any], reporting_currency: str | None
) -> str | None:
    """Picks the one unit bucket a metric is read from.

    A filer may report the same concept in more than one unit: SAP files
    revenue under both EUR and USD. Reading every bucket would put two
    currencies in one column and let deduplication pick between them by
    accident of ordering, which is how a report ends up silently denominated in
    the wrong money.

    The filer's own reporting currency decides when it is known. Otherwise the
    bucket reaching the most recent period wins, then the fuller one, then the
    alphabetically first — so the choice is deterministic rather than lucky.
    """
    populated = sorted(
        unit
        for unit, rows in units.items()
        if isinstance(rows, list) and rows
    )
    if not populated:
        return None
    if reporting_currency is not None and reporting_currency in populated:
        return reporting_currency
    if len(populated) == 1:
        return populated[0]

    def rank(unit: str) -> tuple[str, int]:
        rows = units[unit]
        latest = max(
            (
                str(row.get("end", ""))
                for row in rows
                if isinstance(row, Mapping)
            ),
            default="",
        )
        return (latest, len(rows))

    # max returns the first maximal element, and `populated` is sorted, so ties
    # resolve alphabetically.
    return max(populated, key=rank)


def _read_observations(
    concept: Mapping[str, Any],
    spec: MetricSpec,
    reporting_currency: str | None,
) -> Iterable[_Observation]:
    """Turns one concept's chosen unit bucket into observations."""
    units = concept.get("units")
    if not isinstance(units, Mapping):
        return

    unit = _select_unit(units, reporting_currency)
    if unit is None:
        return

    rows = units[unit]
    if isinstance(rows, list):
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            observation = _read_observation(row, unit, spec)
            if observation is not None:
                yield observation


def _read_observation(
    row: Mapping[str, Any], unit: str, spec: MetricSpec
) -> _Observation | None:
    """Parses one company-facts row, or None when it is not usable.

    Rows are skipped rather than repaired: a row missing an end date, a value
    or an accession number cannot carry provenance, and a fact without
    provenance does not exist.
    """
    value = row.get("val")
    if not isinstance(value, int | float) or isinstance(value, bool):
        return None

    period_end = _parse_date(row.get("end"))
    if period_end is None:
        return None

    period_start = _parse_date(row.get("start"))
    if spec.period_type == "duration":
        if period_start is None:
            return None
        if not _is_annual(period_start, period_end):
            return None
    elif period_start is not None:
        # An instant metric reported against a duration is a different concept.
        return None

    filed_date = _parse_date(row.get("filed"))
    accession_no = row.get("accn")
    form = row.get("form")
    if (
        filed_date is None
        or not isinstance(accession_no, str)
        or not accession_no.strip()
        or not isinstance(form, str)
        or not form.strip()
    ):
        return None

    return _Observation(
        value=float(value),
        unit=unit,
        period_start=period_start,
        period_end=period_end,
        form=form,
        filed_date=filed_date,
        accession_no=accession_no,
    )


def _parse_date(raw: object) -> dt.date | None:
    if not isinstance(raw, str):
        return None
    try:
        return dt.date.fromisoformat(raw)
    except ValueError:
        return None


def _is_annual(start: dt.date, end: dt.date) -> bool:
    """True for a period the length of a fiscal year.

    A 52/53-week calendar makes "a year" a range, not a number, which is why
    this is a band and not an equality.
    """
    days = (end - start).days
    return ANNUAL_PERIOD_MIN_DAYS <= days <= ANNUAL_PERIOD_MAX_DAYS


# --- Deduplication and period selection -------------------------------------


def _select_periods(
    observations: Sequence[_Observation],
    spec: MetricSpec,
    fiscal_year_ends: Collection[dt.date],
) -> list[_Observation]:
    """Reduces every reported instance of a metric to one figure per period.

    Three passes, the first two in the order CLAUDE.md specifies:

    1. Dedupe on (start, end, form), keeping the latest filed date. The same
       period is re-reported as a comparative in each subsequent annual report;
       the most recently filed instance is the filer's current word on it.
    2. Collapse forms for the same period, preferring an amendment over the
       original it amends, then the later filing date. A 10-K/A exists
       precisely because the 10-K was wrong.
    3. For instant metrics, keep only balance sheet dates that fall on a fiscal
       year end. A duration is filtered to annual lengths when it is read, but
       an instant has no length to filter on, so a 10-Q balance sheet date
       would otherwise sit in the annual column looking like a year end.

    The result is the most recent `MAX_ANNUAL_PERIODS` periods, newest first.
    """
    by_dedup_key: dict[tuple[dt.date | None, dt.date, str], _Observation] = {}
    for observation in observations:
        incumbent = by_dedup_key.get(observation.dedup_key)
        if incumbent is None or observation.filed_date > incumbent.filed_date:
            by_dedup_key[observation.dedup_key] = observation

    by_period: dict[tuple[dt.date | None, dt.date], _Observation] = {}
    for observation in by_dedup_key.values():
        incumbent = by_period.get(observation.period_key)
        if incumbent is None or _supersedes(observation, incumbent):
            by_period[observation.period_key] = observation

    selected = list(by_period.values())
    if spec.period_type == "instant" and fiscal_year_ends:
        # Only filter when the filer's own year ends are known. An unknown
        # calendar is a reason to show every date, not to drop them all.
        selected = [
            observation
            for observation in selected
            if observation.period_end in fiscal_year_ends
        ]

    ordered = sorted(selected, key=lambda o: o.period_end, reverse=True)
    return ordered[:MAX_ANNUAL_PERIODS]


def _supersedes(candidate: _Observation, incumbent: _Observation) -> bool:
    """True when `candidate` is the better authority for the same period.

    An amendment of the same base form always wins, whenever it was filed —
    that is what an amendment is. Otherwise the later filing wins.
    """
    if candidate.base_form == incumbent.base_form:
        if candidate.is_amendment != incumbent.is_amendment:
            return candidate.is_amendment
        return candidate.filed_date > incumbent.filed_date
    return candidate.filed_date > incumbent.filed_date


# --- Fiscal year labelling --------------------------------------------------


def _build_fiscal_year_index(
    facts_by_taxonomy: Mapping[str, Any],
) -> dict[dt.date, int]:
    """Maps a period end date to the fiscal year the filer itself calls it.

    EDGAR's `fy` is the reporting filing's fiscal year, so it labels a
    comparative period with the wrong year. But within one annual filing, the
    latest annual period *is* that filing's own fiscal year — and for that row
    `fy` is right. Collecting that pairing across every annual filing yields
    the filer's own labelling for every period it has reported.

    This matters for filers whose year ends early in a calendar year: a
    retailer's fiscal 2023 may end in February 2024, and only the filer can say
    which it is. Nothing here guesses.
    """
    latest_end_by_filing: dict[str, tuple[dt.date, int]] = {}

    for taxonomy, concepts in facts_by_taxonomy.items():
        if taxonomy not in XBRL_TAXONOMY_PREFERENCE:
            continue
        if not isinstance(concepts, Mapping):
            continue
        for concept in concepts.values():
            if not isinstance(concept, Mapping):
                continue
            units = concept.get("units")
            if not isinstance(units, Mapping):
                continue
            for rows in units.values():
                if not isinstance(rows, list):
                    continue
                for row in rows:
                    _note_annual_period(row, latest_end_by_filing)

    index: dict[dt.date, int] = {}
    for period_end, fiscal_year in latest_end_by_filing.values():
        index[period_end] = fiscal_year
    return index


def _note_annual_period(
    row: object, latest_end_by_filing: dict[str, tuple[dt.date, int]]
) -> None:
    """Records the latest annual period an accession reports, with its `fy`."""
    if not isinstance(row, Mapping):
        return
    if row.get("fp") != "FY":
        return

    fiscal_year = row.get("fy")
    accession_no = row.get("accn")
    if not isinstance(fiscal_year, int) or not isinstance(accession_no, str):
        return

    start = _parse_date(row.get("start"))
    end = _parse_date(row.get("end"))
    if start is None or end is None or not _is_annual(start, end):
        return

    incumbent = latest_end_by_filing.get(accession_no)
    if incumbent is None or end > incumbent[0]:
        latest_end_by_filing[accession_no] = (end, fiscal_year)


def _fiscal_year_for(
    period_end: dt.date, fiscal_years: Mapping[dt.date, int]
) -> int | None:
    """The filer's own label for this period end, or None when unknown.

    Returns None rather than the calendar year: a label the filer did not give
    is a label this system does not invent.
    """
    return fiscal_years.get(period_end)


# --- Fact construction ------------------------------------------------------


def _build_fact(
    *,
    spec: MetricSpec,
    company: Company,
    observation: _Observation,
    resolution: _Resolution,
    fiscal_years: Mapping[dt.date, int],
    filings_by_accession: Mapping[str, Filing],
) -> Fact:
    """Assembles one extracted figure into a Fact with full provenance."""
    return Fact(
        metric=spec.metric,
        label=spec.label,
        value=observation.value,
        display_value=_format_value(observation.value, observation.unit),
        unit=observation.unit,
        period_start=observation.period_start,
        period_end=observation.period_end,
        fiscal_year=_fiscal_year_for(observation.period_end, fiscal_years),
        tier=SourceTier.FILING,
        source_type=SourceType.SEC_XBRL,
        source_url=_source_url(
            company, observation.accession_no, filings_by_accession
        ),
        accession_no=observation.accession_no,
        filed_date=observation.filed_date,
        extraction_method=ExtractionMethod.XBRL_COMPANY_FACTS,
        confidence=resolution.confidence,
        resolved_tag=resolution.tag,
        taxonomy=Taxonomy(resolution.taxonomy),
    )


def _not_disclosed_fact(
    spec: MetricSpec, company: Company, manifest: Sequence[Filing]
) -> Fact | None:
    """A marker saying the metric was looked for and is not reported.

    The marker still carries provenance: it names the annual filing that was
    searched. Without a filing to point at there is nothing truthful to say, so
    None is returned and the metric is simply absent.
    """
    annual = _latest_annual_filing(manifest, company.filer_type)
    if annual is None:
        return None

    return Fact(
        metric=spec.metric,
        label=spec.label,
        value=None,
        display_value=NOT_DISCLOSED_TEXT,
        unit=None,
        period_start=None,
        period_end=annual.period_of_report or annual.filed_date,
        fiscal_year=None,
        tier=SourceTier.FILING,
        source_type=SourceType.SEC_FILING,
        source_url=str(annual.primary_doc_url),
        accession_no=annual.accession_no,
        filed_date=annual.filed_date,
        extraction_method=ExtractionMethod.NOT_DISCLOSED,
        confidence=CONFIDENCE_NOT_DISCLOSED,
    )


def _source_url(
    company: Company,
    accession_no: str,
    filings_by_accession: Mapping[str, Filing],
) -> str:
    """Where a reader goes to see this figure in its filing.

    The manifest's primary document is preferred because it lands on the filing
    itself. Falling back to the filing index still lands the reader on the
    right filing, so a missing manifest costs precision, not provenance.
    """
    filing = filings_by_accession.get(accession_no)
    if filing is not None:
        return str(filing.primary_doc_url)
    return edgar.filing_index_url(company.cik, accession_no)


def _format_value(value: float, unit: str) -> str:
    """Renders a figure for display. Never blank, never a bare float repr."""
    if unit in NON_CURRENCY_UNIT_KEYS:
        return f"{value:,.2f}".rstrip("0").rstrip(".") or "0"
    return f"{value:,.0f}" if abs(value) >= 1000 else f"{value:,.2f}"


def _latest_annual_filing(
    manifest: Sequence[Filing], filer_type: FilerType
) -> Filing | None:
    """The most recently filed annual report, amendments included.

    Filer type decides which form is the annual report, so a foreign private
    issuer is not searched for a 10-K it never files.
    """
    annual_forms = _annual_forms_for(filer_type)
    candidates = [
        filing
        for filing in manifest
        if filing.form.split(AMENDMENT_FORM_SUFFIX, 1)[0] in annual_forms
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda f: (f.filed_date, f.accession_no))


def _annual_forms_for(filer_type: FilerType) -> frozenset[str]:
    """Every annual form this filer type might have filed.

    Deliberately generous: a company that migrated from 20-F to 10-K still has
    its older annual reports in the manifest, and either is a valid anchor.
    """
    if filer_type is FilerType.DOMESTIC:
        return frozenset({"10-K"})
    if filer_type is FilerType.CANADIAN:
        return frozenset({"40-F", "20-F"})
    return frozenset({"20-F", "40-F"})


# --- Segment extraction -----------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Context:
    """One XBRL context: what period, and along which dimensions."""

    period_start: dt.date | None
    period_end: dt.date
    #: (axis QName, member QName) pairs, as written in the instance.
    dimensions: tuple[tuple[str, str], ...]


async def _extract_segments_from_filing(
    company: Company, filing: Filing
) -> list[Fact]:
    """Reads dimensional revenue facts out of one filing's XBRL instance."""
    client = edgar.get_client()

    instance_name = await _find_instance_document(client, company, filing)
    if instance_name is None:
        logger.info(
            "No XBRL instance document in filing; segments skipped",
            extra={"cik": company.cik, "accession_no": filing.accession_no},
        )
        return []

    instance_url = edgar.filing_document_url(
        company.cik, filing.accession_no, instance_name
    )
    body = await client.get_bytes(instance_url)
    if len(body) > MAX_INSTANCE_DOCUMENT_BYTES:
        logger.warning(
            "XBRL instance document is larger than the parse ceiling",
            extra={
                "cik": company.cik,
                "accession_no": filing.accession_no,
                "bytes": len(body),
            },
        )
        return []

    return _parse_segment_facts(company, filing, instance_url, body)


async def _find_instance_document(
    client: edgar.EdgarClient, company: Company, filing: Filing
) -> str | None:
    """Names the instance document in a filing's directory.

    EDGAR ships the instance beside its four linkbases. For inline XBRL the
    instance is the extracted `*_htm.xml`; for older filings it is the only
    other XML that is not a linkbase or the filing summary.
    """
    index_url = (
        edgar.filing_document_url(
            company.cik, filing.accession_no, "index.json"
        )
    )
    listing = await client.get_json(index_url)

    directory = listing.get("directory")
    if not isinstance(directory, Mapping):
        return None
    items = directory.get("item")
    if not isinstance(items, list):
        return None

    names: list[str] = [
        name
        for item in items
        if isinstance(item, Mapping)
        for name in [item.get("name")]
        if isinstance(name, str)
    ]
    candidates: list[str] = [
        name
        for name in names
        if name.endswith(".xml")
        and not name.endswith(XBRL_LINKBASE_SUFFIXES)
        and not name.startswith("R")
        and name != "FilingSummary.xml"
    ]
    if not candidates:
        return None

    # The extracted inline instance is the definitive one when present.
    for name in candidates:
        if name.endswith("_htm.xml"):
            return name
    return candidates[0]


def _parse_segment_facts(
    company: Company, filing: Filing, instance_url: str, body: bytes
) -> list[Fact]:
    """Parses segment revenue out of an XBRL instance document."""
    from lxml import etree

    parser = etree.XMLParser(
        resolve_entities=False, no_network=True, huge_tree=False
    )
    root = etree.fromstring(body, parser=parser)

    contexts = _read_contexts(root)
    units = _read_units(root)
    revenue_tags = _revenue_tag_set()

    seen: set[tuple[str, str, dt.date | None, dt.date]] = set()
    facts: list[Fact] = []

    for element in root.iter():
        tag_name = _local_name(str(element.tag))
        if tag_name not in revenue_tags:
            continue

        context = contexts.get(element.get("contextRef") or "")
        if context is None or context.period_start is None:
            continue
        if not _is_annual(context.period_start, context.period_end):
            continue

        segment = _segment_of(context)
        if segment is None:
            continue

        value = _parse_decimal(element.text)
        if value is None:
            continue

        axis, member = segment
        key = (axis, member, context.period_start, context.period_end)
        if key in seen:
            # The same breakdown appears in more than one statement table.
            continue
        seen.add(key)

        facts.append(
            Fact(
                metric=SEGMENT_METRIC,
                label=SEGMENT_METRIC_LABEL,
                value=value,
                display_value=_format_value(value, "USD"),
                unit=units.get(element.get("unitRef") or ""),
                period_start=context.period_start,
                period_end=context.period_end,
                fiscal_year=None,
                segment_axis=axis,
                segment_member=member,
                segment_label=_humanise_member(member),
                tier=SourceTier.FILING,
                source_type=SourceType.SEC_XBRL,
                source_url=instance_url,
                accession_no=filing.accession_no,
                filed_date=filing.filed_date,
                extraction_method=ExtractionMethod.XBRL_DIMENSIONAL,
                confidence=CONFIDENCE_EXACT,
                resolved_tag=tag_name,
                taxonomy=Taxonomy.US_GAAP,
            )
        )

    logger.info(
        "Segment extraction complete",
        extra={
            "cik": company.cik,
            "accession_no": filing.accession_no,
            "segments": len(facts),
        },
    )
    return facts


def _read_contexts(root: XmlElement) -> dict[str, _Context]:
    """Builds the contextRef -> period/dimensions map for an instance."""
    contexts: dict[str, _Context] = {}

    for element in root.iter(f"{{{XBRLI_NAMESPACE}}}context"):
        context_id = element.get("id")
        if not context_id:
            continue

        period = element.find(f"{{{XBRLI_NAMESPACE}}}period")
        if period is None:
            continue

        start = _parse_date(_text_of(period, "startDate"))
        end = _parse_date(_text_of(period, "endDate"))
        if end is None:
            end = _parse_date(_text_of(period, "instant"))
        if end is None:
            continue

        dimensions = tuple(
            (axis, member)
            for axis, member in (
                (
                    (member_element.get("dimension") or "").strip(),
                    (member_element.text or "").strip(),
                )
                for member_element in element.iter(
                    f"{{{XBRLDI_NAMESPACE}}}explicitMember"
                )
            )
            if axis and member
        )

        contexts[context_id] = _Context(
            period_start=start, period_end=end, dimensions=dimensions
        )

    return contexts


def _text_of(period: XmlElement, local: str) -> str | None:
    element = period.find(f"{{{XBRLI_NAMESPACE}}}{local}")
    if element is None or element.text is None:
        return None
    return str(element.text).strip()


def _segment_of(context: _Context) -> tuple[str, str] | None:
    """The single segment axis and member this context selects, if any.

    A context qualifies when exactly one of its dimensions is a configured
    segment axis and every other dimension merely says "this is a reportable
    segment". That rules out cross-tabs — segment by fair-value level is not a
    segmentation of revenue, and summing one would be meaningless.
    """
    if not context.dimensions:
        return None

    segment: tuple[str, str] | None = None

    for axis, member in context.dimensions:
        if _normalise_qname(axis) in _NORMALISED_SEGMENT_AXES:
            if segment is not None:
                # Two segment axes at once is a cross-tab, not a segment.
                return None
            if _normalise_qname(member) in _NORMALISED_EXCLUDED_MEMBERS:
                return None
            segment = (axis, member)
            continue

        if _normalise_pair(axis, member) not in _NORMALISED_NEUTRAL_QUALIFIERS:
            return None

    return segment


def _normalise_qname(qname: str) -> str:
    """Reduces a QName to its lowercased local name.

    Filers differ on whether an axis carries the `srt:` or `us-gaap:` prefix,
    and the taxonomy moves concepts between the two between releases. The local
    name is what identifies the concept, so that is what is compared — matching
    on the prefix would silently drop a filer's segments after a taxonomy
    reshuffle.
    """
    return _QNAME_SPLIT.split(qname.strip())[-1].lower()


def _normalise_pair(axis: str, member: str) -> tuple[str, str]:
    """Normalises an (axis, member) pair, each side by its local name."""
    return (_normalise_qname(axis), _normalise_qname(member))


_NORMALISED_SEGMENT_AXES = frozenset(
    _normalise_qname(axis) for axis in SEGMENT_AXES
)
_NORMALISED_EXCLUDED_MEMBERS = frozenset(
    _normalise_qname(member) for member in SEGMENT_EXCLUDED_MEMBERS
)
_NORMALISED_NEUTRAL_QUALIFIERS = frozenset(
    _normalise_pair(axis, member)
    for axis, member in SEGMENT_NEUTRAL_QUALIFIERS
)


def _revenue_tag_set() -> frozenset[str]:
    """Local names of every tag that could carry segment revenue."""
    for spec in METRIC_SPECS:
        if spec.metric == SEGMENT_SOURCE_METRIC:
            return frozenset(spec.us_gaap_tags) | frozenset(spec.ifrs_tags)
    return frozenset()


def _local_name(tag: str) -> str:
    """Strips the namespace from an lxml tag name."""
    return tag.rsplit("}", 1)[-1]


def _parse_decimal(raw: str | None) -> float | None:
    if raw is None:
        return None
    text = raw.strip().replace(",", "")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _read_units(root: XmlElement) -> dict[str, str]:
    """Builds the unitRef -> measure map, e.g. "usd" -> "USD"."""
    units: dict[str, str] = {}
    for unit in root.iter(f"{{{XBRLI_NAMESPACE}}}unit"):
        unit_id = unit.get("id")
        measure = unit.find(f"{{{XBRLI_NAMESPACE}}}measure")
        if unit_id and measure is not None and measure.text:
            units[str(unit_id)] = str(measure.text).strip().rsplit(":", 1)[-1]
    return units


def _humanise_member(member: str) -> str:
    """Turns "us-gaap:GreaterChinaSegmentMember" into "Greater China".

    A label, not a fact: the figure and its provenance are unaffected by how
    the member reads. The XBRL member QName is kept alongside it so the
    provenance rail can show exactly what was tagged.
    """
    local = _QNAME_SPLIT.split(member)[-1]
    local = local.removesuffix("Member").removesuffix("Segment")
    spaced = _CAMEL_BOUNDARY.sub(" ", local).strip()
    return spaced or local or member
