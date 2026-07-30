# Trisheet

Company profiles, sourced from filings.

Trisheet generates equity-research-grade company profiles for US-listed
companies. Every financial figure traces back to an SEC filing. The product's
value is provenance, not automation.

## Non-negotiable rules

These are architectural constraints, not preferences. Violating any of them is
a defect regardless of whether tests pass.

1. THE LLM NEVER PERFORMS ARITHMETIC.
   All calculation happens in Python using pandas/numpy. The LLM receives
   computed figures and writes prose about them. It never recalls, estimates,
   or derives a number.

2. NO FACT EXISTS WITHOUT PROVENANCE.
   Every Fact carries: tier, source_type, source_url, accession_no, filed_date.
   A fact missing any of these is discarded at write time, not rendered with a
   blank.

3. SOURCE TIER ENFORCEMENT IS CODE, NOT PROMPTING.
   Tier 1 = SEC filings (10-K, 10-Q, 8-K, DEF 14A, 20-F, 40-F, 6-K + exhibits)
   Tier 2 = Company website, investor presentations, press releases
   Tier 3 = Market data providers (price, market cap, multiples ONLY)
   Tier 4 = News, general web

   Section 3 (financial highlights) accepts Tier 1 and 2 ONLY. Tier 3 and 4
   are hard-blocked in code with a logged rejection.

4. EXACTLY ONE MODULE MAY IMPORT A MARKET DATA PROVIDER.
   That module is backend/app/modules/m05_market.py. If any other file imports
   yfinance or a market API client, that is a bug. This constraint is the
   physical expression of rule 3.

5. NEVER FABRICATE, NEVER ESTIMATE.
   Missing data renders as "Not disclosed". Derived values render with a
   "calculated" label and their formula. The system never guesses.

6. ONLY SEC EDGAR IS A HARD DEPENDENCY.
   Every other source must fail gracefully. If market data is down, the report
   still generates without the valuation table. If narrative parsing fails, the
   report still generates from XBRL. Never render a blank screen or a stack
   trace.

7. NOTHING IS HARDCODED TO A SINGLE COMPANY.
   The system will be demonstrated live on an arbitrary US ticker chosen by a
   third party. Detect domestic vs foreign filers (10-K vs 20-F/40-F), detect
   sector from SIC code, and switch metric templates accordingly.

## Stack

Frontend  Next.js 15 (App Router), TypeScript strict, Tailwind CSS, shadcn/ui
Backend   Python 3.11, FastAPI, pandas, numpy, httpx
Database  Supabase (Postgres + Storage + Realtime)
Hosting   Vercel (frontend), Railway (backend)
LLM       Anthropic Claude API

## Repo layout

    trisheet/
      frontend/                 Next.js -> Vercel
        app/
          page.tsx              input screen
          r/[id]/page.tsx       report view
          layout.tsx
        components/
          ui/                   shadcn primitives
          report/               report sections
        lib/
          supabase.ts
          api.ts
          types.ts
      backend/                  FastAPI -> Railway
        app/
          main.py
          config.py
          models.py             pydantic models incl. Fact
          modules/
            m01_resolver.py     ticker -> CIK, filer type, sector
            m02_discovery.py    filing manifest
            m03_financials.py   XBRL extraction
            m04_narrative.py    10-K text sections
            m05_market.py       ONLY file touching market data
            m06_factstore.py    persistence + provenance
            m07_analysis.py     pure Python maths, no LLM
            m08_peers.py        peer selection ladder
            m09_developments.py 8-K timeline
            m10_writer.py       LLM prose generation
            m11_factcheck.py    blocking verification gate
            m12_assembler.py    PDF / XLSX output
          services/
            edgar.py            rate-limited SEC client
            llm.py              Anthropic client
            db.py               Supabase client
        tests/
      db/
        schema.sql
      docs/

## SEC EDGAR client requirements

Every request to sec.gov or data.sec.gov MUST:
  - send User-Agent: "Trisheet <contact-email>"  (403 without it)
  - respect a global limit of 10 requests/second across ALL workers
  - use a shared token-bucket limiter, not a per-process sleep
  - retry on 429 with exponential backoff, max 3 attempts
  - cache filing responses permanently (filings are immutable)

Endpoints:
  https://www.sec.gov/files/company_tickers.json
  https://data.sec.gov/submissions/CIK##########.json
  https://data.sec.gov/api/xbrl/companyfacts/CIK##########.json
  https://data.sec.gov/api/xbrl/companyconcept/CIK##########/{taxonomy}/{tag}.json

CIK is always zero-padded to 10 digits.

## XBRL extraction rules

- Never hardcode a single tag. Use ordered fallback lists per metric.
  Revenue: RevenueFromContractWithCustomerExcludingAssessedTax -> Revenues
           -> SalesRevenueNet -> RevenueFromContractWithCustomerIncludingAssessedTax
- Try us-gaap taxonomy first, fall back to ifrs-full for foreign filers.
- Deduplicate on (start, end, form); keep the record with the latest filed date.
- Prefer amendments (10-K/A) over originals for the same period.
- Record which tag actually resolved, in the Fact.

## Code standards

- TypeScript strict mode. No `any`. No non-null assertions without justification.
- Python fully type-hinted. Pydantic models for all boundaries. mypy clean.
- Every module has a single responsibility and a documented public interface.
- No magic numbers or inline literals. Constants live in config.
- Every external call wrapped in explicit error handling with typed failures.
- Structured logging (JSON) with correlation by report_id. Never print().
- Pure functions in m07_analysis.py. No I/O, no globals, no side effects.
- Unit tests required for m07_analysis.py and m11_factcheck.py.
- No commented-out code. No TODO without an issue reference.
- Secrets only from environment variables. Never committed, never logged.

## Git conventions

Conventional Commits, imperative mood, scoped:

    feat(m03): add IFRS taxonomy fallback for foreign filers
    fix(edgar): honour Retry-After header on 429
    refactor(m07): extract margin bridge into pure function
    test(m11): cover segment-sum tolerance boundary
    docs(readme): document environment variables
    chore(deps): pin httpx to 0.27

Rules:
  - One logical change per commit. Never mix refactor with feature.
  - Body explains WHY, not what. The diff shows what.
  - Never commit secrets, .env, node_modules, __pycache__, or build output.
  - Branch naming: feat/m03-xbrl-extraction, fix/edgar-rate-limit
  - Commit after each working increment, not at the end of a phase.

## Design system

Do not use default shadcn styling unmodified. This must read as a research
document, not a SaaS dashboard.

Typography
  Display  Fraunces          section titles, company name
  Body     IBM Plex Sans     all UI text
  Data     IBM Plex Mono     figures, tickers, accession numbers, XBRL tags

Palette (light mode is the default; financial documents are read paper-white)
  --paper      #FBFAF7   page background
  --ink        #14201C   primary text
  --rule       #E2DED4   hairlines and table borders
  --certified  #1F4D3D   Tier 1, filings
  --market     #3E5C7A   Tier 3, market data
  --flag       #9E3B26   conflicts, warnings, restatements

Rules
  - All figures right-aligned, monospace, tabular-nums.
  - Hairline rules to separate content. Not cards. Not shadows.
  - No gradients, no glassmorphism, no glow, no sparkle icons.
  - Never use the word "AI" in the interface.
  - Sentence case everywhere.
  - "Not disclosed" — never "N/A", never blank, never zero.
  - Errors state what happened and what to do, in the interface's voice.
    "No annual filing found for this CIK" not "Sorry, something went wrong."

Signature element
  The provenance rail: a persistent column beside the report where each
  figure's source appears as a reference card (form type, accession number,
  filing date, link). Hovering a figure highlights its source. This is the
  product's identity — it stays visible, it is never collapsed into a footer.

## Definition of done for any phase

  - Types check clean (tsc --noEmit / mypy)
  - Lint clean
  - Tests pass
  - Runs against at least 3 different tickers including one foreign filer
  - Errors handled, nothing crashes
  - Committed with a conventional commit message
