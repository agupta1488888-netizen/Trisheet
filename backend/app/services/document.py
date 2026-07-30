"""Filing document text extraction.

Turns the HTML EDGAR serves into readable text, and readable text into
sentences. Two modules need this — m08 reads proxy and annual-report prose to
find peer names, m09 reads earnings releases — and neither should carry its
own parser.

What this is not
    This is not a fact source. Text that comes out of here is raw filing prose:
    it may be quoted, and it may be scanned for names, but nothing here
    produces a `Fact`. Provenance is attached by the module that calls this,
    which knows which filing the text came from.

Public interface
    html_to_text(markup) -> str
    split_sentences(text) -> list[str]
    windows_around(text, markers, radius) -> list[str]
"""

from __future__ import annotations

import logging
import re

from lxml import html

logger = logging.getLogger(__name__)

#: Elements whose text is markup, not prose.
_NON_PROSE_TAGS = ("script", "style", "head", "noscript")

#: Collapses the runs of whitespace that HTML-to-text conversion leaves behind.
_WHITESPACE = re.compile(r"[^\S\n]+")
_BLANK_LINES = re.compile(r"\n{3,}")

#: Sentence boundary: terminal punctuation, then whitespace, then a capital or
#: a digit. Abbreviations are guarded below rather than by the pattern, which
#: keeps the expression readable.
_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9$(])")

#: Abbreviations that end in a full stop mid-sentence. Splitting after one of
#: these produces a fragment, which then fails every length check downstream.
_ABBREVIATIONS = (
    "inc.",
    "corp.",
    "co.",
    "ltd.",
    "llc.",
    "l.p.",
    "u.s.",
    "u.k.",
    "no.",
    "mr.",
    "mrs.",
    "ms.",
    "dr.",
    "jr.",
    "sr.",
    "st.",
    "vs.",
    "approx.",
    "est.",
    "fig.",
)

#: Non-breaking and zero-width characters EDGAR documents are full of.
_INVISIBLES = str.maketrans(
    {
        "\xa0": " ",
        "​": "",
        "’": "'",
        "‘": "'",
        "“": '"',
        "”": '"',
        "–": "-",
        "—": "-",
    }
)


def html_to_text(markup: str) -> str:
    """Extracts readable text from a filing document.

    Returns an empty string rather than raising when the markup cannot be
    parsed. A document that will not parse costs the feature that wanted it,
    never the report.
    """
    if not markup.strip():
        return ""

    try:
        document = html.fromstring(markup)
    except (ValueError, TypeError, UnicodeDecodeError):
        logger.warning("Filing document could not be parsed as HTML")
        return ""

    for tag in _NON_PROSE_TAGS:
        for element in document.findall(f".//{tag}"):
            parent = element.getparent()
            if parent is not None:
                parent.remove(element)

    text = document.text_content()
    return normalise_whitespace(text)


def normalise_whitespace(text: str) -> str:
    """Collapses the whitespace HTML layout leaves in extracted text."""
    cleaned = text.translate(_INVISIBLES)
    cleaned = _WHITESPACE.sub(" ", cleaned)
    cleaned = _BLANK_LINES.sub("\n\n", cleaned)
    return cleaned.strip()


def split_sentences(text: str) -> list[str]:
    """Splits prose into sentences, keeping abbreviations intact.

    Deliberately simple. This feeds quotation and keyword matching, both of
    which tolerate an occasional over-long sentence; neither tolerates a
    dependency that has to be trained.
    """
    flattened = _WHITESPACE.sub(" ", text.replace("\n", " ")).strip()
    if not flattened:
        return []

    pieces = _SENTENCE_BOUNDARY.split(flattened)

    merged: list[str] = []
    for piece in pieces:
        if merged and _ends_with_abbreviation(merged[-1]):
            merged[-1] = f"{merged[-1]} {piece}"
            continue
        merged.append(piece)

    return [sentence.strip() for sentence in merged if sentence.strip()]


def _ends_with_abbreviation(sentence: str) -> bool:
    """True when a full stop was an abbreviation rather than a sentence end."""
    lowered = sentence.rstrip().lower()
    return any(lowered.endswith(abbreviation) for abbreviation in _ABBREVIATIONS)


def windows_around(
    text: str, markers: tuple[str, ...], radius: int
) -> list[str]:
    """Slices of `text` surrounding each marker phrase, in document order.

    Overlapping slices are merged, so a section that mentions "peer group"
    four times yields one window rather than four copies of the same prose.

    Args:
        text: The document text to search. Matched case-insensitively.
        markers: Phrases that mark the passage of interest.
        radius: Characters either side of a match to include.

    Returns:
        Merged text windows. Empty when no marker appears — which is a
        finding, not a failure: it means the filer did not discuss this.
    """
    if not text or radius <= 0:
        return []

    lowered = text.lower()
    spans: list[tuple[int, int]] = []

    for marker in markers:
        needle = marker.lower()
        start = lowered.find(needle)
        while start != -1:
            spans.append(
                (max(0, start - radius), min(len(text), start + len(needle) + radius))
            )
            start = lowered.find(needle, start + len(needle))

    if not spans:
        return []

    spans.sort()
    merged: list[tuple[int, int]] = [spans[0]]
    for start, end in spans[1:]:
        held_start, held_end = merged[-1]
        if start <= held_end:
            merged[-1] = (held_start, max(held_end, end))
        else:
            merged.append((start, end))

    return [text[start:end] for start, end in merged]
