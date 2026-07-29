"""m02 — filing discovery.

Responsibility
    Turn a filer's full submission history into a manifest of the filings a
    report may draw on: typed `FilingRef` objects with absolute URLs and
    accession numbers.

    Three things happen here that nothing downstream should have to repeat:

    - The history is filtered to the permitted form set. Everything else —
      Form 4s, S-8s, SC 13Gs — is dropped, so later modules never have to ask
      whether a form is one they understand.
    - Amendment precedence is applied. For a given form and period the latest
      amendment wins over the original, because a restated figure is the
      reported figure.
    - Current reports (8-K, 6-K) have their indexes read so that the EX-99.1
      earnings release and EX-99.2 presentation are addressable. Those exhibits
      carry the numbers the press reports, and they are not in the primary
      document.

Public interface
    build_manifest(company, client, include_exhibits) -> list[FilingRef]
    superseded_filings(company, client) -> list[FilingRef]
    as_filings(refs) -> list[Filing]
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import Any
from urllib.parse import urljoin

from lxml import html

from app.config import (
    AMENDMENT_FORM_SUFFIX,
    CURRENT_REPORT_FORMS,
    EXHIBIT_TYPES_OF_INTEREST,
    MAX_CURRENT_REPORTS_WITH_EXHIBITS,
    PERMITTED_FORMS,
    SEC_ARCHIVES_BASE_URL,
    SEC_SUBMISSIONS_SHARD_URL_TEMPLATE,
)
from app.models import Company, ExhibitRef, Filing, FilingRef
from app.services.edgar import (
    EdgarClient,
    EdgarError,
    filing_document_url,
    filing_index_url,
    pad_cik,
    submissions_url,
)

logger = logging.getLogger(__name__)

#: Inline-XBRL viewer prefix EDGAR puts in front of some document links.
_IXBRL_VIEWER_PREFIX = "/ix?doc="


class DiscoveryError(Exception):
    """The submission history could not be read."""


# --- Form helpers -----------------------------------------------------------


def base_form(form: str) -> str:
    """Strips the amendment suffix: '10-K/A' -> '10-K'."""
    return form.split(AMENDMENT_FORM_SUFFIX)[0].strip()


def is_amendment(form: str) -> bool:
    return AMENDMENT_FORM_SUFFIX in form


def _parse_date(value: str) -> dt.date | None:
    if not value:
        return None
    try:
        return dt.date.fromisoformat(value)
    except ValueError:
        return None


# --- Submission history -----------------------------------------------------


def _column(block: dict[str, Any], key: str) -> list[str]:
    """Reads one parallel array from a submissions block."""
    column = block.get(key)
    if not isinstance(column, list):
        return []
    return [str(item) if item is not None else "" for item in column]


def _rows_from_block(cik: str, block: dict[str, Any]) -> list[FilingRef]:
    """Builds FilingRefs from one submissions block's parallel arrays."""
    accessions = _column(block, "accessionNumber")
    forms = _column(block, "form")
    filed_dates = _column(block, "filingDate")
    report_dates = _column(block, "reportDate")
    documents = _column(block, "primaryDocument")
    items = _column(block, "items")

    refs: list[FilingRef] = []
    for index, accession in enumerate(accessions):
        form = forms[index] if index < len(forms) else ""
        if not accession or not form:
            continue

        stem = base_form(form)
        if stem not in PERMITTED_FORMS:
            continue

        filed = _parse_date(filed_dates[index] if index < len(filed_dates) else "")
        if filed is None:
            # A filing with no filing date cannot be placed in time, and every
            # fact drawn from it would carry an empty filed_date.
            logger.warning(
                "Dropping filing with no filing date",
                extra={"cik": cik, "accession_no": accession},
            )
            continue

        document = documents[index] if index < len(documents) else ""
        try:
            index_url = filing_index_url(cik, accession)
            primary_url = (
                filing_document_url(cik, accession, document)
                if document
                else index_url
            )
        except ValueError:
            logger.warning(
                "Dropping filing with a malformed accession number",
                extra={"cik": cik, "accession_no": accession},
            )
            continue

        raw_items = items[index] if index < len(items) else ""
        refs.append(
            FilingRef(
                accession_no=accession,
                cik=cik,
                form=form,
                base_form=stem,
                is_amendment=is_amendment(form),
                filed_date=filed,
                period_of_report=_parse_date(
                    report_dates[index] if index < len(report_dates) else ""
                ),
                primary_doc_url=primary_url,
                filing_index_url=index_url,
                items=tuple(
                    item.strip() for item in raw_items.split(",") if item.strip()
                ),
            )
        )
    return refs


def _shard_names(filings_map: dict[str, Any]) -> list[str]:
    files = filings_map.get("files")
    if not isinstance(files, list):
        return []
    return [
        shard["name"]
        for shard in files
        if isinstance(shard, dict) and isinstance(shard.get("name"), str)
    ]


async def _all_filings(client: EdgarClient, cik: str) -> list[FilingRef]:
    """Reads the complete history, following shards for long filers."""
    submissions = await client.get_json(submissions_url(cik))

    filings = submissions.get("filings")
    filings_map = filings if isinstance(filings, dict) else {}

    recent = filings_map.get("recent")
    refs = _rows_from_block(cik, recent if isinstance(recent, dict) else {})

    for shard_name in _shard_names(filings_map):
        try:
            shard = await client.get_json(
                SEC_SUBMISSIONS_SHARD_URL_TEMPLATE.format(name=shard_name)
            )
        except EdgarError:
            # An unreadable shard costs older filings, not the whole manifest.
            logger.warning(
                "Could not read submissions shard",
                extra={"cik": cik, "shard": shard_name},
            )
            continue
        refs.extend(_rows_from_block(cik, shard))

    return refs


# --- Amendment precedence ---------------------------------------------------


def _precedence_key(ref: FilingRef) -> tuple[str, str]:
    """Groups filings that describe the same form and period.

    Falls back to the filing date when a form carries no period of report, so
    that unrelated filings are never collapsed into one another.
    """
    period = (
        ref.period_of_report.isoformat()
        if ref.period_of_report is not None
        else ref.filed_date.isoformat()
    )
    return ref.base_form, period


def apply_amendment_precedence(refs: list[FilingRef]) -> list[FilingRef]:
    """Keeps the authoritative filing for each form and period.

    An amendment supersedes the original for the same period; between two
    amendments the later filing wins. A restated figure is the reported figure.
    """
    winners: dict[tuple[str, str], FilingRef] = {}
    for ref in refs:
        key = _precedence_key(ref)
        held = winners.get(key)
        if held is None or _supersedes(ref, held):
            winners[key] = ref
    return list(winners.values())


def _supersedes(candidate: FilingRef, held: FilingRef) -> bool:
    """True when `candidate` is the more authoritative of the two."""
    if candidate.is_amendment != held.is_amendment:
        return candidate.is_amendment
    return candidate.filed_date > held.filed_date


def superseded_of(refs: list[FilingRef]) -> list[FilingRef]:
    """The filings amendment precedence discarded. Useful for an audit trail."""
    kept = {ref.accession_no for ref in apply_amendment_precedence(refs)}
    return [ref for ref in refs if ref.accession_no not in kept]


# --- Exhibits ---------------------------------------------------------------


def _absolute_document_url(href: str) -> str:
    cleaned = href.strip()
    if cleaned.startswith(_IXBRL_VIEWER_PREFIX):
        cleaned = cleaned[len(_IXBRL_VIEWER_PREFIX) :]
    return urljoin(SEC_ARCHIVES_BASE_URL, cleaned)


def parse_exhibits(index_html: str) -> tuple[ExhibitRef, ...]:
    """Extracts the exhibits of interest from a filing index page.

    EDGAR's index tables carry the exhibit type in its own column, which is the
    only place the EX-99.1 / EX-99.2 distinction is stated. The document name
    is not reliable — filers name these files anything.
    """
    try:
        document = html.fromstring(index_html)
    except (ValueError, TypeError):
        return ()

    exhibits: list[ExhibitRef] = []
    seen: set[str] = set()

    for row in document.xpath("//tr"):
        cells = [
            " ".join(cell.text_content().split()) for cell in row.xpath("./td")
        ]
        if not cells:
            continue

        matched = next(
            (
                wanted
                for wanted in EXHIBIT_TYPES_OF_INTEREST
                if any(cell.upper() == wanted for cell in cells)
            ),
            None,
        )
        if matched is None or matched in seen:
            continue

        hrefs = row.xpath(".//a/@href")
        if not hrefs:
            continue

        seen.add(matched)
        description = next(
            (
                cell
                for cell in cells
                if cell and cell.upper() != matched and not cell.isdigit()
            ),
            None,
        )
        exhibits.append(
            ExhibitRef(
                exhibit_type=matched,
                description=description,
                url=_absolute_document_url(str(hrefs[0])),
            )
        )

    return tuple(exhibits)


async def _attach_exhibits(
    client: EdgarClient, refs: list[FilingRef]
) -> list[FilingRef]:
    """Reads indexes for the most recent current reports and attaches exhibits.

    Each index costs one request against the SEC budget, so only the most
    recent current reports are expanded.
    """
    current = sorted(
        (ref for ref in refs if ref.base_form in CURRENT_REPORT_FORMS),
        key=lambda ref: ref.filed_date,
        reverse=True,
    )[:MAX_CURRENT_REPORTS_WITH_EXHIBITS]
    targets = {ref.accession_no for ref in current}

    resolved: list[FilingRef] = []
    for ref in refs:
        if ref.accession_no not in targets:
            resolved.append(ref)
            continue
        try:
            index_html = await client.get_text(str(ref.filing_index_url))
        except EdgarError:
            # Exhibits are an enrichment. Losing them must not lose the filing.
            logger.warning(
                "Could not read filing index",
                extra={"accession_no": ref.accession_no},
            )
            resolved.append(ref)
            continue
        resolved.append(
            ref.model_copy(update={"exhibits": parse_exhibits(index_html)})
        )
    return resolved


# --- Public interface -------------------------------------------------------


async def build_manifest(
    company: Company, client: EdgarClient, *, include_exhibits: bool = True
) -> list[FilingRef]:
    """Builds the filing manifest for a company, newest filing first."""
    cik = pad_cik(company.cik)
    refs = await _all_filings(client, cik)

    if not refs:
        message = (
            "No filings found for this company in the permitted form set. "
            "Tearsheet builds profiles from periodic and current reports."
        )
        raise DiscoveryError(message)

    authoritative = apply_amendment_precedence(refs)

    if include_exhibits:
        authoritative = await _attach_exhibits(client, authoritative)

    authoritative.sort(key=lambda ref: (ref.filed_date, ref.accession_no), reverse=True)

    logger.info(
        "Filing manifest built",
        extra={
            "cik": cik,
            "filings": len(authoritative),
            "superseded": len(refs) - len(authoritative),
        },
    )
    return authoritative


def as_filings(refs: list[FilingRef]) -> list[Filing]:
    """Narrows the manifest to the `Filing` shape the extraction modules take.

    m03 and m04 only need to address a document; the amendment flag, exhibits
    and 8-K items that FilingRef carries are this module's concern and m08's,
    not theirs. Converting here keeps that asymmetry in one place rather than
    forcing every consumer to know about both shapes.
    """
    return [
        Filing(
            accession_no=ref.accession_no,
            cik=ref.cik,
            form=ref.form,
            filed_date=ref.filed_date,
            period_of_report=ref.period_of_report,
            primary_doc_url=ref.primary_doc_url,
        )
        for ref in refs
    ]


async def superseded_filings(
    company: Company, client: EdgarClient
) -> list[FilingRef]:
    """The filings amendment precedence set aside, newest first."""
    refs = await _all_filings(client, pad_cik(company.cik))
    discarded = superseded_of(refs)
    discarded.sort(key=lambda ref: ref.filed_date, reverse=True)
    return discarded
