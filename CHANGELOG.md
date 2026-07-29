# Changelog

All notable changes to Tearsheet are recorded here.

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
  Tearsheet palette replaces shadcn's neutral ramp at the token level, radii
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
  Tearsheet palette inverted rather than into stock shadcn neutrals.
