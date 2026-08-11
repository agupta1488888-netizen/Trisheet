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
    Fact,
    FilerType,
    Report,
    ReportDocument,
    ReportStatus,
    RiskCategory,
    SectionId,
    SourceTier,
    SourceType,
)
from app.modules.m12_assembler import _build_index, _risk_category, render_html

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


def test_the_cover_box_includes_its_own_padding() -> None:
    # Confirmed live: without box-sizing: border-box, a 297mm height plus
    # 24mm top-and-bottom padding renders as a 345mm box on a 297mm page,
    # and the closing lines of the cover spill onto an otherwise-blank
    # second page. This only asserts the declaration exists — actual layout
    # needs a renderer this suite does not have — but the declaration is
    # the entire fix, so its absence is the entire regression.
    markup = render_html(_document())

    cover_start = markup.index(".cover {")
    cover_end = markup.index("}", cover_start)
    cover_rule = markup[cover_start:cover_end]

    assert "box-sizing: border-box" in cover_rule


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


# --- Years shown in a figure table --------------------------------------------


def _annual_fact(metric: str, year: int) -> Fact:
    """One anchor-metric figure for a fiscal year, for period-selection tests."""
    return Fact(
        metric=metric,
        label=metric.rsplit(".", 1)[-1].replace("_", " ").capitalize(),
        value=float(year),
        display_value=f"{year}",
        unit="USD",
        period_start=dt.date(year - 1, 1, 1),
        period_end=dt.date(year, 12, 31),
        fiscal_year=year,
        tier=SourceTier.FILING,
        source_type=SourceType.SEC_XBRL,
        source_url="https://www.sec.gov/Archives/nke-x.htm",
        accession_no=f"0000320187-{year % 100:02d}-000039",
        filed_date=dt.date(year + 1, 2, 1),
        extraction_method=ExtractionMethod.XBRL_COMPANY_FACTS,
        confidence=1.0,
    )


def test_a_full_depth_run_shows_all_ten_extracted_years() -> None:
    # Regression: a fixed MAX_TABLE_PERIODS constant in m12 once re-truncated
    # a "full" (10y) or custom-range run back down to 5 years, after m03 had
    # already correctly extracted the years the run's depth asked for. The
    # table must show every year that survives, not re-impose a second cap.
    years = range(2016, 2026)
    facts = tuple(
        _annual_fact(metric, year)
        for year in years
        for metric in (
            "income.revenue",
            "balance.total_assets",
            "income.net_income",
        )
    )

    index = _build_index(facts)

    assert index.years == tuple(years)


def test_a_standard_depth_run_still_shows_only_its_five_extracted_years() -> None:
    # The fix must defer entirely to what was extracted, not simply show
    # everything unbounded — a standard run's five years should still be
    # five, because m03 never extracted a sixth.
    years = range(2021, 2026)
    facts = tuple(
        _annual_fact(metric, year)
        for year in years
        for metric in (
            "income.revenue",
            "balance.total_assets",
            "income.net_income",
        )
    )

    index = _build_index(facts)

    assert index.years == tuple(years)


# --- The analysis tables ------------------------------------------------------
#
# m07 computes considerably more than m12 once rendered: DuPont, common size,
# compound growth, per-share, shareholder returns, the margin bridge and the
# working-capital durations were all derived on every run and then dropped on
# the floor at assembly. These guard against that happening again silently —
# a metric family that stops resolving fails here rather than simply vanishing
# from the report.
#
# The fact set is m07's own, imported rather than restated so that the two
# modules are exercised against one description of what a filer discloses.


def _analysed_index() -> object:
    from app.modules.m07_analysis import analyse
    from tests.test_m07_analysis import _general_facts

    facts = list(_general_facts())
    return _build_index([*facts, *analyse(facts).facts])


@pytest.mark.parametrize(
    ("caption", "rows_name"),
    [
        ("Return on equity, decomposed", "_DUPONT_ROWS"),
        ("Working capital", "_WORKING_CAPITAL_ROWS"),
        ("Compound growth", "_CAGR_ROWS"),
        ("What moved the operating margin", "_BRIDGE_ROWS"),
        ("Per share", "_PER_SHARE_ROWS"),
        ("Shareholder returns", "_SHAREHOLDER_ROWS"),
        ("Income statement, common size", "_COMMON_SIZE_INCOME_ROWS"),
        ("Balance sheet, common size", "_COMMON_SIZE_BALANCE_ROWS"),
    ],
)
def test_each_analysis_table_renders_from_what_m07_derives(
    caption: str, rows_name: str
) -> None:
    from app.modules import m12_assembler as m12

    index = _analysed_index()
    rows = getattr(m12, rows_name)

    table = m12._table("t", caption, rows, index, [], unit_note="")

    assert table is not None, f"{caption} rendered no table"
    assert table.rows, f"{caption} rendered no rows"
    populated = sum(1 for row in table.rows for cell in row.fact_ids if cell)
    assert populated > 0, f"{caption} rendered rows but no figures"


def test_the_cash_conversion_cycle_is_not_shown_twice_in_the_analysis() -> None:
    """It belongs with the durations it is made of, not among leverage ratios.

    The risks section shows the leverage set again on purpose, and says why;
    showing the cycle in two analysis tables would be duplication with no such
    reason behind it.
    """
    from app.modules import m12_assembler as m12

    strength = {row.metric for row in m12._STRENGTH_ROWS}
    working = {row.metric for row in m12._WORKING_CAPITAL_ROWS}

    assert "working_capital.cash_conversion_cycle" in working
    assert "working_capital.cash_conversion_cycle" not in strength


def test_common_size_rows_borrow_their_statement_labels() -> None:
    """So a line renamed on the statement is renamed here too."""
    from app.modules import m12_assembler as m12

    income = {row.metric: row.label for row in m12._INCOME_ROWS}
    common = {row.metric: row.label for row in m12._COMMON_SIZE_INCOME_ROWS}

    assert common["common_size.income.net_income"] == income["income.net_income"]
    # A metric with no statement row of its own still gets a readable label
    # rather than a raw dotted path.
    assert common["common_size.income.operating_expenses"] == "Operating expenses"


def test_the_derived_amounts_behind_the_ratios_are_shown() -> None:
    """A ratio a reader cannot check is a ratio they have to trust.

    Net debt to EBITDA was reported without either figure appearing anywhere
    in the document, so the one number that would have exposed a wrong net
    debt was the one number not shown.
    """
    from app.modules import m12_assembler as m12

    index = _analysed_index()

    table = m12._table(
        "t", "Derived amounts", m12._DERIVED_ABSOLUTE_ROWS, index, [], unit_note=""
    )

    assert table is not None
    shown = {row.label for row in table.rows}
    assert {"EBITDA", "Total debt", "Net debt"} <= shown


def test_a_line_the_filer_never_reported_gets_no_empty_row() -> None:
    """Twenty new optional metrics must not become twenty "Not disclosed" rows.

    A filer holding no goodwill is not withholding one, and `_table` drops a
    row nothing answered for — which is what makes it safe to widen the
    statement tables this far.
    """
    from app.modules import m12_assembler as m12

    index = _analysed_index()

    table = m12._table(
        "t", "Balance sheet", m12._BALANCE_ROWS, index, [], unit_note=""
    )

    assert table is not None
    assert len(table.rows) < len(m12._BALANCE_ROWS)
    assert "Goodwill" not in {row.label for row in table.rows}


def test_the_statements_stay_reported_only() -> None:
    """Derived amounts live in the analysis section, not the financials one.

    Section 3 is reported figures, and m06/m11 enforce the tier rule on it.
    Keeping calculated amounts out of those three tables is what keeps that
    section's own description true.
    """
    from app.modules import m12_assembler as m12

    statement_metrics = {
        row.metric
        for rows in (m12._INCOME_ROWS, m12._BALANCE_ROWS, m12._CASHFLOW_ROWS)
        for row in rows
    }

    assert not any(
        metric.startswith("derived.") for metric in statement_metrics
    )
    # Free cash flow is the documented exception: it is on the cash flow
    # statement's own terms, and already carried its DERIVED emphasis before
    # this table was widened.
    assert "cashflow.free_cash_flow" in statement_metrics


def test_the_trailing_twelve_months_renders_as_its_own_column() -> None:
    """Not as a fiscal year, because it is not one.

    The window ends at the filer's most recent quarter, which is the whole
    reason a reader wants it: an annual column can be ten months old while the
    price beside it is today's.
    """
    import datetime as dt

    from app.models import Fact as PlainFact
    from app.modules import m12_assembler as m12
    from tests.conftest import make_fact

    ttm: list[PlainFact] = [
        make_fact(
            metric="ttm.income.revenue",
            label="Revenue",
            value=451_442_000_000.0,
            display_value="451,442",
            period_start=dt.date(2025, 3, 30),
            period_end=dt.date(2026, 3, 28),
            fiscal_year=None,
            is_calculated=True,
            formula="the four quarters ended 2026-03-28",
        )
    ]
    index = _build_index(ttm)

    table = m12._ttm_table(index, [])

    assert table is not None
    assert table.periods == ("12 months to 2026-03-28",)
    assert len(table.rows) == 1
    assert table.rows[0].label == "Revenue"


def test_no_trailing_twelve_months_column_when_there_is_nothing_to_add() -> None:
    """A filer that has just closed its year already reports twelve months."""
    from app.modules import m12_assembler as m12

    assert m12._ttm_table(_analysed_index(), []) is None


def test_an_interim_figure_cannot_occupy_an_annual_column() -> None:
    """The reason interim facts carry their own metric prefix.

    m12 keys a cell by (metric, fiscal year), so an interim revenue sharing the
    annual metric name would collide with the full-year figure and silently
    overwrite it in the financial statements.
    """
    from app.config import QUARTERLY_METRIC_PREFIX, quarterly_metric
    from app.modules import m12_assembler as m12

    statement_metrics = {
        row.metric
        for rows in (m12._INCOME_ROWS, m12._BALANCE_ROWS, m12._CASHFLOW_ROWS)
        for row in rows
    }

    assert quarterly_metric("income.revenue") == "quarterly.income.revenue"
    assert not any(
        metric.startswith(QUARTERLY_METRIC_PREFIX) for metric in statement_metrics
    )
