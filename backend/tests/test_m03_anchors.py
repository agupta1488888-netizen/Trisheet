"""Tests for m03 anchoring — a figure's own position inside its filing.

The value of an anchor is that it is exact, so most of what follows is about
the cases where it cannot be. Pointing confidently at the wrong number is the
one failure that would discredit every other citation on the page, which is
why every ambiguity here resolves to "no anchor" rather than to a best guess.

The report never depends on any of this succeeding: an unanchored figure keeps
the document URL it already had.
"""

from __future__ import annotations

from app.models import ExtractionMethod, Fact
from app.modules import m03_financials as m03
from tests.conftest import (
    StubEdgarClient,
    make_company,
    make_fact,
    make_filing,
)

_FILING_DIR = (
    "https://www.sec.gov/Archives/edgar/data/320193/000032019324000123"
)

#: The rendered document the ids belong to, derived from the instance name.
_DOCUMENT = f"{_FILING_DIR}/aapl-20240928.htm"

#: An instance whose facts carry ids, as EDGAR's extracted inline instances do.
#: `f-total` and `f-note` are one figure tagged twice — in the statement and
#: again in a note — at the same value. `f-dup-a` and `f-dup-b` share a key and
#: disagree about what they report.
ANCHORED_INSTANCE = """<?xml version="1.0" encoding="UTF-8"?>
<xbrl xmlns="http://www.xbrl.org/2003/instance"
      xmlns:xbrldi="http://xbrl.org/2006/xbrldi"
      xmlns:us-gaap="http://fasb.org/us-gaap/2024">
  <unit id="usd"><measure>iso4217:USD</measure></unit>

  <context id="fy">
    <entity><identifier scheme="s">320193</identifier></entity>
    <period><startDate>2023-10-01</startDate><endDate>2024-09-28</endDate></period>
  </context>

  <context id="americas">
    <entity><identifier scheme="s">320193</identifier>
      <segment>
        <xbrldi:explicitMember dimension="us-gaap:ConsolidationItemsAxis"
          >us-gaap:OperatingSegmentsMember</xbrldi:explicitMember>
        <xbrldi:explicitMember dimension="us-gaap:StatementBusinessSegmentsAxis"
          >aapl:AmericasSegmentMember</xbrldi:explicitMember>
      </segment>
    </entity>
    <period><startDate>2023-10-01</startDate><endDate>2024-09-28</endDate></period>
  </context>

  <us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax
    contextRef="fy" unitRef="usd" id="f-total"
    >391035000000</us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax>
  <us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax
    contextRef="fy" unitRef="usd" id="f-note"
    >391035000000</us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax>
  <us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax
    contextRef="americas" unitRef="usd" id="f-americas"
    >167045000000</us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax>
  <us-gaap:GrossProfit contextRef="fy" unitRef="usd" id="f-dup-a"
    >180683000000</us-gaap:GrossProfit>
  <us-gaap:GrossProfit contextRef="fy" unitRef="usd" id="f-dup-b"
    >999000000</us-gaap:GrossProfit>
</xbrl>
"""


def _register(
    stub_edgar: StubEdgarClient,
    *,
    body: str = ANCHORED_INSTANCE,
    instance_name: str = "aapl-20240928_htm.xml",
) -> None:
    stub_edgar.register(
        f"{_FILING_DIR}/index.json",
        {"directory": {"item": [{"name": instance_name}]}},
    )
    stub_edgar.register(f"{_FILING_DIR}/{instance_name}", body)


async def _anchor(facts: list[Fact]) -> list[Fact]:
    return await m03.attach_anchors(make_company(), facts, [make_filing()])


# --- What it is for ---------------------------------------------------------


async def test_a_figure_is_anchored_to_the_element_it_came_from(
    stub_edgar: StubEdgarClient,
) -> None:
    """The whole point: the link addresses the number, not the document."""
    _register(stub_edgar)

    (fact,) = await _anchor([make_fact()])

    assert str(fact.anchor_url) == f"{_DOCUMENT}#f-total"


async def test_the_document_url_is_left_intact_beneath_the_anchor(
    stub_edgar: StubEdgarClient,
) -> None:
    """Anchoring adds a way in; it never rewrites the provenance under it."""
    _register(stub_edgar)
    original = make_fact()

    (fact,) = await _anchor([original])

    assert fact.source_url == original.source_url
    assert "#" not in str(fact.source_url)


async def test_a_segment_figure_anchors_to_its_own_dimensional_element(
    stub_edgar: StubEdgarClient,
) -> None:
    """A segment must not resolve to the consolidated element beside it."""
    _register(stub_edgar)

    (fact,) = await _anchor(
        [
            make_fact(
                metric="segment.revenue",
                value=167_045_000_000.0,
                display_value="167,045",
                segment_axis="us-gaap:StatementBusinessSegmentsAxis",
                segment_member="aapl:AmericasSegmentMember",
                segment_label="Americas",
                extraction_method=ExtractionMethod.XBRL_DIMENSIONAL,
            )
        ]
    )

    assert str(fact.anchor_url) == f"{_DOCUMENT}#f-americas"


async def test_a_consolidated_figure_does_not_anchor_to_a_segment(
    stub_edgar: StubEdgarClient,
) -> None:
    """The mirror of the above, and the more damaging way to get it wrong."""
    _register(stub_edgar)

    (fact,) = await _anchor([make_fact()])

    assert str(fact.anchor_url).endswith("#f-total")


# --- Where it declines to answer --------------------------------------------


async def test_a_figure_tagged_twice_at_one_value_takes_the_first(
    stub_edgar: StubEdgarClient,
) -> None:
    """Statement and note both show it, so either answers "where"."""
    _register(stub_edgar)

    (fact,) = await _anchor([make_fact()])

    assert str(fact.anchor_url).endswith("#f-total")


async def test_a_figure_whose_element_is_ambiguous_is_left_unanchored(
    stub_edgar: StubEdgarClient,
) -> None:
    """Two elements, one key, different values: nothing here can choose."""
    _register(stub_edgar)

    (fact,) = await _anchor(
        [
            make_fact(
                metric="income.gross_profit",
                value=180_683_000_000.0,
                resolved_tag="GrossProfit",
            )
        ]
    )

    assert fact.anchor_url is None


async def test_a_figure_that_disagrees_with_the_filing_is_not_anchored(
    stub_edgar: StubEdgarClient,
) -> None:
    """The value at the anchor must be the value being cited.

    A tag and period that match while the figure does not means the two are
    not the same fact, whatever the key says.
    """
    _register(stub_edgar)

    (fact,) = await _anchor([make_fact(value=123.0)])

    assert fact.anchor_url is None


async def test_a_filing_predating_inline_xbrl_is_not_anchored(
    stub_edgar: StubEdgarClient,
) -> None:
    """A standalone instance's ids appear in no document a reader can open."""
    _register(stub_edgar, instance_name="aapl-20240928.xml")

    (fact,) = await _anchor([make_fact()])

    assert fact.anchor_url is None


async def test_a_calculated_figure_is_never_anchored(
    stub_edgar: StubEdgarClient,
) -> None:
    """m07 produced it. It appears in no filing, so it has no position."""
    _register(stub_edgar)

    (fact,) = await _anchor(
        [make_fact(is_calculated=True, formula="gross profit / revenue")]
    )

    assert fact.anchor_url is None


async def test_a_not_disclosed_marker_is_never_anchored(
    stub_edgar: StubEdgarClient,
) -> None:
    """There is no value to find, so there is nowhere to point."""
    _register(stub_edgar)

    (fact,) = await _anchor(
        [
            make_fact(
                value=None,
                display_value="Not disclosed",
                extraction_method=ExtractionMethod.NOT_DISCLOSED,
                confidence=0.0,
            )
        ]
    )

    assert fact.anchor_url is None


async def test_a_fact_whose_filing_is_not_in_the_manifest_is_unanchored(
    stub_edgar: StubEdgarClient,
) -> None:
    """No filing to open, so no instance and no ids."""
    _register(stub_edgar)

    facts = await m03.attach_anchors(make_company(), [make_fact()], [])

    assert facts[0].anchor_url is None


# --- Degradation. Rule 6: anchors are lost, the report is not ---------------


async def test_an_unreadable_instance_costs_anchors_and_nothing_else(
    stub_edgar: StubEdgarClient,
) -> None:
    _register(stub_edgar, body="<not-xml")

    facts = await _anchor([make_fact()])

    assert len(facts) == 1
    assert facts[0].anchor_url is None
    assert facts[0].value == 391_035_000_000.0


async def test_a_filing_edgar_will_not_serve_costs_anchors_and_nothing_else(
    stub_edgar: StubEdgarClient,
) -> None:
    """Nothing registered at all: the fetch raises and is absorbed."""
    facts = await _anchor([make_fact()])

    assert len(facts) == 1
    assert facts[0].anchor_url is None


async def test_anchoring_returns_every_fact_it_was_given(
    stub_edgar: StubEdgarClient,
) -> None:
    """It enriches a list. It is not a filter, and never a lossy one."""
    _register(stub_edgar)
    given = [
        make_fact(),
        make_fact(is_calculated=True, formula="a / b"),
        make_fact(metric="market.price", value=1.0),
    ]

    returned = await _anchor(given)

    assert [fact.metric for fact in returned] == [
        fact.metric for fact in given
    ]


async def test_each_filing_is_opened_once_however_many_facts_cite_it(
    stub_edgar: StubEdgarClient,
) -> None:
    """Twenty figures from one 10-K are twenty anchors and one download."""
    _register(stub_edgar)

    await _anchor([make_fact() for _ in range(20)])

    fetches = [
        url for url in stub_edgar.requested if url.endswith("_htm.xml")
    ]
    assert len(fetches) == 1
