# Architecture

A ticker goes in. A verified, fully sourced company profile comes out.

Thirteen modules run in a fixed order. Each has one responsibility, a
documented public interface, and a defined behaviour when it fails. Only one
of them is allowed to fail the run.

```
ticker
  │
  ├─ m01 resolver ......... ticker → CIK, filer type, sector, identity
  ├─ m02 discovery ........ which filings this report may draw on
  │
  ├─ m03 financials ....... XBRL figures, ordered tag ladders
  ├─ m04 narrative ........ business, risk factors, MD&A
  ├─ m05 market ........... price and market cap  ← the only market module
  ├─ m09 developments ..... 8-K / 6-K timeline
  ├─ m13 sources .......... reader-supplied links, quoted not tabulated
  │
  ├─ m06 factstore ........ write gate: no provenance, no fact
  ├─ m07 analysis ......... all arithmetic, pure Python
  ├─ m08 peers ............ comparables, and the valuation multiples
  ├─ m10 writer ........... the model writes prose about computed figures
  ├─ m11 factcheck ........ blocking verification
  └─ m12 assembler ........ document, PDF, XLSX
        │
        ▼
  report + provenance rail
```

## What each module does

**m01 — resolver.** Turns whatever the reader typed into a filer. Matches
against the SEC's own ticker index, and refuses to guess: input matching more
than one entity returns `AMBIGUOUS` with candidates, and the interface asks.
Determines filer type from the annual form actually filed (10-K, 20-F, 40-F)
rather than from country or name, and reads identity — headquarters, exchange,
state of incorporation, employees — off the submissions document.
*Fails:* the run stops. Nothing downstream means anything without a CIK.

**m02 — discovery.** Builds the manifest: which filings exist, their forms,
periods, primary documents and exhibits. Everything downstream draws only from
this list, so the set of admissible sources is decided in one place.
*Fails:* the run stops.

**m03 — financials.** Extracts reported figures from XBRL. Never a single
hardcoded tag — each metric has an ordered ladder (revenue tries four tags in
turn), us-gaap first and ifrs-full behind it for foreign filers. Deduplicates
on (start, end, form), keeps the latest filed date, prefers an amendment over
the original. Records which tag actually resolved.
*Fails:* the run stops if no figures at all were extracted. A single missing
metric is emitted as `NOT_DISCLOSED`, never omitted.

**m04 — narrative.** The filer's own words: business description, risk
factors, MD&A. Each item is searched down a ladder — its own heading, then
punctuation and wording variants, then the filing's exhibits. Quoted, never
summarised, so m10 is shown the text rather than asked to recall it.
*Fails:* those sections say so. The report still renders from XBRL.

**m05 — market.** Price and market capitalisation. **The only module in the
codebase permitted to import a market data provider.** That is the physical
expression of the tier rule: market data cannot leak into a filings-only
section, because no other module can obtain it.
*Fails:* no valuation figures. Everything else renders.

**m09 — developments.** The 8-K / 6-K timeline. Reads the EX-99.1 earnings
release behind a results filing and separates reported results from guidance,
because a projection must never render as a booked figure.
*Fails:* no timeline.

**m13 — sources.** Reads links the reader supplied. Produces `SourceNote`,
which is deliberately not a `Fact` and cannot become one — a pasted page has
no accession number and nothing verifies it is the company's own site.
*Fails:* the links are reported as unread.

**m06 — factstore.** The write gate. A `Fact` has no default for any
provenance field, so one that cannot name its source cannot be constructed,
let alone stored. Tier enforcement happens here, in code.

**m07 — analysis.** Every calculation in the system. Pure functions, no I/O,
no globals. Margins, returns, liquidity, leverage, growth, segment mix,
sector-specific metrics (banks, REITs, insurers), and a DCF. Every derived
figure carries the formula that produced it.

**m08 — peers.** Comparables, down a four-rung ladder: the compensation peer
group named in the proxy, then the competition paragraph of the annual report,
then SIC-code neighbours, then a model proposal. The model only *proposes* a
name — it is still resolved to a CIK and its figures read from its own
filings. Each peer discloses which rung selected it. Also computes the
valuation multiples, since those need both filings and a live quote.
*Fails:* no comparables, with the reason stated.

**m10 — writer.** The model writes prose. It is shown computed figures and
the filer's own words, and asked to restate them. Each section's prompt
contains only that section's facts, so a sentence cannot cite a figure it was
never shown. **It never performs arithmetic.**

**m11 — factcheck.** The blocking gate. Every figure in generated prose must
resolve to a stored fact; coverage below 100% fails the report rather than
rendering it. Also reconciles segments against the consolidated total, the
balance sheet, and the cash flow statement, each against a stated tolerance.

**m12 — assembler.** Turns verified facts into the document, the PDF and the
XLSX. Contains no arithmetic beyond display scaling.

## Why it is shaped this way

**Extraction is separated from calculation, and calculation from writing.**
The model never sees a number it could get wrong, because by the time it is
involved every number already exists and is sourced. This is the single
largest reason the system does not hallucinate figures: it is not asked to
produce any.

**Degradation is designed, not incidental.** Only EDGAR is a hard dependency.
Market data, narrative, peers, developments and reader links each degrade to
absence with a stated reason. A report missing its peer table is a smaller
report, not a broken one.

**Provenance is a property of the type.** Not a convention, not a lint rule,
not a prompt instruction — a fact without a source cannot be constructed.

## Testing

`m07` and `m11` carry unit tests as a standing requirement, being the modules
where a silent error would be least visible. The suite covers the tag ladders,
the peer ladder, the narrative ladder, the verification tolerances and the
valuation arithmetic.
