"""m10 — prose generation.

Responsibility
    Hand the model a set of already-computed facts and ask it to write the
    narrative around them.

What reaches the prompt
    Fact objects, and nothing else. Every fact in the payload has already
    passed m06's provenance gate, so the prompt contains only figures that
    carry a tier, a source, an accession number and a filing date. No raw web
    text, no filing HTML, no market payload, no scraped page is serialised
    into a prompt anywhere in this module — `_render_facts` is the only thing
    that writes the fact table, and it reads fields off `Fact`.

    The one exception is the company's own identity — name, ticker, CIK,
    sector — which comes from m01's resolution of EDGAR's register. It is
    there so the model knows who it is writing about; it is not a figure, and
    nothing may be derived from it.

Constraints, all enforced here rather than hoped for
    - The model receives figures. It never computes, recalls or estimates one.
      The system prompt says so, and a self-check reruns the same
      figure-matching m11 audits with — scoped to exactly the facts this
      section was shown — before the section is considered final. A sentence
      that still states an unsupported figure after one retry is dropped
      here rather than shipped to the blocking gate, so a report is never
      failed outright by a defect this module can catch on its own facts.
    - It may only reference facts present in the payload it is given. A cited
      id that was not supplied is dropped here, with the drop logged.
    - Output is a JSON object constrained by a schema, so a section arrives as
      sentences with fact ids attached rather than as prose someone has to
      parse afterwards.
    - Sampling variance is held at or below the configured ceiling by
      `services.llm`.

Degradation
    A section the model cannot write becomes a section carrying the reason it
    is missing. Never a blank, never a stack trace, never a fabricated
    paragraph. Nothing in the public interface raises.

Public interface
    write_sections(company, facts) -> tuple[GeneratedReport, tuple[str, ...]]
    render_fact_table(facts) -> str
    build_user_prompt(company, section, facts) -> str
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from app.config import (
    LLM_MAX_FACTS_IN_PROMPT,
    LLM_WRITER_SELF_CHECK_MAX_RETRIES,
    NOT_DISCLOSED_TEXT,
    SECTION_METRIC_PREFIXES,
    WRITER_SECTIONS,
    WriterSection,
)
from app.models import (
    Company,
    Fact,
    GeneratedReport,
    GeneratedSection,
    GeneratedSentence,
)
from app.modules._prose_checks import (
    find_figures,
    is_year_reference,
    supporting_facts,
    years_in,
)
from app.services import llm

logger = logging.getLogger(__name__)

#: The shape every section answer must take. Constrained server-side, so the
#: parse below is a formality rather than a hope.
_SECTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "sentences": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "One complete sentence of prose.",
                    },
                    "fact_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Ids of the supplied facts this sentence draws on. "
                            "Required whenever the sentence states a figure."
                        ),
                    },
                },
                "required": ["text", "fact_ids"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["sentences"],
    "additionalProperties": False,
}

#: The rules. These are stated to the model, and every one of them is also
#: enforced in code somewhere — the prompt is a courtesy to the model, not the
#: mechanism. m11 is the mechanism.
SYSTEM_PROMPT = f"""\
You write equity research prose for a company profile. Every figure you are \
given has already been extracted from a filing and computed in Python.

Rules:

1. Use only the values supplied in the fact table. Never calculate, never \
recall a figure from memory, never estimate, never interpolate, never round \
beyond what the supplied display value already shows.
2. Write a figure exactly as its display value gives it. If a fact reads \
"391,035" you may write 391,035; you may not write "about 391 billion", \
"roughly 390,000" or any other restatement of the number.
3. If something is not in the table, it is not available. Write \
"{NOT_DISCLOSED_TEXT}" and move on. Never fill a gap.
4. Every sentence that states a figure must list the fact ids it draws on in \
its fact_ids array. A sentence that states no figure may have an empty array.
5. Never cite an id that is not in the table you were given.
6. A fact whose text is labelled as guidance is a forward-looking statement by \
the company. Describe it as guidance. Never present it as a reported result.
7. Sentence case. No headings, no bullet points, no markdown — plain \
sentences. Do not use the word "AI". Do not address the reader. Do not \
speculate about causes the filings do not state.
8. Be brief. This is a research document, not a summary of one.
"""


def _facts_for(section: WriterSection, facts: Sequence[Fact]) -> list[Fact]:
    """The facts a section is allowed to see.

    Membership is by metric prefix, the same rule m06 and m11 apply, so the
    three cannot disagree about what belongs where. A section is shown its own
    facts and no others — a sentence in the business section cannot cite a
    figure it was never given.
    """
    prefixes = tuple(
        prefix
        for number in section.metric_sections
        for prefix in SECTION_METRIC_PREFIXES.get(number, ())
    )
    if not prefixes:
        return []

    selected = [
        fact
        for fact in facts
        if any(fact.metric.startswith(prefix) for prefix in prefixes)
    ]
    selected.sort(key=lambda fact: (fact.metric, fact.period_end), reverse=False)
    return selected[:LLM_MAX_FACTS_IN_PROMPT]


def render_fact_table(facts: Sequence[Fact]) -> str:
    """Renders facts as the table the model is asked to write from.

    Every column is read off a `Fact`. The tier and the source travel with the
    figure so that the model is never in a position to treat a market quote as
    a filed number.

    Public for testing: what the model is shown decides what it can say, and
    that is worth being able to inspect without a network call.
    """
    lines = [
        "id | metric | label | period | value | tier | source",
        "-- | ------ | ----- | ------ | ----- | ---- | ------",
    ]
    for fact in facts:
        period = _period(fact)
        lines.append(
            " | ".join(
                (
                    fact.fact_id,
                    fact.metric,
                    fact.label,
                    period,
                    fact.display_value,
                    str(int(fact.tier)),
                    f"{fact.accession_no} filed {fact.filed_date.isoformat()}",
                )
            )
        )
    return "\n".join(lines)


def _period(fact: Fact) -> str:
    """How a fact's period reads in the table."""
    if fact.fiscal_year is not None:
        return f"FY{fact.fiscal_year}"
    if fact.period_start is not None:
        return f"{fact.period_start.isoformat()} to {fact.period_end.isoformat()}"
    return fact.period_end.isoformat()


def build_user_prompt(
    company: Company, section: WriterSection, facts: Sequence[Fact]
) -> str:
    """Assembles the user turn: who the company is, and the fact table.

    Public for testing. The assertion worth testing is a negative one — that
    nothing but identity and facts appears in what is sent.
    """
    identity = "\n".join(
        (
            f"Company: {company.name}",
            f"Ticker: {company.ticker or NOT_DISCLOSED_TEXT}",
            f"CIK: {company.cik}",
            f"Industry (SEC classification): {company.sector or NOT_DISCLOSED_TEXT}",
            f"Reporting currency: {company.reporting_currency or NOT_DISCLOSED_TEXT}",
        )
    )
    return (
        f"{identity}\n\n"
        f"Section to write: {section.title}\n"
        f"{section.brief}\n\n"
        f"Facts available for this section:\n{render_fact_table(facts)}\n"
    )


def _parse_sentences(
    answer: dict[str, Any], allowed: dict[str, Fact], section_id: str
) -> list[GeneratedSentence]:
    """Turns the model's answer into sentences, dropping unsupplied citations.

    A cited id that was not in the table is removed rather than trusted. The
    sentence survives; m11 then decides whether a sentence that has lost its
    citation still has the support its figures need.
    """
    raw = answer.get("sentences")
    if not isinstance(raw, list):
        return []

    sentences: list[GeneratedSentence] = []
    unknown: list[str] = []

    for item in raw:
        if not isinstance(item, dict):
            continue
        text = item.get("text")
        if not isinstance(text, str) or not text.strip():
            continue

        cited_raw = item.get("fact_ids")
        cited = cited_raw if isinstance(cited_raw, list) else []

        kept: list[str] = []
        for fact_id in cited:
            if not isinstance(fact_id, str):
                continue
            if fact_id in allowed:
                if fact_id not in kept:
                    kept.append(fact_id)
            else:
                unknown.append(fact_id)

        sentences.append(
            GeneratedSentence(text=text.strip(), fact_ids=tuple(kept))
        )

    if unknown:
        logger.warning(
            "Dropped citations to facts that were not supplied",
            extra={"section": section_id, "unknown_fact_ids": unknown},
        )

    return sentences


@dataclass(frozen=True, slots=True)
class _ProseIssue:
    """One sentence a self-check would also see m11 reject, and why."""

    index: int
    #: "unsourced" — no fact in this section's own table supports the figure.
    #: "uncited" — a fact does, but not one this sentence's own citations name.
    reason: str
    figure_text: str
    sentence_text: str


def _self_check(
    sentences: Sequence[GeneratedSentence], available: Sequence[Fact]
) -> list[_ProseIssue]:
    """Finds sentences a section's own facts cannot support.

    Reruns the identical figure-extraction and fact-matching m11 audits
    with — `find_figures`, `supporting_facts`, `is_year_reference` — but
    scoped to `available`, exactly what this section's prompt supplied.
    Because `available` is always a subset of the whole fact store m11 checks
    against, nothing flagged here could pass m11's own check either: this can
    only catch a problem earlier, never miss one m11 would also miss.
    """
    by_id = {fact.fact_id: fact for fact in available}
    years = years_in(available)
    issues: list[_ProseIssue] = []

    for index, sentence in enumerate(sentences):
        cited = [by_id[fid] for fid in sentence.fact_ids if fid in by_id]
        for figure in find_figures(sentence.text):
            if is_year_reference(figure, years):
                continue
            if not supporting_facts(figure, available):
                issues.append(
                    _ProseIssue(index, "unsourced", figure.text, sentence.text)
                )
                continue
            if not supporting_facts(figure, cited):
                issues.append(
                    _ProseIssue(index, "uncited", figure.text, sentence.text)
                )

    return issues


def _retry_prompt(
    base_prompt: str,
    sentences: Sequence[GeneratedSentence],
    issues: Sequence[_ProseIssue],
) -> str:
    """The user turn for a second attempt, naming exactly what was rejected.

    A fresh completion, not a diff — the base prompt is restated in full so
    the second attempt is not missing anything the first one had.
    """
    rejected = "\n".join(
        f'- "{sentences[issue.index].text}" — the figure "{issue.figure_text}" '
        + (
            "does not match any fact in the table above."
            if issue.reason == "unsourced"
            else "is not supported by the fact ids this sentence cited."
        )
        for issue in issues
    )
    return (
        f"{base_prompt}\n"
        "Your previous answer stated figures that could not be verified "
        "against the facts above. Rewrite the section from scratch without "
        "these sentences, citing only what the fact table actually "
        "supports:\n"
        f"{rejected}\n"
    )


def _strip_issues(
    sentences: Sequence[GeneratedSentence], issues: Sequence[_ProseIssue]
) -> list[GeneratedSentence]:
    """Drops the sentences a self-check could not clear, keeping the rest."""
    dropped = {issue.index for issue in issues}
    return [sentence for i, sentence in enumerate(sentences) if i not in dropped]


async def _write_section(
    company: Company, section: WriterSection, facts: Sequence[Fact]
) -> tuple[GeneratedSection, tuple[str, ...]]:
    """Writes one section, or states why there is none.

    Returns the section and any warning raised by the self-check stripping a
    sentence — empty in the ordinary case where nothing needed stripping.
    """
    available = _facts_for(section, facts)

    if not available:
        reason = (
            f"No sourced figures were found for {section.title.lower()}. "
            "The section is omitted rather than written without them."
        )
        logger.info(
            "Section has no facts; omitted",
            extra={"cik": company.cik, "section": section.section_id},
        )
        return (
            GeneratedSection(
                section_id=section.section_id,
                title=section.title,
                unavailable_reason=reason,
            ),
            (),
        )

    allowed = {fact.fact_id: fact for fact in available}
    prompt = build_user_prompt(company, section, available)

    sentences: list[GeneratedSentence] = []
    issues: list[_ProseIssue] = []

    for attempt in range(LLM_WRITER_SELF_CHECK_MAX_RETRIES + 1):
        try:
            answer = await llm.complete_json(
                SYSTEM_PROMPT,
                prompt,
                _SECTION_SCHEMA,
                purpose=f"write:{section.section_id}",
            )
        except llm.LlmError as cause:
            logger.warning(
                "Section could not be written",
                extra={
                    "cik": company.cik,
                    "section": section.section_id,
                    "error": str(cause),
                },
            )
            return (
                GeneratedSection(
                    section_id=section.section_id,
                    title=section.title,
                    unavailable_reason=(
                        "This section could not be generated. The figures "
                        "behind it are still available in the tables and the "
                        "provenance rail."
                    ),
                ),
                (),
            )

        sentences = _parse_sentences(answer, allowed, section.section_id)
        issues = _self_check(sentences, available)
        if not issues:
            break
        if attempt < LLM_WRITER_SELF_CHECK_MAX_RETRIES:
            logger.info(
                "Self-check found unsourced figures; retrying",
                extra={
                    "cik": company.cik,
                    "section": section.section_id,
                    "issues": len(issues),
                },
            )
            prompt = _retry_prompt(prompt, sentences, issues)

    warnings: tuple[str, ...] = ()
    if issues:
        sentences = _strip_issues(sentences, issues)
        logger.warning(
            "Self-check stripped ungrounded sentence(s) after the retry",
            extra={
                "cik": company.cik,
                "section": section.section_id,
                "stripped": len(issues),
            },
        )
        warnings = (
            f"{section.title}: {len(issues)} sentence(s) stripped — the "
            "model stated a figure this section's own facts do not support.",
        )

    if not sentences:
        return (
            GeneratedSection(
                section_id=section.section_id,
                title=section.title,
                unavailable_reason=(
                    "This section came back empty and was not written."
                ),
            ),
            warnings,
        )

    logger.info(
        "Section written",
        extra={
            "cik": company.cik,
            "section": section.section_id,
            "sentences": len(sentences),
            "facts_supplied": len(available),
            "citations": sum(len(s.fact_ids) for s in sentences),
        },
    )
    return (
        GeneratedSection(
            section_id=section.section_id,
            title=section.title,
            sentences=tuple(sentences),
        ),
        warnings,
    )


async def write_sections(
    company: Company, facts: Sequence[Fact]
) -> tuple[GeneratedReport, tuple[str, ...]]:
    """Generates prose for every report section, in report order.

    Args:
        company: The subject, for identity only.
        facts: Every stored fact for the report. Each section is shown the
            subset its metric prefixes select and nothing else.

    Returns:
        A `GeneratedReport` — a section that could not be written carries the
        reason instead of prose, and the report is still returned — plus any
        warnings raised when a section's self-check had to strip a sentence.
    """
    if not llm.is_configured():
        logger.warning(
            "No model configured; the report is assembled without prose",
            extra={"cik": company.cik},
        )
        return (
            GeneratedReport(
                sections=tuple(
                    GeneratedSection(
                        section_id=section.section_id,
                        title=section.title,
                        unavailable_reason=(
                            "Generated prose is unavailable. Every figure "
                            "below is still sourced and cited."
                        ),
                    )
                    for section in WRITER_SECTIONS
                )
            ),
            (),
        )

    # Concurrently, not in turn. Each section is shown its own facts and reads
    # nothing the others produce, so writing them one at a time bought nothing
    # and cost the report the sum of seven round trips rather than the longest
    # one. That is invisible while the model answers in a second and dominates
    # everything else when it does not: a provider slow enough to make one
    # section take fifty seconds made the report take six minutes.
    #
    # gather resolves in argument order, so sections stay in report order.
    # `_write_section` never raises — a section that could not be written
    # carries its reason — so one slow or refused section cannot cancel the
    # rest.
    results = list(
        await asyncio.gather(
            *(
                _write_section(company, section, facts)
                for section in WRITER_SECTIONS
            )
        )
    )
    sections = [section for section, _ in results]
    warnings = tuple(w for _, section_warnings in results for w in section_warnings)

    logger.info(
        "Prose generation complete",
        extra={
            "cik": company.cik,
            "sections_written": sum(
                1 for section in sections if section.sentences
            ),
            "sections_omitted": sum(
                1 for section in sections if section.unavailable_reason
            ),
            "self_check_warnings": len(warnings),
        },
    )
    return GeneratedReport(sections=tuple(sections)), warnings
