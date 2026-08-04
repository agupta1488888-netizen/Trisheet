"""Tests for m12's risk categorisation.

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

import pytest

from app.models import RiskCategory
from app.modules.m12_assembler import _risk_category


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
