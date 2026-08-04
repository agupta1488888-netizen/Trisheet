"""Tests for m11 — the blocking verification gate.

What this module refuses is the product. Every test below is a way a report
could be wrong that must not reach a reader, so each one asserts on the gate
holding rather than on the gate running.

The tier violation path is exercised in full: a Tier 3 market figure carrying
a section 3 metric must fail the report outright, and must do so even though
m05 cannot build such a fact and m06 would not store one. The audit does not
assume the controls worked.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import pytest

from app.config import (
    BALANCE_SHEET_TOLERANCE,
    CASH_FLOW_TIE_TOLERANCE,
    SEGMENT_METRIC,
    SEGMENT_SUM_TOLERANCE,
    CheckName,
)
from app.models import (
    CheckResult,
    ComplianceReport,
    ExtractionMethod,
    Fact,
    GeneratedReport,
    GeneratedSection,
    GeneratedSentence,
    Severity,
    SourceTier,
    SourceType,
)
from app.modules import m11_factcheck as m11
from tests.conftest import make_fact

VERIFIED_AT = dt.datetime(2026, 7, 30, 12, 0, tzinfo=dt.UTC)


# --- Builders ---------------------------------------------------------------


def section(
    *sentences: tuple[str, tuple[str, ...]],
    section_id: str = "financials",
) -> GeneratedSection:
    """A written section from (text, fact_ids) pairs."""
    return GeneratedSection(
        section_id=section_id,
        title=section_id.title(),
        sentences=tuple(
            GeneratedSentence(text=text, fact_ids=fact_ids)
            for text, fact_ids in sentences
        ),
    )


def report(*sections: GeneratedSection) -> GeneratedReport:
    return GeneratedReport(sections=tuple(sections))


def verify(prose: GeneratedReport, facts: list[Fact]) -> ComplianceReport:
    return m11.verify(prose, facts, verified_at=VERIFIED_AT)


def market_fact(**overrides: Any) -> Fact:
    """A Tier 3 market fact, correctly classified."""
    fields = {
        "metric": "market.price",
        "label": "Share price",
        "value": 212.44,
        "display_value": "212.44 USD",
        "unit": "USD",
        "period_start": None,
        "period_end": dt.date(2026, 7, 30),
        "fiscal_year": None,
        "tier": SourceTier.MARKET,
        "source_type": SourceType.MARKET_DATA,
        "source_url": "https://query1.finance.yahoo.com/v8/finance/chart/AAPL",
        "accession_no": "MARKET-YAHOO-20260730T120000Z",
        "filed_date": dt.date(2026, 7, 30),
        "extraction_method": ExtractionMethod.MARKET_DATA,
        "confidence": 1.0,
        "resolved_tag": None,
        "taxonomy": None,
    }
    fields.update(overrides)
    return Fact(**fields)


def balance_sheet(
    assets: float, liabilities: float, equity: float, *, end: dt.date
) -> list[Fact]:
    """A balance sheet as three facts for one instant."""
    return [
        make_fact(
            metric="balance.total_assets",
            label="Total assets",
            value=assets,
            display_value=f"{assets:,.0f}",
            period_start=None,
            period_end=end,
        ),
        make_fact(
            metric="balance.total_liabilities",
            label="Total liabilities",
            value=liabilities,
            display_value=f"{liabilities:,.0f}",
            period_start=None,
            period_end=end,
        ),
        make_fact(
            metric="balance.total_equity",
            label="Total equity",
            value=equity,
            display_value=f"{equity:,.0f}",
            period_start=None,
            period_end=end,
        ),
    ]


def check(result: ComplianceReport, name: str) -> CheckResult:
    """The named check out of a compliance report."""
    found = next((c for c in result.checks if c.check == name), None)
    assert found is not None, f"{name} was not run"
    return found


# --- The figure scanner -----------------------------------------------------


class TestFindFigures:
    """What counts as a numeric claim decides what the gate can catch."""

    def test_reads_a_scaled_currency_figure(self) -> None:
        (figure,) = m11.find_figures("Revenue was $391,035 million.")
        assert figure.value == pytest.approx(391_035_000_000.0)
        assert figure.scale == 1_000_000.0
        assert not figure.is_percent

    def test_reads_a_percentage(self) -> None:
        (figure,) = m11.find_figures("Gross margin was 46.2%.")
        assert figure.value == pytest.approx(46.2)
        assert figure.decimals == 1
        assert figure.is_percent

    def test_reads_an_accounting_negative(self) -> None:
        (figure,) = m11.find_figures("Free cash flow was (1,234).")
        assert figure.value == pytest.approx(-1234.0)

    @pytest.mark.parametrize(
        "text",
        [
            "The company filed an 8-K on 2025-06-26.",
            "On June 26, 2025 the company reported.",
            "Item 2.02 was furnished.",
            "Results for Q3 FY2025 were filed.",
            "Accession 0000320193-24-000123 carries the figures.",
            "Nike will report results on Tuesday, June 30th at 2:00 p.m.",
            "That price sits near the bottom of a 52-week range.",
            "Microsoft's fiscal year ends 06-30, unlike NIKE's 05-31.",
        ],
    )
    def test_ignores_text_that_carries_digits_but_states_no_figure(
        self, text: str
    ) -> None:
        assert m11.find_figures(text) == []

    def test_a_parenthesised_month_day_does_not_split_into_two_figures(
        self,
    ) -> None:
        # The shape m08's peer commentary actually produces: a real
        # day-count figure ("30 days") followed by the bare month-day it is
        # explaining, in parentheses. Before the month-day mask, this split
        # into an unsourced "(06" fragment and a "30)" fragment that
        # happened to agree with the day count — flagging a sentence that
        # had, in fact, cited its real figure correctly.
        text = "Microsoft's fiscal year differs by 30 days (06-30)."

        figures = m11.find_figures(text)

        assert [f.value for f in figures] == [30.0]


# --- Every number in the prose exists in the fact store ---------------------


class TestFiguresSourced:
    def test_a_figure_matching_a_fact_passes(self) -> None:
        fact = make_fact()
        prose = report(
            section(("Revenue was 391,035,000,000.", (fact.fact_id,)))
        )

        result = verify(prose, [fact])

        assert result.passed
        assert check(result, CheckName.FIGURES_SOURCED).passed

    def test_a_figure_no_fact_carries_fails_the_report(self) -> None:
        fact = make_fact()
        prose = report(
            section(("Revenue was 400,000,000,000.", (fact.fact_id,)))
        )

        result = verify(prose, [fact])

        assert not result.passed
        sourced = check(result, CheckName.FIGURES_SOURCED)
        assert not sourced.passed
        assert sourced.violations[0].severity is Severity.BLOCKING
        assert "400,000,000,000" in (sourced.violations[0].detail or "")

    def test_a_correctly_rounded_restatement_is_accepted(self) -> None:
        """391,035,000,000 rounds to $391.0 billion, so the prose is true."""
        fact = make_fact()
        prose = report(
            section(("Revenue was $391.0 billion.", (fact.fact_id,)))
        )

        assert verify(prose, [fact]).passed

    def test_a_wrongly_rounded_restatement_is_refused(self) -> None:
        """391,035,000,000 does not round to $392 billion."""
        fact = make_fact()
        prose = report(
            section(("Revenue was $392 billion.", (fact.fact_id,)))
        )

        result = verify(prose, [fact])

        assert not result.passed
        assert not check(result, CheckName.FIGURES_SOURCED).passed

    def test_an_outflow_stated_at_its_magnitude_is_accepted(self) -> None:
        """A cash outflow is a negative fact, but prose carries the direction
        in the verb rather than repeating the minus sign.

        Found live: m10 wrote "net cash used in investing activities was
        488,000,000" for a fact whose value was -488,000,000, and this failed
        figures_sourced before _fact_numbers offered the magnitude as well as
        the signed value.
        """
        fact = make_fact(
            metric="cashflow.investing",
            value=-488_000_000.0,
            display_value="-488,000,000",
        )
        prose = report(
            section((
                "Net cash used in investing activities was 488,000,000.",
                (fact.fact_id,),
            ))
        )

        result = verify(prose, [fact])

        assert result.passed
        assert check(result, CheckName.FIGURES_SOURCED).passed

    def test_a_year_the_facts_cover_is_not_treated_as_a_figure(self) -> None:
        fact = make_fact()
        prose = report(
            section(("The 2024 fiscal year closed in September.", ()))
        )

        result = verify(prose, [fact])

        assert result.passed
        assert check(result, CheckName.FIGURES_SOURCED).examined == 0

    def test_a_year_outside_the_report_is_treated_as_a_figure(self) -> None:
        """A number that merely looks like a year still has to be sourced."""
        fact = make_fact()
        prose = report(section(("Revenue reached 1987 in the period.", ())))

        assert not verify(prose, [fact]).passed

    def test_a_sentence_stating_no_figure_is_not_examined(self) -> None:
        fact = make_fact()
        prose = report(section(("The company sells consumer hardware.", ())))

        result = verify(prose, [fact])

        assert result.passed
        assert result.figure_count == 0
        assert result.coverage_display == "100%"


# --- Citation coverage ------------------------------------------------------


class TestCitationCoverage:
    def test_a_figure_whose_sentence_cites_nothing_fails(self) -> None:
        fact = make_fact()
        prose = report(section(("Revenue was 391,035,000,000.", ())))

        result = verify(prose, [fact])

        assert not result.passed
        coverage = check(result, CheckName.CITATION_COVERAGE)
        assert not coverage.passed
        assert result.coverage_ratio == 0.0
        assert result.coverage_display == "0%"

    def test_a_figure_citing_the_wrong_fact_fails(self) -> None:
        """Citing one fact while stating another's number is a mis-citation."""
        revenue = make_fact()
        assets = make_fact(
            metric="balance.total_assets",
            label="Total assets",
            value=364_980_000_000.0,
            display_value="364,980,000,000",
            period_start=None,
            period_end=dt.date(2024, 9, 28),
        )
        prose = report(
            section(("Revenue was 391,035,000,000.", (assets.fact_id,)))
        )

        result = verify(prose, [revenue, assets])

        assert not result.passed
        assert not check(result, CheckName.CITATION_COVERAGE).passed
        # The figure is sourced — it just is not the fact the sentence cited.
        assert check(result, CheckName.FIGURES_SOURCED).passed

    def test_a_citation_to_an_unknown_fact_fails(self) -> None:
        fact = make_fact()
        prose = report(
            section(("Revenue was 391,035,000,000.", (fact.fact_id, "fdeadbeef")))
        )

        result = verify(prose, [fact])

        assert not result.passed
        coverage = check(result, CheckName.CITATION_COVERAGE)
        assert any(
            "fdeadbeef" in (violation.detail or "")
            for violation in coverage.violations
        )

    def test_coverage_is_reported_as_a_ratio_and_a_display_string(self) -> None:
        revenue = make_fact()
        net_income = make_fact(
            metric="income.net_income",
            label="Net income",
            value=93_736_000_000.0,
            display_value="93,736,000,000",
        )
        prose = report(
            section(
                ("Revenue was 391,035,000,000.", (revenue.fact_id,)),
                ("Net income was 93,736,000,000.", ()),
            )
        )

        result = verify(prose, [revenue, net_income])

        assert result.figure_count == 2
        assert result.cited_figure_count == 1
        assert result.coverage_ratio == pytest.approx(0.5)
        assert result.coverage_display == "50%"


# --- The tier rule ----------------------------------------------------------


class TestSectionThreeTiers:
    """The rule the whole tier system exists to enforce."""

    def test_a_tier_3_fact_in_section_3_fails_the_report(self) -> None:
        """A market figure wearing a section 3 metric is a hard failure.

        m05 cannot construct this fact and m06 would refuse to store it. The
        gate still checks, because an audit that trusts its controls is not an
        audit.
        """
        smuggled = market_fact(
            metric="income.revenue",
            label="Revenue",
            value=391_035_000_000.0,
            display_value="391,035,000,000",
        )

        result = verify(report(), [smuggled])

        assert not result.passed
        tiers = check(result, CheckName.SECTION_3_TIERS)
        assert not tiers.passed
        violation = tiers.violations[0]
        assert violation.severity is Severity.BLOCKING
        assert violation.metric == "income.revenue"
        assert "tier 3" in violation.message
        assert "market_data" in (violation.detail or "")

    def test_a_tier_4_fact_in_section_3_fails_the_report(self) -> None:
        news = make_fact(
            metric="income.net_income",
            label="Net income",
            tier=SourceTier.NEWS,
            source_type=SourceType.NEWS,
            source_url="https://example.com/story",
            extraction_method=ExtractionMethod.NARRATIVE,
            resolved_tag=None,
            taxonomy=None,
        )

        result = verify(report(), [news])

        assert not result.passed
        assert not check(result, CheckName.SECTION_3_TIERS).passed

    def test_a_tier_3_fact_outside_section_3_is_allowed(self) -> None:
        """Market data belongs in the market section, and is fine there."""
        result = verify(report(), [market_fact()])

        assert result.passed
        tiers = check(result, CheckName.SECTION_3_TIERS)
        assert tiers.passed
        assert tiers.examined == 0

    def test_computed_metrics_inherit_the_section_3_tier_block(self) -> None:
        """A derived figure is only as sound as what it was derived from."""
        derived = market_fact(
            metric="margin.gross",
            label="Gross margin",
            value=46.2,
            display_value="46.2%",
            unit="percent",
        )

        result = verify(report(), [derived])

        assert not result.passed
        assert not check(result, CheckName.SECTION_3_TIERS).passed

    def test_tier_counts_are_reported_for_the_compliance_strip(self) -> None:
        result = verify(report(), [make_fact(), make_fact(metric="x.y"), market_fact()])

        assert result.tier_counts == {1: 2, 2: 0, 3: 1, 4: 0}
        assert result.fact_count == 3

    def test_tier_counts_include_tiers_with_no_facts(self) -> None:
        """A tier with zero facts reports 0 — the UI renders a missing key as "NaN"."""
        result = verify(report(), [make_fact()])

        assert result.tier_counts == {1: 1, 2: 0, 3: 0, 4: 0}


# --- Segment sum ------------------------------------------------------------


class TestSegmentSum:
    def _segments(self, *values: float) -> list[Fact]:
        return [
            make_fact(
                metric=SEGMENT_METRIC,
                label="Revenue by segment",
                value=value,
                display_value=f"{value:,.0f}",
                segment_axis="us-gaap:StatementBusinessSegmentsAxis",
                segment_member=f"tst:Segment{index}Member",
                segment_label=f"Segment {index}",
            )
            for index, value in enumerate(values)
        ]

    def test_segments_that_sum_to_revenue_pass(self) -> None:
        revenue = make_fact()
        segments = self._segments(200_000_000_000.0, 191_035_000_000.0)

        result = verify(report(), [revenue, *segments])

        assert result.passed
        assert check(result, CheckName.SEGMENT_SUM).examined == 1

    def test_segments_that_miss_revenue_are_flagged(self) -> None:
        revenue = make_fact()
        segments = self._segments(100_000_000_000.0, 100_000_000_000.0)

        result = verify(report(), [revenue, *segments])

        segment_check = check(result, CheckName.SEGMENT_SUM)
        assert not segment_check.passed
        # Advisory: an incomplete breakdown is disclosed, not fatal.
        assert segment_check.violations[0].severity is Severity.ADVISORY
        assert result.passed

    def test_the_tolerance_boundary_is_respected(self) -> None:
        """Rounding inside the tolerance passes; just outside it does not."""
        revenue = make_fact()
        total = revenue.value or 0.0

        inside = total * (1 + SEGMENT_SUM_TOLERANCE * 0.9)
        outside = total * (1 + SEGMENT_SUM_TOLERANCE * 1.1)

        assert check(
            verify(report(), [revenue, *self._segments(inside)]),
            CheckName.SEGMENT_SUM,
        ).passed
        assert not check(
            verify(report(), [revenue, *self._segments(outside)]),
            CheckName.SEGMENT_SUM,
        ).passed

    def test_no_segments_means_the_check_had_nothing_to_run_on(self) -> None:
        result = verify(report(), [make_fact()])

        segment_check = check(result, CheckName.SEGMENT_SUM)
        assert segment_check.passed
        assert not segment_check.applicable


# --- Balance sheet ----------------------------------------------------------


class TestBalanceSheet:
    END = dt.date(2024, 9, 28)

    def test_a_balance_sheet_that_balances_passes(self) -> None:
        facts = balance_sheet(365_000.0, 308_000.0, 57_000.0, end=self.END)

        result = verify(report(), facts)

        assert result.passed
        assert check(result, CheckName.BALANCE_SHEET).examined == 1

    def test_a_balance_sheet_that_does_not_balance_fails_the_report(self) -> None:
        facts = balance_sheet(365_000.0, 308_000.0, 20_000.0, end=self.END)

        result = verify(report(), facts)

        assert not result.passed
        balance = check(result, CheckName.BALANCE_SHEET)
        assert balance.violations[0].severity is Severity.BLOCKING
        assert "does not balance" in balance.violations[0].message

    def test_the_tolerance_boundary_is_respected(self) -> None:
        assets = 365_000.0
        inside = assets * (1 - BALANCE_SHEET_TOLERANCE * 0.9)
        outside = assets * (1 - BALANCE_SHEET_TOLERANCE * 1.1)

        assert check(
            verify(report(), balance_sheet(assets, inside, 0.0, end=self.END)),
            CheckName.BALANCE_SHEET,
        ).passed
        assert not check(
            verify(report(), balance_sheet(assets, outside, 0.0, end=self.END)),
            CheckName.BALANCE_SHEET,
        ).passed

    def test_a_missing_line_means_the_period_is_not_checked(self) -> None:
        facts = balance_sheet(365_000.0, 308_000.0, 57_000.0, end=self.END)

        result = verify(report(), facts[:2])

        assert check(result, CheckName.BALANCE_SHEET).examined == 0


# --- Cash flow tie ----------------------------------------------------------


class TestCashFlowTie:
    OPENING = dt.date(2023, 9, 30)
    CLOSING = dt.date(2024, 9, 28)

    def _facts(
        self, opening: float, closing: float, operating: float,
        investing: float, financing: float,
    ) -> list[Fact]:
        cash = [
            make_fact(
                metric="balance.cash_and_equivalents",
                label="Cash and cash equivalents",
                value=value,
                display_value=f"{value:,.0f}",
                period_start=None,
                period_end=end,
            )
            for value, end in ((opening, self.OPENING), (closing, self.CLOSING))
        ]
        flows = [
            make_fact(
                metric=metric,
                label=metric,
                value=value,
                display_value=f"{value:,.0f}",
                period_start=self.OPENING,
                period_end=self.CLOSING,
            )
            for metric, value in (
                ("cashflow.operating", operating),
                ("cashflow.investing", investing),
                ("cashflow.financing", financing),
            )
        ]
        return [*cash, *flows]

    def test_a_cash_flow_statement_that_ties_passes(self) -> None:
        facts = self._facts(30_000.0, 34_000.0, 118_000.0, -2_000.0, -112_000.0)

        result = verify(report(), facts)

        assert result.passed
        assert check(result, CheckName.CASH_FLOW_TIE).examined == 1

    def test_a_cash_flow_statement_that_does_not_tie_is_flagged(self) -> None:
        facts = self._facts(30_000.0, 90_000.0, 118_000.0, -2_000.0, -112_000.0)

        result = verify(report(), facts)

        tie = check(result, CheckName.CASH_FLOW_TIE)
        assert not tie.passed
        # Advisory: the gap is usually the exchange rate effect on cash held
        # abroad, which is not extracted as a metric.
        assert tie.violations[0].severity is Severity.ADVISORY
        assert result.passed

    def test_a_gap_inside_the_tolerance_passes(self) -> None:
        """The exchange rate effect on foreign cash lives in this tolerance."""
        sections_total = 4_000.0
        drift = sections_total * CASH_FLOW_TIE_TOLERANCE * 0.5
        facts = self._facts(
            30_000.0, 30_000.0 + sections_total + drift,
            118_000.0, -2_000.0, -112_000.0,
        )

        assert check(verify(report(), facts), CheckName.CASH_FLOW_TIE).passed


# --- Range sanity -----------------------------------------------------------


class TestRangeSanity:
    def test_a_negative_revenue_fails_the_report(self) -> None:
        fact = make_fact(value=-1.0, display_value="-1")

        result = verify(report(), [fact])

        assert not result.passed
        sanity = check(result, CheckName.RANGE_SANITY)
        assert sanity.violations[0].severity is Severity.BLOCKING
        assert "not negative" in sanity.violations[0].message

    def test_a_margin_above_one_hundred_percent_fails_the_report(self) -> None:
        fact = make_fact(
            metric="margin.gross",
            label="Gross margin",
            value=4_620.0,
            display_value="4620.0%",
            unit="percent",
        )

        result = verify(report(), [fact])

        assert not result.passed
        assert not check(result, CheckName.RANGE_SANITY).passed

    def test_a_plausible_margin_passes(self) -> None:
        fact = make_fact(
            metric="margin.gross",
            label="Gross margin",
            value=46.2,
            display_value="46.2%",
            unit="percent",
        )

        assert verify(report(), [fact]).passed

    def test_a_loss_making_margin_is_not_a_violation(self) -> None:
        """A pre-revenue filer's losses dwarf its sales. That is not an error."""
        fact = make_fact(
            metric="margin.net",
            label="Net margin",
            value=-820.0,
            display_value="-820.0%",
            unit="percent",
        )

        assert verify(report(), [fact]).passed

    def test_an_unbounded_metric_is_not_examined(self) -> None:
        fact = make_fact(
            metric="income.net_income",
            label="Net income",
            value=-5_000.0,
            display_value="-5,000",
        )

        result = verify(report(), [fact])

        assert result.passed
        assert check(result, CheckName.RANGE_SANITY).examined == 0

    def test_the_percent_unit_is_bounded_even_without_a_metric_rule(
        self,
    ) -> None:
        """A percentage metric added later is covered without a config edit."""
        fact = make_fact(
            metric="bank.net_interest_margin",
            label="Net interest margin",
            value=5_000_000.0,
            display_value="5000000.0%",
            unit="percent",
        )

        assert not verify(report(), [fact]).passed


# --- The report itself ------------------------------------------------------


class TestComplianceReport:
    def test_every_check_is_reported_whether_or_not_it_found_anything(
        self,
    ) -> None:
        result = verify(report(), [make_fact()])

        assert {c.check for c in result.checks} == {
            CheckName.FIGURES_SOURCED,
            CheckName.CITATION_COVERAGE,
            CheckName.SECTION_3_TIERS,
            CheckName.SEGMENT_SUM,
            CheckName.BALANCE_SHEET,
            CheckName.CASH_FLOW_TIE,
            CheckName.RANGE_SANITY,
        }

    def test_every_check_states_the_tolerance_it_applied(self) -> None:
        for result_check in verify(report(), [make_fact()]).checks:
            assert result_check.description.strip()

    def test_advisory_findings_do_not_withhold_the_report(self) -> None:
        revenue = make_fact()
        segment = make_fact(
            metric=SEGMENT_METRIC,
            label="Revenue by segment",
            value=1.0,
            display_value="1",
            segment_axis="us-gaap:StatementBusinessSegmentsAxis",
            segment_member="tst:OneMember",
        )

        result = verify(report(), [revenue, segment])

        assert result.violations
        assert not result.blocking_violations
        assert result.passed

    def test_a_blocking_finding_withholds_the_report(self) -> None:
        result = verify(report(), [make_fact(value=-1.0, display_value="-1")])

        assert result.blocking_violations
        assert not result.passed

    def test_the_timestamp_is_injectable(self) -> None:
        assert verify(report(), []).verified_at == VERIFIED_AT

    def test_an_unwritten_section_is_not_examined(self) -> None:
        """A section carrying its reason instead of prose has nothing to check."""
        prose = report(
            GeneratedSection(
                section_id="peers",
                title="Peers",
                unavailable_reason="No peer group was disclosed.",
            )
        )

        result = verify(prose, [make_fact()])

        assert result.passed
        assert result.figure_count == 0

    def test_an_empty_report_passes_with_full_coverage(self) -> None:
        result = verify(report(), [])

        assert result.passed
        assert result.coverage_ratio == 1.0
        assert result.coverage_display == "100%"
        assert result.fact_count == 0

    def test_verification_does_not_mutate_its_inputs(self) -> None:
        """The gate is a pure function of the prose and the facts."""
        facts = [make_fact(), market_fact()]
        prose = report(
            section(("Revenue was 391,035,000,000.", (facts[0].fact_id,)))
        )
        before = [fact.model_dump() for fact in facts]

        verify(prose, facts)

        assert [fact.model_dump() for fact in facts] == before
