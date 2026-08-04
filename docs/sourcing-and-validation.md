# Sourcing and validation

How the system finds sources, cites claims, handles missing and conflicting
information, and why it does not hallucinate figures.

The short version: **the model is never asked to produce a number.** By the
time it writes a sentence, every figure already exists, was extracted from a
named document, and was calculated in Python. That is the mechanism. Nothing
below is a prompt instruction.

## The source hierarchy

| Tier | What | Where it may be used |
|------|------|----------------------|
| 1 | SEC filings — 10-K, 10-Q, 8-K, DEF 14A, 20-F, 40-F, 6-K, and exhibits | Anywhere |
| 2 | Company website, investor presentations, press releases | Anywhere |
| 3 | Market data providers | Price, market cap and multiples **only** |
| 4 | News, general web | Never in financial highlights |

**Financial highlights accept Tier 1 and Tier 2 only.** Tier 3 and 4 are
hard-blocked with a logged rejection — `SECTION_3_ALLOWED_TIERS = (1, 2)`,
enforced at write time in `m06_factstore`, not at render time.

That rule is backed by a structural constraint rather than discipline:
**exactly one module may import a market data provider**, and that module is
`m05_market.py`. No other module can obtain a Tier 3 figure, so no other
module can leak one into a filings-only table. A stray `import yfinance`
anywhere else is a build-level defect, not a style violation.

Where a figure legitimately mixes tiers — a P/E needs a filing *and* a live
quote — the resulting fact takes the **highest (least trusted) tier among its
inputs**. A multiple built on a quote is only as sound as the quote, and the
interface labels it accordingly.

## How sources are found

1. `m01` resolves the ticker against the SEC's own company index and
   determines the filer type from the annual form actually filed.
2. `m02` builds a manifest of that filer's filings from the EDGAR submissions
   API — forms, periods, primary documents, exhibits.
3. Everything downstream draws **only** from that manifest. The admissible set
   is decided once, in one module.

No general web search is involved in producing a figure. A reader may attach a
link, which `m13` reads — but what comes back becomes a `SourceNote`, never a
`Fact`, and the two are rendered apart.

## How claims are cited

Every `Fact` carries five provenance fields with **no defaults**: tier, source
type, source URL, accession number, filed date. A fact that cannot name its
source raises at construction. The database declares the same five NOT NULL.
"Discarded at write time" is therefore a property of the type, not a check
someone has to remember.

Each fact also records **how** it came to exist — XBRL company facts, XBRL
dimensional, narrative, calculated, market data, or not-disclosed — and which
XBRL tag actually resolved. A reader who disagrees with a figure can tell
whether to distrust the extraction, the calculation, or the filing.

In the interface, every figure carries a superscript marker resolving to the
provenance rail: form type, accession number, filing date, link. In the PDF,
the same markers resolve to a sources appendix. Derived figures additionally
render the formula that produced them.

## How hallucination is prevented

**The model never performs arithmetic.** All calculation happens in
`m07_analysis` — pure functions, no I/O, no globals. The model receives
computed figures and writes prose about them.

**Sections are shown only their own facts.** Each section's prompt contains
the facts for that section and no others, so a sentence in the business
section cannot cite a figure it was never shown.

**Verification is blocking.** `m11` extracts every number from the generated
prose and resolves it against the fact store. A figure matches when it is a
correct rounding of a stored fact to the precision the prose used: "$391.0
billion" is a true statement about 391,035,000,000; "$392 billion" is not,
because it does not round back. Required coverage is **1.0** — not "most
figures are sourced", which is not the product. A report below that does not
render.

## How conflicting information is handled

Three reconciliations run on every report, each against a stated tolerance,
and each surfaced to the reader rather than kept in a log:

| Check | Tolerance | Why that number |
|---|---|---|
| Segments sum to consolidated revenue | 0.5% | Filings round segment tables independently of the income statement |
| Balance sheet balances | 0.5% | Assets − liabilities − equity, as a fraction of assets |
| Cash flow ties to the change in cash | 2.0% | The gap is the FX effect on cash, reported on its own line and not extracted as a metric — so this tolerance is deliberately wider, and for a stated reason |

Range bounds catch figures that are arithmetically consistent but impossible —
a 4000% gross margin from a unit error, a negative share count. They are
deliberately wide: a smoke alarm, not a judgement about the business.

Where two sources disagree on the same period, the deduplication rule in `m03`
decides: dedupe on (start, end, form), keep the latest filed date, and prefer
an amendment over the original it amends. A restatement wins over the figure
it restated, which is the correct answer rather than a convenient one.

## How missing information is handled

Missing data renders as **"Not disclosed"** — never "N/A", never blank, never
zero. The distinction matters: a segment absent from one period's table is a
segment not disclosed that period, which is not the same as a segment that
earned nothing.

Where an input to a calculation is missing, the calculation is **not
performed**. EV/EBITDA needs debt and cash as well as market capitalisation;
treating an undisclosed debt balance as zero would understate leverage while
looking like a complete answer. The multiple is absent instead.

Employee headcount is a worked example of the same principle. Most filers
state it in the text of Item 1 and never tag it in XBRL, so the field is
usually empty. Parsing the number out of that sentence was considered and
rejected — a figure lifted from prose is not a reported figure, and giving it
a Tier 1 citation would make it look like one.

## On Tier 2, stated plainly

The brief asks that company overview and segment sections prioritise the
company website and investor presentations, then general web. **Trisheet reads
those sections from the annual report only.**

The seam exists and is typed: `SourceTier.COMPANY`,
`SourceType.INVESTOR_PRESENTATION` and `SourceType.PRESS_RELEASE` are in the
model, and `services/webfetch.py` is a hardened fetcher — scheme allow-list,
every resolved address checked against private and loopback ranges, redirects
followed one hop at a time with the guard re-applied, response body bounded
while streaming. It is currently wired to the chat assistant and to `m13`
rather than to the report pipeline.

That is a scoping decision, not an oversight. SEC filings gave complete
coverage for every company tested, and a filing carries an accession number
and a filed date that an investor-relations page does not. Extending `m13`'s
ingestion into the overview and segment sections is the natural next step, and
the tier machinery to admit it correctly is already in place.
