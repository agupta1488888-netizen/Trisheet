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
