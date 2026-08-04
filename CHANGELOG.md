# Changelog

All notable changes to Trisheet are recorded here.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versioning follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## Conventions

- Every working increment gets an entry, added in the same commit as the change.
- Entries group under: Added, Changed, Deprecated, Removed, Fixed, Security.
- Each entry names the affected module scope in the same form as the commit
  message, e.g. `m03`, `edgar`, `frontend/report`.
- Unreleased work accumulates under `[Unreleased]` and is promoted to a dated
  version heading at release.
- Dates are ISO 8601 (YYYY-MM-DD).

## [Unreleased]

### Added — the report surfaces what the backend already produced (2026-08-04)

- `frontend/report`: a download control. The backend has rendered a PDF and an
  XLSX for every completed report and served them from
  `/reports/{id}/artifacts/{kind}` all along; nothing in the interface offered
  them, so a reader who wanted the document on paper had no way to get it.
  Built-but-unpublished and never-built each render their stated reason rather
  than a dead button.
- `frontend/report`: the timeline shows the result sentences m09 has been
  parsing out of each EX-99.1 earnings release, with guidance held apart and
  labelled as a projection. "Item 2.02" told a reader that results were
  reported; it did not tell them what the results were.
- `frontend/report`: the masthead carries exchange, headquarters, state of
  incorporation, employees and fiscal year end, each rendering "Not disclosed"
  when EDGAR does not state it.
- `frontend/report`: each risk shows its category; the compliance strip shows
  every reconciliation m11 ran with its tolerance, and states the source
  hierarchy rather than leaving it to be inferred from the tier counts.
- `frontend/format`: `formatBytes` and `formatFiscalYearEnd`.

### Security — report creation is rate limited (2026-08-04)

- `main`: a per-caller rolling limit on `POST /reports`, which had none. The
  endpoint has no authentication and a report costs dozens of rate-limited
  EDGAR reads and several model calls, so one unbounded caller degraded the
  service for everyone — the EDGAR token bucket makes concurrent callers wait
  rather than dropping them — while spending model budget doing it.
- A refused attempt is deliberately not recorded, so a caller who keeps
  retrying is not locked out for longer the harder they try. Tracked callers
  are bounded, so the limiter cannot become a memory leak with an unbounded
  key space. The clock is injected, so the window boundary is tested exactly
  rather than slept through.
- In process, not in the database, and the reason is written down: the
  deployment is a single backend instance because the EDGAR limiter requires
  one. Scaling out means moving both to a shared store, together.

### Added — the printed profile has a cover (2026-08-04)

- `m12`: the PDF opens on a title page — brand mark, company, ticker, listing
  and the one sentence stating what every figure in the document rests on.
  The mark is inline SVG rather than an image: WeasyPrint renders with no
  network access, so an external asset would have failed silently and left a
  hole in the artifact that actually gets handed over.
- `m12`: a running header on every page after the cover, carrying the company
  name via `string-set` off the masthead heading, with the cover suppressing
  it so the name is not repeated three lines below itself.
- `m12`: a contents page. Sections that could not be built are listed with
  their reason rather than omitted — a contents page that silently skips one
  implies the profile was never meant to have it.
- `m12`: the sources appendix states the tier hierarchy and the rule it
  enforces, since a printed profile cannot be read alongside the codebase.
- `m12`: widow and orphan control, headings that do not strand at a page foot,
  and source rows that do not split across a break.

### Added — the valuation set, risk categories, and the written record (2026-08-04)

- `m08`: enterprise value, EV to sales and dividend yield join price to
  earnings and EV to EBITDA. All five now share one definition of enterprise
  value (`_enterprise_value_of`), so it cannot drift between them. Valuation
  figures render by kind — a multiple as "18.40x", enterprise value as scaled
  currency, a yield as a percentage — rather than every figure as a multiple.
- `m12`: the peer comparison table carries the valuation rows it was already
  computing and sending only to the chart. The snapshot gains a valuation
  table built from the subject's own comparison row, which exists even when
  the peer ladder found nobody.
- `m12`: each disclosed risk carries a category derived from the filer's own
  heading, and the risks section shows the computed liquidity and leverage
  figures beside the disclosures. The category says what a risk is about and
  nothing more — there is no probability, impact score or severity anywhere,
  because a filing states none of them.
- `docs`: architecture, sourcing-and-validation and deployment, covering the
  module pipeline and its failure behaviour, tier enforcement and the
  reconciliation tolerances, and the deployed topology with the EDGAR rate
  constraint. The Tier 2 position is stated plainly rather than left implicit.

### Fixed — narrative items are searched down a ladder (2026-08-04)

- `m04`: each narrative item is now located down an ordered ladder rather than
  at one address. The item's own heading is tried first, then the punctuation
  and wording variants the spec declares, then — for anything still missing —
  the filing's exhibits. A 40-F carries its annual information form as an
  exhibit and some 10-K filers carry Item 1 the same way, so a primary
  document that lacks the item is not evidence the filing does. A rung costs
  only itself; exhausting them all costs the section, never the report.
- `m04`: a document over the parse ceiling is windowed rather than abandoned.
  It previously returned nothing at all, which cost every narrative item on
  filers whose annual report happened to be large. Narrative items sit in the
  front matter, ahead of the financial statements that make these documents
  large, so the leading window is where they are. The cut is logged.
- `m04`/`pipeline`: m04 now receives the `FilingRef` manifest rather than the
  flattened filings, because only `FilingRef` carries exhibits. m02 is asked
  for exhibits whenever narrative or developments is on.
- `config`: `NarrativeFallback`, and fallback rungs on all eight specs —
  including a business description spec for 40-F filers, which previously had
  none at all.
- `config`: `allow_model_peers` is on at standard depth. The rung proposes
  names only; each is still resolved to a CIK and read from its own filings,
  so provenance is unchanged. Off, it left filers whose proxy names no
  compensation peer group with no comparables at all.
- `config`: the snapshot brief asks for an investment snapshot rather than two
  or three sentences. Prompt text only — m11 still gates every figure.
- `models`/`types`: `Company` carries headquarters, exchange, state of
  incorporation and employees; `RiskCategory` and `RiskItem.category` classify
  a filer's own heading without rating it; the browser's types now declare
  `CheckResult`, `Violation` and `ArtifactRef`, all of which the backend was
  already serialising and the frontend was discarding.

### Added — the pipeline, m03 through m12 (2026-07-30)

The full run: a ticker in, a verified report out, over real EDGAR data.

- `models`/`config`/`schema`: widened for every module below — segmented and
  calculated `Fact`, `GeneratedReport`/`ComplianceReport`/`Violation` for what
  m10 writes and m11 verifies, `Peer`/`DevelopmentEvent`/`ArtifactRef` for
  peers, the timeline and assembly, and the `Report`/`ProgressStep` pair the
  frontend polls. Every module's tag ladders, tolerances and provider chains
  live in `config` rather than as literals in the modules that use them.
- `m03`: financial statements via ordered XBRL tag ladders, us-gaap first then
  ifrs-full, confidence charged per fallback rung. Deduplicates on (start, end,
  form) keeping the latest filed date; an amendment wins over the original it
  amends. Segments read from dimensional contexts, with cross-tabulated
  contexts excluded rather than mistaken for one. A metric no tag answers for
  is emitted as `NOT_DISCLOSED`, never omitted.
- `m04`: narrative sections (business description, risk factors, operating
  review), quoted rather than summarised, so the writer is shown the filer's
  own words instead of being asked to recall them.
- `m05`: market data behind a provider chain (yahoo, then stooq) — the one
  module permitted to import a market client. Every emitted metric must carry
  a section-5 prefix or it produces no fact at all; m06 and m11 each
  independently refuse a Tier 3 fact in section 3 as well.
- `m06`: the provenance write gate, enforced independently of whether storage
  is reachable, plus the real Supabase persistence behind it. A database
  outage costs `persisted=False`, never a fact.
- `m07`: derived metrics — growth, CAGR, margins, ratios, sector-specific
  groups by SIC code — as pure Python. No I/O, no LLM, no globals. Every
  derived fact carries its formula.
- `m08`: peer selection by a ladder of decreasing authority: the DEF 14A
  compensation peer group, the competition discussion, SIC match, the model as
  a last resort. Every candidate resolves to a live CIK before inclusion,
  including the model's own suggestions.
- `m09`: an 8-K/6-K developments timeline, filtered by item number. Earnings
  releases are read from their EX-99.1 exhibit; guidance is stored under its
  own prefixed metric so it can never be read downstream as a reported figure.
- `m10`: prose generated from `Fact` objects alone, output as schema-
  constrained JSON so sentences arrive with fact ids attached. A cited id
  absent from the supplied payload is dropped and logged.
- `m11`: the blocking verification gate — figures in prose checked against the
  facts they cite, balance sheet and segment-sum tie-outs to tolerance, Tier 3
  in section 3 failing the report outright regardless of whether m05/m06 could
  have produced it.
- `m12`: one `ReportDocument` rendered three ways (screen, PDF, XLSX), so a
  figure cannot differ across renderings. The workbook's derived cells are live
  Excel formulas over an assumptions tab, not baked-in numbers.
- `services`: `document` (HTML to text, shared by m04 and m09), `llm` (the only
  module that talks to the model), `storage` (Supabase Storage uploads),
  `metrics` (success rate, latency, cost — counted from run records, never
  estimated), `runlog` (the run's own status and step timing, distinct from
  the facts m06 stores about the company).
- `pipeline`: orchestrates m01 through m12 as one tracked run. EDGAR is the
  only hard dependency; every later step degrades to SKIPPED/FAILED with a
  reason rather than taking the run down. Exposed via `cli.py`
  (`resolve|discover|report|matrix`) and the API in `main.py`.
- 196 new tests across m03, m06, m07, m11 and the logging fix below, run
  alongside the existing 55 from the EDGAR foundation phase — 251 total.

### Fixed

- `m03`: the balance sheet ties out against
  `StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest`,
  not the parent-only `StockholdersEquity` that was previously first on the
  ladder. Found by running live against ENB: `Liabilities + StockholdersEquity`
  was short of `Assets` by exactly `MinorityInterest` every year. Affects any
  filer carrying noncontrolling interests, not only ENB.
- `logging_config`: a `False` `exc_info` (a caller stating a caught failure
  does *not* need a traceback) was unpacked as if it were a real exception
  tuple, crashing the log call meant to record the failure. Now checked with
  `isinstance(..., tuple)`.

### Notes — live verification (2026-07-30)

Run against live EDGAR with no database or model configured, to confirm the
pipeline's degradation paths rather than just its happy path:

- NKE, TSM, CNI, JPM, ENB, SHOP — 6/6 completed and passed verification.
  Between them: a domestic 10-K, a 20-F, a 40-F, and ENB's CAD-reporting 10-K.
- Every run: `m06` skipped storage (`persisted=False`), `m10` skipped prose
  (no `ANTHROPIC_API_KEY`), `m12` skipped the PDF (no WeasyPrint system
  libraries on this machine) — three independent optional dependencies, all
  absent at once, and every report still completed and passed m11.
- ENB failed `balance_sheet` before the fix above and passed after, on the
  same live data, with no other change.

### Added — frontend (2026-07-30)

Built against typed fixtures, so every screen is verifiable before the pipeline
is reachable from the browser. Only the routes under `/preview` import
`lib/mock`; each of them renders a standing notice saying the figures are
placeholders.

- `frontend/types`: contracts for the report document — `AnalysisDepth`,
  `TickerSuggestion`, `ProgressStep`, the seven `SECTION_ORDER` sections,
  `FigureTable` / `ProseBlock` / `DevelopmentEvent` / `RiskItem`, the four chart
  series, and `ComplianceSummary`. Chart values arrive in their display scale
  and compliance counts arrive already counted, so the browser plots and
  formats but never derives a figure.
- `frontend/lib`: `provenance.ts` assigns a superscript marker per source in
  first-appearance order and joins facts to the filing manifest; `format.ts`
  turns final values into strings and scales nothing; `constants.ts` holds every
  literal the interface renders.
- `frontend/report`: the seven sections in brief order with the provenance rail
  beside them. Figures are right-aligned monospace with a marker that links to
  its reference card; pointing at a figure raises its card and pointing at a
  card raises every figure drawn from it. Derived figures carry a "calculated"
  label bearing the formula. A section that could not be built states why and
  the rest of the report is unaffected.
- `frontend/report`: the compliance strip — fact count, tier distribution,
  citation coverage and the verification verdict, rendered from counts m11
  supplies.
- `frontend/charts`: revenue and margin trend, segment mix, cash flow against
  capital expenditure, and peer valuation, on Recharts 3. Colours come from CSS
  variables so charts follow the palette; animation is off; `--flag` is kept out
  of the categorical ramp so it keeps meaning conflict rather than decoration.
- `frontend/input`: a combobox following the ARIA pattern with debounced,
  race-safe suggestions; a radio-group depth selector; four example chips
  spanning a 10-K, a 20-F and a 40-F filer; and the disambiguation surface,
  which asks rather than guessing when the resolver returns candidates.
- `frontend/progress`: a monospace step feed driven by `run_logs` over Supabase
  Realtime, correlated by `report_id`, reporting the real count each step
  produced. Realtime is not a dependency — the hook falls back to polling and
  the screen says which mode it is in. A skipped optional source reads as a
  skip, not a failure.
- `frontend`: skip link, a single visible focus treatment, a blanket
  `prefers-reduced-motion` rule, and layouts that reflow to a narrow screen —
  where the rail becomes a docked panel rather than a footer.
- `frontend/preview`: fixture harness at `/preview` covering the input screen,
  three progress runs and two reports — a dense domestic 10-K filer and a 20-F
  filer with market data unavailable, which exercises "Not disclosed" and the
  absent valuation chart.

### Changed

- `frontend/constants`: example tickers corrected against the live EDGAR
  verification below. ENB files a 10-K, not a 40-F, so CNI is the 40-F example
  and TSM the 20-F one.

### Added — EDGAR foundation (2026-07-29)

- `models`: `FilerType.CANADIAN` for 40-F filers under the Multijurisdictional
  Disclosure System, `Company.reporting_currency`, `Resolution` / `Candidate`
  for ambiguous input, and `FilingRef` / `ExhibitRef` for the manifest. Schema
  and `frontend/lib/types.ts` updated in step, both idempotently.
- `edgar`: async httpx client enforcing the User-Agent, a process-wide token
  bucket at 10 req/s, retries on 429 and 5xx with exponential backoff and
  Retry-After taking precedence, and an on-disk cache that is permanent for
  filings and ttl-bound for the ticker index and XBRL concepts. Failures are
  typed; 404 is never retried. Cache hits bypass the limiter.
- `m01`: resolves a ticker or a company name. Ambiguous input returns
  candidates and resolves nothing; share classes of one filer still resolve.
  Filer type comes from the annual form most recently filed, so a company that
  migrated between form types is classified by what it files now, and a filer
  with no annual report is refused rather than defaulted. Reporting currency is
  read from the filer's own XBRL units, taking the code backing the most facts
  so a convenience translation cannot outvote the real one.
- `m02`: filing manifest with shard following, permitted-form filtering,
  amendment precedence, and EX-99.1 / EX-99.2 exhibits read from the index type
  column. Superseded filings stay reachable via `superseded_filings`.
  `as_filings` narrows the manifest to the `Filing` shape m03 and m04 take.
- `cli`: `python -m app.cli resolve|discover <query>`, writing to stdout so the
  ban on `print()` stays absolute in application code.
- 55 tests across the EDGAR client, m01 and m02, covering the limiter under
  concurrency, retry and cache behaviour, ambiguity, filer-type detection and
  amendment precedence.

### Notes — live verification (2026-07-29)

Run against live EDGAR. Results worth recording because two contradict the
brief:

- NKE — domestic, 10-K, USD, fiscal year ending 31 May. 419 filings after
  precedence, 32 annual reports, EX-99.1 resolved on real 8-K indexes.
- TSM — foreign, 20-F, TWD. 1117 filings, 1092 of them 6-K.
- SHOP — **domestic, 10-K, USD, not Canadian/40-F.** Shopify filed 20-F for
  2015, 40-F for 2016 through 2023, and has filed 10-K since. Classifying it
  by its latest annual form is correct; the 40-F expectation is out of date.
- CNI — canadian, 40-F, CAD. Added as the live check of the 40-F path that
  SHOP no longer provides.
- ENB — domestic, 10-K, **CAD**. Confirms filer type and reporting currency are
  independent: a 10-K filer need not report in USD.

TSM's monthly 6-K filings carry no EX-99.x exhibit — the content sits in the
primary document. Verified against raw indexes, so the empty exhibit list is
accurate rather than a parser miss.

### Added

- Project constitution in `CLAUDE.md`: non-negotiable architectural rules,
  stack, repo layout, SEC EDGAR client requirements, XBRL extraction rules,
  code standards, git conventions, design system, and definition of done.
- This changelog, with the conventions above.
- `frontend`: Next.js 15.5 App Router scaffold on TypeScript strict, Tailwind
  CSS 4 and shadcn/ui (Radix primitives). Fraunces, IBM Plex Sans and IBM Plex
  Mono wired through `next/font/google` and exposed as CSS variables. The
  Trisheet palette replaces shadcn's neutral ramp at the token level, radii
  are near-square, and `figure` / `ref` utilities carry the monospace
  tabular-nums treatment for all data.
- `frontend`: structural shells for the input screen and report view, plus
  `ProvenanceRail` — presentational only, no data loading yet.
- `frontend/lib`: `types.ts` (Fact contract mirroring the backend), `api.ts`
  (typed transport returning a discriminated result, never throwing on expected
  failure) and `supabase.ts` (browser client that degrades to null when
  unconfigured).
- `frontend`: ESLint rules banning `any`, non-null assertions and `console`
  outside warn/error; `noUncheckedIndexedAccess` enabled; `typecheck` script.
- `backend`: FastAPI application with `/health`, pydantic-settings
  configuration, CORS restricted to configured origins, and a catch-all handler
  that returns a typed `ApiError` rather than a stack trace.
- `backend`: structured single-line JSON logging correlated by `report_id` via
  a context variable, with uvicorn's loggers routed through it. No `print()`.
- `backend/models`: pydantic models including `Fact`, whose five provenance
  fields have no defaults — a fact without a source cannot be constructed.
- `backend`: documented stubs for modules m01–m12 and the edgar, llm and db
  services; no business logic yet.
- `backend`: pinned `requirements.txt` and `requirements-dev.txt`, and
  `pyproject.toml` configuring mypy strict, ruff (with `T20` banning `print`)
  and pytest.
- `db/schema.sql`: `companies`, `filings`, `facts`, `reports`, `market_cache`
  and `run_logs`. All five provenance columns on `facts` are NOT NULL, derived
  facts must carry a formula, display values cannot be blank, and row level
  security is enabled on every table with read-only browser policies and no
  client write path.
- `.env.example` documenting every variable, `.gitignore`, root `README.md`
  and `docs/`.

### Fixed

- `backend/deps`: numpy pinned to 2.4.6, not 2.5.1. numpy 2.5 requires Python
  3.12 and `CLAUDE.md` mandates 3.11, so the original pin was unresolvable.
  uvicorn corrected to 0.52.0.
- `backend/main`: the lifespan handler read process settings instead of the
  settings injected into `create_app`, so a test that injected an unconfigured
  EDGAR contact never exercised the warning path.
- `backend`: `StrEnum` and `datetime.UTC` adopted, clearing ruff `UP042`
  and `UP017`.

### Notes

- `backend/app/logging_config.py` and `backend/pyproject.toml` are additions to
  the layout in `CLAUDE.md`. Logging setup needs to be importable by every
  module rather than living in `config.py` or `main.py`, and mypy, ruff and
  pytest need a config file.
- The `.dark` palette in `globals.css` is derived, not specified. Light is the
  canonical mode; the block exists so a forced dark context degrades into the
  Trisheet palette inverted rather than into stock shadcn neutrals.
