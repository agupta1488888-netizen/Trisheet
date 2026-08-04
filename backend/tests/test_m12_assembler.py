"""Tests for m12's risk categorisation and the printed document.

`render_html` is pure, which is why it exists separately from `assemble_pdf`:
the layout can be asserted on without a rendering engine installed. That
matters here — WeasyPrint's native libraries are absent on some development
machines, and the PDF is the artifact handed to a reader, so its structure
should not go unverified merely because one machine cannot rasterise it.

## Risk categorisation

The category answers "what is this risk about", and deliberately nothing
else. There is no probability here, no impact score and no severity, because
a filing states none of them — a rated risk matrix would be this system
inventing figures the filer did not disclose, which is the one thing it must
never do.

What is worth testing is therefore narrow but real: that the declared order
resolves headings which legitimately mention two categories, and that a
heading matching nothing is left alone rather than swept into the nearest
category to avoid an empty tag.
"""

from __future__ import annotations

import datetime as dt

import pytest

from app.models import (
    AnalysisDepth,
    Company,
    ComplianceReport,
    DocumentFact,
    DocumentSection,
    ExtractionMethod,
    FilerType,
    Report,
    ReportDocument,
    ReportStatus,
    RiskCategory,
    SectionId,
    SourceTier,
    SourceType,
)
from app.modules.m12_assembler import _risk_category, render_html

_NOW = dt.datetime(2026, 8, 4, tzinfo=dt.UTC)


def _fact() -> DocumentFact:
    return DocumentFact(
        id="f1",
        report_id="r1",
        metric="income.revenue",
        label="Revenue",
        value=51_362_000_000.0,
        display_value="51,362",
        unit="USD",
        period_end=dt.date(2025, 5, 31),
        fiscal_year=2025,
        tier=SourceTier.FILING,
        source_type=SourceType.SEC_XBRL,
        source_url="https://www.sec.gov/Archives/nke-20250531.htm",
        accession_no="0000320187-25-000039",
        filed_date=dt.date(2025, 7, 24),
        extraction_method=ExtractionMethod.XBRL_COMPANY_FACTS,
        confidence=1.0,
    )


def _document(
    *,
    sections: tuple[DocumentSection, ...] = (),
    facts: tuple[DocumentFact, ...] = (),
) -> ReportDocument:
    return ReportDocument(
        report=Report(
            id="r1",
            ticker="NKE",
            cik="0000320187",
            status=ReportStatus.COMPLETE,
            created_at=_NOW,
            completed_at=_NOW,
        ),
        company=Company(
            cik="0000320187",
            ticker="NKE",
            name="NIKE, Inc.",
            filer_type=FilerType.DOMESTIC,
            exchange="NYSE",
            headquarters="Beaverton, OR",
        ),
        depth=AnalysisDepth.STANDARD,
        facts=facts,
        sections=sections,
        compliance=ComplianceReport(
            passed=True,
            verified_at=_NOW,
            fact_count=len(facts),
            figure_count=len(facts),
            cited_figure_count=len(facts),
            coverage_ratio=1.0,
            coverage_display="100%",
        ),
    )


@pytest.mark.parametrize(
    ("heading", "expected"),
    [
        (
            "Our indebtedness could adversely affect our financial condition",
            RiskCategory.FINANCIAL,
        ),
        (
            "We rely on a single supplier for a majority of our raw materials",
            RiskCategory.OPERATIONAL,
        ),
        (
            "Changes in consumer demand could reduce our revenue",
            RiskCategory.MARKET,
        ),
        (
            "We are subject to environmental regulation in many jurisdictions",
            RiskCategory.REGULATORY,
        ),
        (
            "We are party to litigation that could result in material losses",
            RiskCategory.LEGAL,
        ),
    ],
)
def test_a_heading_is_classified_by_what_it_is_about(
    heading: str, expected: RiskCategory
) -> None:
    assert _risk_category(heading) is expected


def test_an_unrecognised_heading_is_left_uncategorised() -> None:
    # Forcing this into the nearest category would put a tag on the page that
    # the filer's own words do not support. It renders untagged instead.
    assert _risk_category("We may not achieve our strategic objectives") is None


def test_declared_order_settles_a_heading_that_mentions_two_categories() -> None:
    # This heading contains "patent" (legal) and "product" (operational).
    # Legal is declared first precisely so infringement reads as a legal
    # exposure rather than an operational one.
    heading = "Third parties may claim our products infringe their patents"

    assert _risk_category(heading) is RiskCategory.LEGAL


def test_classification_is_case_insensitive() -> None:
    # Filers set headings in capitals, title case and sentence case, and a
    # 10-K commonly uses more than one of those in the same item.
    upper = _risk_category("OUR INDEBTEDNESS COULD LIMIT OUR FLEXIBILITY")

    assert upper is RiskCategory.FINANCIAL


# --- The printed document ----------------------------------------------------


def test_cover_names_the_company_and_carries_the_mark() -> None:
    markup = render_html(_document())

    assert "class='cover'" in markup
    # Drawn, not fetched: WeasyPrint renders with no network access, so an
    # external asset would leave a hole in the cover of the artifact that
    # actually gets handed over.
    assert "<svg" in markup
    assert "<span class='wordmark'>Trisheet</span>" in markup
    assert "Company profile" in markup
    assert "NIKE, Inc." in markup
    assert "NYSE" in markup
    assert "Issued 2026-08-04" in markup


def test_cover_states_the_basis_of_every_figure() -> None:
    # The claim the product rests on belongs on the cover, not three pages
    # into an appendix a forwarded copy may never be read past.
    markup = render_html(_document())

    assert "traces to a filing" in markup
    assert "Nothing here is estimated." in markup


def test_the_cover_suppresses_the_running_header() -> None:
    # The cover names the company in 34pt. Repeating it in the header three
    # lines above would read as a mistake.
    markup = render_html(_document())

    assert "@page cover" in markup
    assert "string-set: company-name content();" in markup


def test_contents_lists_a_section_that_could_not_be_built() -> None:
    # Silently omitting it would imply the profile was never meant to have
    # that section. Naming the reason is the honest answer.
    document = _document(
        sections=(
            DocumentSection(id=SectionId.SNAPSHOT, title="Snapshot"),
            DocumentSection(
                id=SectionId.BUSINESS,
                title="Business",
                unavailable_reason="The annual report did not name this item.",
            ),
        )
    )

    markup = render_html(document)

    assert "class='contents'" in markup
    assert "Snapshot" in markup
    assert "The annual report did not name this item." in markup


def test_sources_appendix_states_the_hierarchy_and_the_rule() -> None:
    # A printed profile cannot be read alongside the codebase, so the
    # constraint is written beside the sources it governs.
    markup = render_html(_document(facts=(_fact(),)))

    assert "Source hierarchy" in markup
    assert "0000320187-25-000039" in markup
    assert "tier 1 and tier 2" in markup
    # The rule is stated as enforcement, not intention: refused at write
    # time, which is where m06 actually refuses it.
    assert "refused when a figure is written" in markup


def test_a_document_with_no_sources_omits_the_appendix() -> None:
    # And therefore the hierarchy with it: a legend explaining a table that
    # is not there would be furniture.
    markup = render_html(_document())

    assert "Source hierarchy" not in markup
