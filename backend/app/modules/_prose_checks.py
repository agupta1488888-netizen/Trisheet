"""Shared prose-figure matching primitives.

Responsibility
    Extract numeric claims from a passage of prose and test them against
    stored `Fact` objects. Two callers share this mechanism and must not be
    able to disagree about what counts as a figure or what "sourced" means:
    m10 runs it as a self-check on a section before it ships, m11 runs it as
    the audit afterward. Both must see the same answer for the same input, so
    the matching lives here once rather than twice.

Purity
    No I/O, no network, no globals beyond the regex and scale tables built at
    import time from config.

Public interface
    Figure
    find_figures(text) -> list[Figure]
    supporting_facts(figure, facts) -> list[Fact]
    fact_numbers(fact) -> tuple[float, ...]
    agrees(stated, actual, decimals, scale) -> bool
    is_year_reference(figure, years) -> bool
    years_in(facts) -> frozenset[int]
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from app.config import (
    PROSE_FIGURE_TOLERANCE,
    PROSE_SCALE_WORDS,
    PROSE_YEAR_MAX,
    PROSE_YEAR_MIN,
)
from app.models import Fact

#: A figure in prose: an optional sign and currency, digits, an optional scale
#: word, an optional percent sign. Scale words come from config so that a
#: caller and this module cannot disagree about what "billion" multiplies by.
_SCALE_ALTERNATION = "|".join(sorted(PROSE_SCALE_WORDS, key=len, reverse=True))

_FIGURE_PATTERN = re.compile(
    r"""
    (?P<open>\()?                                   # accounting negative
    \s*
    (?P<sign>[-−])?
    \s*
    (?P<currency>[$€£¥])?
    \s*
    (?P<number>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)
    \s*
    (?P<scale>(?:"""
    + _SCALE_ALTERNATION
    + r""")\b)?
    \s*
    (?P<percent>%|percent\b|per\ cent\b)?
    (?P<close>\))?
    """,
    re.VERBOSE | re.IGNORECASE,
)

#: Month names, for masking dates written out in prose.
_MONTHS = (
    "January|February|March|April|May|June|July|August|September|October"
    "|November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec"
)

#: Text that contains digits but asserts no figure. Masked before the scan so
#: that a form name is never read as an unsourced number.
_NON_FIGURE_PATTERNS = (
    # Accession numbers, as filed.
    re.compile(r"\b\d{10}-\d{2}-\d{6}\b"),
    # Dates. A day of the month is not a figure about the business, and an
    # unmasked ISO date reads as three separate numbers. The day allows an
    # ordinal suffix ("June 30th") — prose writes dates that way, and the
    # digits are still not a figure either way.
    re.compile(r"\b\d{4}-\d{2}-\d{2}\b"),
    # A bare month-day, the form m08's fiscal-year-end notes render
    # ("06-30") and prose restates verbatim, often in parentheses. Without
    # this the figure scanner splits it at the dash: "(06-30)" produces an
    # unsourced "(06" fragment and a "30)" fragment that happens to agree
    # with an unrelated day-count elsewhere in the same sentence — a false
    # blocking violation on a sentence that cited its figures correctly.
    re.compile(r"\b\d{2}-\d{2}\b"),
    re.compile(
        rf"\b(?:{_MONTHS})\.?\s+\d{{1,2}}(?:st|nd|rd|th)?,?\s+\d{{4}}\b",
        re.IGNORECASE,
    ),
    re.compile(
        rf"\b\d{{1,2}}(?:st|nd|rd|th)?\s+(?:{_MONTHS})\.?\s+\d{{4}}\b",
        re.IGNORECASE,
    ),
    re.compile(
        rf"\b(?:{_MONTHS})\.?\s+\d{{1,2}}(?:st|nd|rd|th)?\b", re.IGNORECASE
    ),
    # Clock times ("2:00 p.m."). Not a figure about the business.
    re.compile(r"\b\d{1,2}:\d{2}\s*(?:[ap]\.?m\.?)?\b", re.IGNORECASE),
    # Form names: 10-K, 10-Q, 8-K, 20-F, 40-F, 6-K, DEF 14A, S-1.
    re.compile(r"\b(?:DEF\s+14A|[A-Z]?\d{1,2}-[A-Z]{1,2}(?:/A)?)\b"),
    # 8-K item numbers, which look like decimals.
    re.compile(r"\bitems?\s+\d{1,2}\.\d{2}\b", re.IGNORECASE),
    # Quarters and fiscal-year labels.
    re.compile(r"\bQ[1-4]\b", re.IGNORECASE),
    re.compile(r"\bFY\s?\d{2,4}\b", re.IGNORECASE),
    # A trading-range window ("52-week"). Not a figure about the business.
    re.compile(r"\b\d{1,3}-week\b", re.IGNORECASE),
    # Section references in the interface's own voice.
    re.compile(r"\bsection\s+\d\b", re.IGNORECASE),
)

#: Numbers inside a fact's own display value, grouped or plain. Deliberately
#: simpler than `_FIGURE_PATTERN`: a display value was rendered by this system,
#: so it needs reading rather than interpreting.
_DISPLAY_NUMBER_PATTERN = re.compile(
    r"-?\d{1,3}(?:,\d{3})+(?:\.\d+)?|-?\d+(?:\.\d+)?"
)


@dataclass(frozen=True, slots=True)
class Figure:
    """One number found in prose, with everything needed to test it."""

    #: As written, for the violation message.
    text: str
    #: The number itself, sign and scale word applied.
    value: float
    #: Decimal places the prose used. Decides how tightly a fact must round.
    decimals: int
    #: Multiplier the scale word contributed. 1.0 when there was none.
    scale: float
    is_percent: bool

    def candidate_values(self) -> tuple[float, ...]:
        """Values a fact could carry and still be what this figure states.

        A percentage is stored as a percent number by m07, so "43.2%" is
        43.2 — but a filing that reports a rate as a decimal stores 0.432, and
        both readings are offered rather than one being assumed.
        """
        if self.is_percent:
            return (self.value, self.value / 100.0)
        return (self.value,)


def _mask_non_figures(text: str) -> str:
    """Blanks out text that carries digits but asserts no figure.

    Replacement preserves length so that offsets stay meaningful, and uses
    spaces so that a masked form name cannot join two numbers together.
    """
    masked = text
    for pattern in _NON_FIGURE_PATTERNS:
        masked = pattern.sub(lambda match: " " * len(match.group(0)), masked)
    return masked


def find_figures(text: str) -> list[Figure]:
    """Every numeric claim in a passage of prose.

    Public because it decides what a caller examines, and a scanner that
    cannot be exercised directly is one nobody can trust.
    """
    figures: list[Figure] = []

    for match in _FIGURE_PATTERN.finditer(_mask_non_figures(text)):
        raw = match.group("number")
        try:
            magnitude = float(raw.replace(",", ""))
        except ValueError:
            continue

        scale_word = (match.group("scale") or "").lower()
        scale = PROSE_SCALE_WORDS.get(scale_word, 1.0)
        negative = bool(match.group("sign")) or bool(match.group("open"))

        value = magnitude * scale * (-1.0 if negative else 1.0)
        decimals = len(raw.split(".")[1]) if "." in raw else 0

        figures.append(
            Figure(
                text=match.group(0).strip(),
                value=value,
                decimals=decimals,
                scale=scale,
                is_percent=bool(match.group("percent")),
            )
        )

    return figures


# --- Matching ---------------------------------------------------------------


def _numbers_in(display_value: str) -> list[float]:
    """Numbers a fact's own display value contains.

    A fact renders as "391,035" or "43.2%" or "1.85x", and the writer is told
    to reproduce the display value verbatim — so what the display value says is
    the primary thing a figure in prose is checked against.
    """
    numbers: list[float] = []
    for match in _DISPLAY_NUMBER_PATTERN.finditer(display_value):
        try:
            numbers.append(float(match.group(0).replace(",", "")))
        except ValueError:
            continue
    return numbers


def fact_numbers(fact: Fact) -> tuple[float, ...]:
    """Every number a fact can legitimately be quoted as.

    A negative fact — an outflow, a decrease — is offered at its magnitude as
    well as its signed value. Prose conventionally carries the direction in
    the verb ("cash used was 488,000,000", "outflows narrowed to...") rather
    than repeating a literal minus sign, and both are faithful statements of
    the same fact.
    """
    numbers: list[float] = []
    if fact.value is not None:
        numbers.append(fact.value)
    numbers.extend(_numbers_in(fact.display_value))
    numbers.extend(-n for n in numbers if n < 0)
    return tuple(numbers)


def agrees(stated: float, actual: float, decimals: int, scale: float) -> bool:
    """True when a figure in prose is a faithful statement of a value.

    The rule is correct rounding: the value, rounded to the precision the
    prose used, must be the number the prose printed. This is what makes
    "$391.0 billion" a true statement about 391,035,000,000 while "$392
    billion" is not — the second does not round back, so it is a restatement
    rather than a quotation.

    The relative test that precedes it absorbs floating-point representation
    error on a value that has been through a division. It is not a second,
    looser standard.
    """
    if math.isclose(stated, actual, rel_tol=PROSE_FIGURE_TOLERANCE, abs_tol=1e-9):
        return True

    half_step = 0.5 * (10.0**-decimals) * scale
    return abs(stated - actual) <= half_step


def supporting_facts(figure: Figure, facts: Iterable[Fact]) -> list[Fact]:
    """Facts that would make this figure a true statement."""
    supporting: list[Fact] = []
    for fact in facts:
        for actual in fact_numbers(fact):
            if any(
                agrees(stated, actual, figure.decimals, figure.scale)
                for stated in figure.candidate_values()
            ):
                supporting.append(fact)
                break
    return supporting


def is_year_reference(figure: Figure, years: frozenset[int]) -> bool:
    """True when a bare four-digit number is a year the report covers.

    Only years the facts themselves carry are allowed. A number that merely
    looks like a year but belongs to no period in the report is treated as a
    figure and must be sourced like any other.
    """
    if figure.decimals or figure.is_percent or figure.scale != 1.0:
        return False
    if not float(figure.value).is_integer():
        return False
    year = int(figure.value)
    if not PROSE_YEAR_MIN <= year <= PROSE_YEAR_MAX:
        return False
    return year in years


def years_in(facts: Sequence[Fact]) -> frozenset[int]:
    """Every year the report's facts refer to."""
    years: set[int] = set()
    for fact in facts:
        if fact.fiscal_year is not None:
            years.add(fact.fiscal_year)
        years.add(fact.period_end.year)
        years.add(fact.filed_date.year)
        if fact.period_start is not None:
            years.add(fact.period_start.year)
    return frozenset(years)
