"""Tests for m04's item-location heuristic: `_locate` and its helpers.

`_locate` finds where one item of an annual report starts, in a document
with no machine-readable structure. Real headings are set apart from a
table-of-contents entry, a mid-sentence cross-reference, and a differently
scoped heading that merely contains the search phrase — the last of which
is what a live SAP 20-F triggered: its risk factors section is titled
"Risk Factors" in title case (never capitals), and a later, unrelated note
in the financial statements is titled "Financial Risk Factors and Risk
Management", which contains the same phrase and runs to the end of the
document with no further terminator after it. Before the isolated-heading
signal was added, the reverse-order, capitalisation-only heuristic picked
that later, wrong section because its trailing body trivially cleared the
minimum-length check.
"""

from __future__ import annotations

from app.config import (
    NARRATIVE_MIN_SECTION_CHARS,
    NarrativeFallback,
    NarrativeSpec,
)
from app.modules.m04_narrative import (
    _is_isolated_heading,
    _locate,
    _locate_down_ladder,
)

_RISK_SPEC = NarrativeSpec(
    metric="risk.factors",
    label="Risk factors",
    headings=("risk factors",),
    terminators=("item 4.",),
    forms=frozenset({"20-F"}),
)


def _pad(label: str, length: int) -> str:
    """A paragraph of prose, long enough on its own, naming `label` once."""
    filler = "Filler sentence about unrelated matters. "
    body = (filler * (length // len(filler) + 1))[:length]
    return f"{label} {body}"


def test_locate_prefers_an_isolated_heading_over_a_longer_lookalike() -> None:
    # Shaped after the live SAP 20-F: no heading is ever in capitals, a
    # cross-reference sits mid-sentence, and an unrelated note title
    # ("Financial Risk Factors and Risk Management") contains the bare
    # phrase and runs to the end of the document with nothing after it.
    toc = "16\n\nRisk Factors\n\n17\n\nItem 4. Information About SAP\n\n"
    cross_reference = (
        "We describe these and other risks and uncertainties in the "
        "Risk Factors section, and elsewhere in this report.\n\n"
    )
    real_heading = "Risk Factors\n\n" + _pad(
        "Our operations and financial results are subject to various "
        "risks and uncertainties.",
        NARRATIVE_MIN_SECTION_CHARS + 200,
    )
    terminator = "\n\nItem 4. Information about SAP - Products.\n\n"
    unrelated_note = "Financial Risk Factors and Risk Management\n\n" + _pad(
        "We use derivatives to hedge foreign currency risk.",
        NARRATIVE_MIN_SECTION_CHARS + 200,
    )

    text = toc + cross_reference + real_heading + terminator + unrelated_note

    located = _locate(text, _RISK_SPEC)

    assert located is not None
    assert located.startswith("Risk Factors")
    assert "Our operations and financial results" in located
    assert "hedge foreign currency risk" not in located


def test_locate_still_prefers_a_capitalised_heading_when_one_exists() -> None:
    # The NIKE/AAPL shape: a real ALL-CAPS heading exists, so the
    # capitalisation signal should still win outright, unaffected by the
    # isolated-heading fallback added for filers that never capitalise.
    spec = NarrativeSpec(
        metric="risk.factors",
        label="Risk factors",
        headings=("item 1a. risk factors",),
        terminators=("item 1b.",),
        forms=frozenset({"10-K"}),
    )
    toc = "Item 1A. Risk Factors\n\n17\n\nItem 1B.\n\n"
    real_heading = "ITEM 1A. RISK FACTORS\n\n" + _pad(
        "Our products, services and experiences face intense competition.",
        NARRATIVE_MIN_SECTION_CHARS + 200,
    )
    terminator = "\n\nItem 1B. Unresolved staff comments.\n\n"

    text = toc + real_heading + terminator

    located = _locate(text, spec)

    assert located is not None
    assert located.startswith("ITEM 1A. RISK FACTORS")


def test_locate_returns_none_when_the_item_is_absent() -> None:
    spec = NarrativeSpec(
        metric="risk.factors",
        label="Risk factors",
        headings=("item 1a. risk factors",),
        terminators=("item 1b.",),
        forms=frozenset({"10-K"}),
    )
    assert _locate("Nothing relevant here.", spec) is None


def test_is_isolated_heading_true_for_a_standalone_paragraph() -> None:
    text = "Some earlier text.\n\nRisk Factors\n\nSome later text."
    start = text.index("Risk Factors")
    assert _is_isolated_heading(text, start, len("Risk Factors")) is True


def test_is_isolated_heading_false_inside_a_longer_sentence() -> None:
    text = "We describe these risks in the Risk Factors section below."
    start = text.index("Risk Factors")
    assert _is_isolated_heading(text, start, len("Risk Factors")) is False


def test_is_isolated_heading_false_for_a_longer_containing_heading() -> None:
    text = "\n\nFinancial Risk Factors and Risk Management\n\nBody text."
    start = text.index("Risk Factors")
    assert _is_isolated_heading(text, start, len("Risk Factors")) is False


# --- The fallback ladder -----------------------------------------------------
# A single heading list turned ordinary filer variation into a missing
# section. These cover the ladder that replaced it: the preferred heading
# still wins outright, each fallback is consulted only after the one above it
# missed, and exhausting every rung is a clean miss rather than a wrong match.

_LADDER_SPEC = NarrativeSpec(
    metric="business.description",
    label="Business",
    headings=("item 1. business",),
    terminators=("item 1a.",),
    forms=frozenset({"10-K"}),
    fallbacks=(
        NarrativeFallback(
            headings=("item 1 — business",),
            terminators=("item 1a.",),
            reason="item 1 under a punctuation variant",
        ),
        NarrativeFallback(
            headings=("item 1.",),
            terminators=("item 1a.",),
            reason="item 1 by number alone",
        ),
    ),
)


def _item_body(heading: str) -> str:
    return f"{heading}\n\n" + _pad(
        "We design and sell athletic footwear and apparel.",
        NARRATIVE_MIN_SECTION_CHARS + 200,
    )


def test_ladder_uses_the_preferred_heading_when_it_matches() -> None:
    # A conventionally headed filing must cost exactly what it did before the
    # ladder existed: the first rung answers and nothing below it is tried.
    text = _item_body("ITEM 1. BUSINESS") + "\n\nITEM 1A. RISK FACTORS\n\n"

    located = _locate_down_ladder(text, _LADDER_SPEC)

    assert located is not None
    body, rung = located
    assert rung == "the item's own heading"
    assert "athletic footwear" in body


def test_ladder_falls_through_to_a_punctuation_variant() -> None:
    # The em-dash filer. The preferred heading never appears, so the first
    # fallback is what finds the item — and it names itself in the rung.
    text = _item_body("ITEM 1 — BUSINESS") + "\n\nITEM 1A. RISK FACTORS\n\n"

    located = _locate_down_ladder(text, _LADDER_SPEC)

    assert located is not None
    body, rung = located
    assert rung == "item 1 under a punctuation variant"
    assert "athletic footwear" in body


def test_ladder_falls_through_to_the_bare_item_number() -> None:
    # A filer that heads the item with its number and nothing else. Only the
    # last rung can reach this, and it must not have been reached earlier.
    text = _item_body("ITEM 1.") + "\n\nITEM 1A. RISK FACTORS\n\n"

    located = _locate_down_ladder(text, _LADDER_SPEC)

    assert located is not None
    _, rung = located
    assert rung == "item 1 by number alone"


def test_ladder_returns_none_when_every_rung_misses() -> None:
    # Exhausting the ladder is a miss, not a loose match on whatever is left.
    # This is what sends the item to the exhibit rung above it in m04.
    assert _locate_down_ladder("Nothing relevant here.", _LADDER_SPEC) is None


def test_ladder_does_not_match_a_cross_reference_on_its_last_rung() -> None:
    # The loosest rung is the one that could do damage: a bare "item 1."
    # appears in cross-references and contents lines too. The body-length and
    # isolated-heading tests are what keep it honest, and they still apply to
    # a fallback because a fallback is located by the same code path.
    text = (
        "See Item 1. Business for a description of our segments, and refer "
        "to the contents on page 3 for where each item begins.\n\n"
    )
    assert _locate_down_ladder(text, _LADDER_SPEC) is None
