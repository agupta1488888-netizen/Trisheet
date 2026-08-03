"""Tests for `document.html_to_text`: separator insertion at tag boundaries.

Inline-XBRL filings wrap short phrases in their own elements with no
whitespace between them in the source markup. `lxml.html.fromstring(...).
text_content()` concatenates every descendant text node with nothing between
them, so without an explicit separator step, adjacent tagged phrases fuse
into one word ("BUSINESSGENERAL") and risk-factor paragraphs lose the blank
lines `m04_narrative` relies on to split individual risk headings.
"""

from __future__ import annotations

from app.services.document import html_to_text


def test_inserts_space_between_adjacent_inline_elements() -> None:
    markup = (
        "<div><span>ITEM 1. BUSINESS</span><span>GENERAL</span>"
        "<span>NIKE, Inc. was incorporated in 1967.</span></div>"
    )
    text = html_to_text(markup)
    assert "BUSINESSGENERAL" not in text
    assert "GENERALNIKE" not in text
    assert text == (
        "ITEM 1. BUSINESS GENERAL NIKE, Inc. was incorporated in 1967."
    )


def test_inserts_paragraph_break_between_block_elements() -> None:
    markup = "<div><p>First risk heading.</p><p>Second risk heading.</p></div>"
    text = html_to_text(markup)
    assert "\n\n" in text
    paragraphs = text.split("\n\n")
    assert paragraphs == ["First risk heading.", "Second risk heading."]


def test_does_not_duplicate_existing_whitespace() -> None:
    markup = "<div><span>First</span> <span>Second</span></div>"
    text = html_to_text(markup)
    assert text == "First Second"


def test_table_cells_stay_separated_without_extra_blank_lines() -> None:
    markup = "<table><tr><td>Alpha Corp</td><td>Beta Inc</td></tr></table>"
    text = html_to_text(markup)
    assert "Alpha CorpBeta" not in text
    assert "\n\n\n" not in text


def test_empty_and_unparseable_markup_returns_empty_string() -> None:
    assert html_to_text("") == ""
    assert html_to_text("   ") == ""
