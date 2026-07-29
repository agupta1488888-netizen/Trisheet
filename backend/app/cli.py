"""Command line entry point, for driving the pipeline without the web app.

    python -m app.cli resolve NKE
    python -m app.cli discover NKE
    python -m app.cli discover "Taiwan Semiconductor" --limit 20

This is an operator tool: it writes to stdout, which is the one place in the
codebase where that is the point rather than a mistake. Application code logs
structured JSON instead.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from collections.abc import Sequence

from app.config import get_settings
from app.logging_config import configure_logging
from app.models import Company, FilingRef, ResolutionOutcome
from app.modules.m01_resolver import ResolutionError, resolve
from app.modules.m02_discovery import DiscoveryError, build_manifest
from app.services.edgar import EdgarClient, EdgarError

EXIT_OK = 0
EXIT_FAILED = 1

DEFAULT_MANIFEST_LIMIT = 40
RULE_WIDTH = 78

NOT_DISCLOSED = "Not disclosed"


def _write(line: str = "") -> None:
    sys.stdout.write(f"{line}\n")


def _rule(character: str = "-") -> None:
    _write(character * RULE_WIDTH)


def _field(label: str, value: str | None) -> None:
    _write(f"  {label:<20}{value if value else NOT_DISCLOSED}")


def _print_company(company: Company) -> None:
    _write(company.name)
    _rule()
    _field("Ticker", company.ticker)
    _field("CIK", company.cik)
    _field("Filer type", company.filer_type.value)
    _field("Annual form", _annual_form_for(company))
    _field("SIC", company.sic_code)
    _field("Sector", company.sector)
    _field("Fiscal year end", _format_fiscal_year_end(company.fiscal_year_end))
    _field("Currency", company.reporting_currency)


def _annual_form_for(company: Company) -> str:
    """The form this filer's annual report arrives on."""
    return {"domestic": "10-K", "foreign": "20-F", "canadian": "40-F"}[
        company.filer_type.value
    ]


def _format_fiscal_year_end(value: str | None) -> str | None:
    """Renders EDGAR's MMDD as a readable date."""
    if not value or len(value) != 4 or not value.isdigit():
        return value
    months = (
        "January February March April May June July "
        "August September October November December"
    ).split()
    month = int(value[:2])
    day = int(value[2:])
    if not 1 <= month <= len(months):
        return value
    return f"{day} {months[month - 1]}"


def _print_manifest(refs: Sequence[FilingRef], limit: int) -> None:
    _write()
    _write(f"Filings ({len(refs)} after amendment precedence)")
    _rule()
    _write(f"  {'FORM':<10}{'FILED':<13}{'PERIOD':<13}{'ACCESSION':<22}EXHIBITS")
    _rule()

    for ref in refs[:limit]:
        period = (
            ref.period_of_report.isoformat()
            if ref.period_of_report is not None
            else "-"
        )
        exhibits = (
            ", ".join(exhibit.exhibit_type for exhibit in ref.exhibits)
            if ref.exhibits
            else "-"
        )
        _write(
            f"  {ref.form:<10}{ref.filed_date.isoformat():<13}"
            f"{period:<13}{ref.accession_no:<22}{exhibits}"
        )

    if len(refs) > limit:
        _write(f"  ... {len(refs) - limit} more")

    _write()
    _print_manifest_summary(refs)


def _print_manifest_summary(refs: Sequence[FilingRef]) -> None:
    counts: dict[str, int] = {}
    for ref in refs:
        counts[ref.base_form] = counts.get(ref.base_form, 0) + 1

    _write("By form")
    _rule()
    for form in sorted(counts):
        _write(f"  {form:<10}{counts[form]:>5}")

    with_exhibits = sum(1 for ref in refs if ref.exhibits)
    amendments = sum(1 for ref in refs if ref.is_amendment)
    _write()
    _write(f"  {'Amendments':<20}{amendments:>5}")
    _write(f"  {'With exhibits':<20}{with_exhibits:>5}")


async def _resolve_or_report(client: EdgarClient, query: str) -> Company | None:
    """Resolves, printing candidates and returning None when ambiguous."""
    resolution = await resolve(query, client)

    if resolution.outcome is ResolutionOutcome.NOT_FOUND:
        _write(f'No US-listed company found for "{query}".')
        _write("Try a ticker, or the company name as it appears in filings.")
        return None

    if resolution.outcome is ResolutionOutcome.AMBIGUOUS:
        _write(f'"{query}" matches more than one company. Choose one:')
        _rule()
        _write(f"  {'TICKER':<10}{'CIK':<14}NAME")
        _rule()
        for candidate in resolution.candidates:
            _write(f"  {candidate.ticker:<10}{candidate.cik:<14}{candidate.name}")
        return None

    return resolution.company


async def _run_resolve(query: str) -> int:
    async with _client() as client:
        company = await _resolve_or_report(client, query)
        if company is None:
            return EXIT_FAILED
        _print_company(company)
    return EXIT_OK


async def _run_discover(query: str, limit: int, *, exhibits: bool) -> int:
    async with _client() as client:
        company = await _resolve_or_report(client, query)
        if company is None:
            return EXIT_FAILED

        _print_company(company)
        manifest = await build_manifest(
            company, client, include_exhibits=exhibits
        )
        _print_manifest(manifest, limit)
    return EXIT_OK


def _client() -> EdgarClient:
    settings = get_settings()
    return EdgarClient(user_agent=settings.edgar_user_agent)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.cli",
        description="Tearsheet operator commands.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    resolve_parser = subparsers.add_parser(
        "resolve", help="resolve a ticker or company name to a filer"
    )
    resolve_parser.add_argument("query", help="ticker or company name")

    discover_parser = subparsers.add_parser(
        "discover", help="resolve, then print the filing manifest"
    )
    discover_parser.add_argument("query", help="ticker or company name")
    discover_parser.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_MANIFEST_LIMIT,
        help=f"filings to list (default {DEFAULT_MANIFEST_LIMIT})",
    )
    discover_parser.add_argument(
        "--no-exhibits",
        action="store_true",
        help="skip reading 8-K indexes, for a faster manifest",
    )

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    settings = get_settings()
    configure_logging(settings.log_level)
    # The operator wants the report, not the pipeline's log stream.
    logging.getLogger().setLevel(logging.WARNING)

    arguments = _build_parser().parse_args(argv)

    if not settings.edgar_configured:
        _write("No SEC contact email is configured.")
        _write("Set EDGAR_CONTACT_EMAIL in backend/.env; SEC refuses requests")
        _write("that do not identify a contact.")
        return EXIT_FAILED

    try:
        if arguments.command == "resolve":
            return asyncio.run(_run_resolve(arguments.query))
        return asyncio.run(
            _run_discover(
                arguments.query,
                arguments.limit,
                exhibits=not arguments.no_exhibits,
            )
        )
    except (ResolutionError, DiscoveryError, EdgarError) as failure:
        _write(str(failure))
        return EXIT_FAILED
    except KeyboardInterrupt:
        return EXIT_FAILED


if __name__ == "__main__":
    sys.exit(main())
