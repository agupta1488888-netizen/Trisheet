"""Command line entry point, for driving the pipeline without the web app.

    python -m app.cli resolve NKE
    python -m app.cli discover NKE
    python -m app.cli discover "Taiwan Semiconductor" --limit 20
    python -m app.cli report NKE
    python -m app.cli matrix NKE AAPL COST --depth standard

This is an operator tool: it writes to stdout, which is the one place in the
codebase where that is the point rather than a mistake. Application code logs
structured JSON instead.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import logging
import sys
from collections.abc import Sequence

from app import pipeline
from app.config import get_settings
from app.logging_config import configure_logging
from app.models import (
    AnalysisDepth,
    Company,
    FilingRef,
    ResolutionOutcome,
)
from app.modules.m01_resolver import ResolutionError, resolve
from app.modules.m02_discovery import DiscoveryError, build_manifest
from app.services import runlog
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


# --- Running a report --------------------------------------------------------


async def _generate(query: str, depth: AnalysisDepth) -> pipeline.RunOutcome:
    """Resolves a query and runs the full pipeline over it."""
    async with _client() as client:
        company = await _resolve_or_report(client, query)
    if company is None:
        message = f'"{query}" did not resolve to one filer.'
        raise pipeline.PipelineError(message)

    report = runlog.create_report(company.ticker or query, company.cik, depth)
    return await pipeline.run(report.id, report.ticker, company.cik, depth)


def _print_outcome(outcome: pipeline.RunOutcome) -> None:
    """The run, as an operator reads it."""
    report = outcome.report
    _write()
    _rule("=")
    _write(f"{report.ticker}  {report.status.value}")
    _rule("=")
    _field("Report id", report.id)
    _field("Duration", f"{outcome.duration_ms / 1000:.1f}s")
    _field("Facts", str(outcome.facts))

    if report.error_message:
        _field("Reason", report.error_message)

    compliance = outcome.compliance
    if compliance is not None:
        _field("Verification", "passed" if compliance.passed else "FAILED")
        _field(
            "Citation coverage",
            f"{compliance.coverage_display} "
            f"({compliance.cited_figure_count}/{compliance.figure_count})",
        )
        _field(
            "Tiers",
            ", ".join(
                f"T{tier}={count}"
                for tier, count in sorted(compliance.tier_counts.items())
            )
            or NOT_DISCLOSED,
        )
        failed = [check.check for check in compliance.checks if not check.passed]
        _field("Failed checks", ", ".join(failed) if failed else "none")

    _write()
    _write("Steps")
    _rule()
    for step in runlog.steps_for(report.id):
        count = (
            f"{step.count} {step.count_label or ''}".strip()
            if step.count is not None
            else ""
        )
        elapsed = f"{step.duration_ms}ms" if step.duration_ms is not None else ""
        _write(
            f"  {step.module:<6}{step.state.value:<9}{elapsed:>9}  "
            f"{count:<16}{step.detail or ''}"
        )

    if outcome.document is not None:
        _write()
        _write("Artifacts")
        _rule()
        for artifact in outcome.document.artifacts:
            where = artifact.url or artifact.unavailable_reason or NOT_DISCLOSED
            _write(
                f"  {str(artifact.kind):<6}{artifact.size_bytes:>9} bytes  "
                f"{where}"
            )


async def _run_report(query: str, depth: AnalysisDepth) -> int:
    outcome = await _generate(query, depth)
    _print_outcome(outcome)
    return EXIT_OK if outcome.completed else EXIT_FAILED


# --- The test matrix ---------------------------------------------------------

#: Columns of the matrix table, and how wide each prints.
_MATRIX_COLUMNS: tuple[tuple[str, int], ...] = (
    ("TICKER", 8),
    ("DONE", 6),
    ("SECS", 7),
    ("FACTS", 7),
    ("COVERAGE", 10),
    ("TIERS", 22),
    ("PDF", 6),
    ("XLSX", 6),
    ("WARNINGS", 9),
)


async def _run_matrix(tickers: Sequence[str], depth: AnalysisDepth) -> int:
    """Runs the pipeline over several tickers and prints one row each.

    Sequential on purpose. The point of the matrix is to exercise the pipeline
    against real filers, and running them concurrently would put the shared
    EDGAR limiter under contention that has nothing to do with what is being
    measured.
    """
    header = "".join(name.ljust(width) for name, width in _MATRIX_COLUMNS)
    rows: list[str] = []
    failures = 0
    started = dt.datetime.now(dt.UTC)

    for ticker in tickers:
        try:
            outcome = await _generate(ticker, depth)
        except (pipeline.PipelineError, ResolutionError, EdgarError) as failure:
            failures += 1
            rows.append(_matrix_row(ticker, None, str(failure)))
            _write(f"  {ticker:<8} failed: {failure}")
            continue

        if not outcome.completed:
            failures += 1
        rows.append(_matrix_row(ticker, outcome, None))
        _write(f"  {ticker:<8} {outcome.report.status.value}")

    elapsed = (dt.datetime.now(dt.UTC) - started).total_seconds()

    _write()
    _rule("=")
    _write(header)
    _rule("=")
    for row in rows:
        _write(row)
    _rule()
    _write(
        f"  {len(tickers) - failures}/{len(tickers)} complete "
        f"in {elapsed:.0f}s"
    )
    return EXIT_OK if failures == 0 else EXIT_FAILED


def _matrix_row(
    ticker: str, outcome: pipeline.RunOutcome | None, failure: str | None
) -> str:
    """One row of the matrix table."""
    if outcome is None:
        cells = [ticker, "no", "-", "-", "-", "-", "-", "-", failure or "1"]
        return "".join(
            str(cell)[: width - 1].ljust(width)
            for cell, (_, width) in zip(cells, _MATRIX_COLUMNS, strict=True)
        )

    compliance = outcome.compliance
    artifacts = (
        {str(a.kind): a for a in outcome.document.artifacts}
        if outcome.document is not None
        else {}
    )

    def artifact_cell(kind: str) -> str:
        ref = artifacts.get(kind)
        if ref is None or ref.size_bytes == 0:
            return "-"
        return f"{ref.size_bytes // 1024}k"

    cells = [
        ticker,
        "yes" if outcome.completed else "no",
        f"{outcome.duration_ms / 1000:.1f}",
        str(outcome.facts),
        compliance.coverage_display if compliance else "-",
        (
            ",".join(
                f"T{tier}:{count}"
                for tier, count in sorted(compliance.tier_counts.items())
            )
            if compliance
            else "-"
        ),
        artifact_cell("pdf"),
        artifact_cell("xlsx"),
        str(len(outcome.warnings)),
    ]
    return "".join(
        str(cell)[: width - 1].ljust(width)
        for cell, (_, width) in zip(cells, _MATRIX_COLUMNS, strict=True)
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.cli",
        description="Trisheet operator commands.",
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

    depths = [depth.value for depth in AnalysisDepth]

    report_parser = subparsers.add_parser(
        "report", help="run the full pipeline over one filer"
    )
    report_parser.add_argument("query", help="ticker or company name")
    report_parser.add_argument(
        "--depth",
        choices=depths,
        default=AnalysisDepth.STANDARD.value,
        help="how far the pipeline goes (default standard)",
    )

    matrix_parser = subparsers.add_parser(
        "matrix", help="run the pipeline over several filers and tabulate"
    )
    matrix_parser.add_argument("tickers", nargs="+", help="tickers to run")
    matrix_parser.add_argument(
        "--depth",
        choices=depths,
        default=AnalysisDepth.STANDARD.value,
        help="how far the pipeline goes (default standard)",
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
        if arguments.command == "report":
            return asyncio.run(
                _run_report(arguments.query, AnalysisDepth(arguments.depth))
            )
        if arguments.command == "matrix":
            return asyncio.run(
                _run_matrix(arguments.tickers, AnalysisDepth(arguments.depth))
            )
        return asyncio.run(
            _run_discover(
                arguments.query,
                arguments.limit,
                exhibits=not arguments.no_exhibits,
            )
        )
    except (
        ResolutionError,
        DiscoveryError,
        EdgarError,
        pipeline.PipelineError,
    ) as failure:
        _write(str(failure))
        return EXIT_FAILED
    except KeyboardInterrupt:
        return EXIT_FAILED


if __name__ == "__main__":
    sys.exit(main())
