# Tearsheet

Company profiles, sourced from filings.

Tearsheet generates equity-research-grade company profiles for US-listed
companies. Every financial figure traces back to an SEC filing. The product's
value is provenance, not automation.

The working rules for this repository are in [CLAUDE.md](CLAUDE.md). They are
architectural constraints, not preferences.

## Architecture

A ticker goes in. A verified, fully sourced profile comes out.

    ticker
      |
      v
    m01 resolver ....... ticker -> CIK, filer type (10-K vs 20-F/40-F), sector
    m02 discovery ...... which filings this report may draw on
      |
      +-- m03 financials ... XBRL figures, ordered tag fallbacks, us-gaap -> ifrs
      +-- m04 narrative .... business, risk factors, MD&A
      +-- m05 market ....... price and multiples (the ONLY market-data module)
      +-- m09 developments . 8-K / 6-K timeline
      |
      v
    m06 factstore ...... write gate: no provenance, no fact
    m07 analysis ....... all arithmetic, pure Python, pandas/numpy
    m08 peers .......... comparables, selected from SIC code
    m10 writer ......... the model writes prose about computed figures
    m11 factcheck ...... blocking verification; a failing report does not render
    m12 assembler ...... PDF / XLSX
      |
      v
    report + provenance rail

Three properties are worth stating plainly, because the rest of the design
follows from them:

- **The model never does arithmetic.** Every figure is computed in `m07` and
  handed to the model already calculated. It writes prose about numbers; it
  never derives one.
- **A fact cannot exist without its source.** `Fact` has no default for any
  provenance field, and the `facts` table declares all five NOT NULL. A fact
  that cannot name its source cannot be constructed or stored.
- **Only SEC EDGAR is a hard dependency.** Market data, narrative parsing and
  peers all degrade to absence. The report still renders.

### Stack

| Layer    | Choice                                                   |
| -------- | -------------------------------------------------------- |
| Frontend | Next.js 15 (App Router), TypeScript strict, Tailwind CSS 4, shadcn/ui |
| Backend  | Python 3.11, FastAPI, pandas, numpy, httpx               |
| Database | Supabase (Postgres + Storage + Realtime)                 |
| Hosting  | Vercel (frontend), Railway (backend)                     |
| Model    | Anthropic Claude API                                     |

## Local setup

### Prerequisites

- Node.js 20 or later
- Python 3.11
- A Supabase project
- An Anthropic API key

### Database

Run `db/schema.sql` against your Supabase project — SQL editor, or:

    psql "$SUPABASE_DB_URL" -f db/schema.sql

It is idempotent and safe to re-run. Postgres 15 or later is required.

### Backend

    cd backend
    python -m venv .venv
    .venv\Scripts\activate          # Windows
    source .venv/bin/activate       # macOS / Linux
    pip install -r requirements-dev.txt
    cp ../.env.example .env         # then fill in the backend section
    uvicorn app.main:app --reload --port 8000

Check it:

    curl http://localhost:8000/health

`edgar_configured: false` in that response means `EDGAR_CONTACT_EMAIL` is unset
and no SEC request will succeed.

Checks:

    mypy app
    ruff check app tests
    pytest

### Frontend

    cd frontend
    npm install
    cp ../.env.example .env.local   # then fill in the frontend section
    npm run dev

Open http://localhost:3000.

Checks:

    npm run typecheck
    npm run lint
    npm run build

## Environment variables

The full annotated list is in [.env.example](.env.example). In brief:

### Backend — `backend/.env`

| Variable | Required | Purpose |
| -------- | -------- | ------- |
| `EDGAR_CONTACT_EMAIL` | yes | Sent to SEC as `User-Agent: Tearsheet <email>`. SEC returns 403 without it. |
| `ANTHROPIC_API_KEY` | yes | Prose generation. |
| `SUPABASE_URL` | yes | Project URL. |
| `SUPABASE_SERVICE_ROLE_KEY` | yes | Server-side database access. Bypasses row level security — never expose it to the browser. |
| `CORS_ALLOWED_ORIGINS` | no | Comma-separated browser origins. Defaults to `http://localhost:3000`. |
| `ENVIRONMENT` | no | `development` or `production`. |
| `LOG_LEVEL` | no | `DEBUG`, `INFO`, `WARNING` or `ERROR`. |
| `PORT` | no | Railway sets this at deploy time. |

### Frontend — `frontend/.env.local`

| Variable | Required | Purpose |
| -------- | -------- | ------- |
| `NEXT_PUBLIC_API_BASE_URL` | yes | Backend base URL, no trailing slash. |
| `NEXT_PUBLIC_SUPABASE_URL` | no | Enables the live status feed. |
| `NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY` | no | Browser-safe key; row level security limits it to reads. |

Secrets come from the environment only. They are never committed and never
logged.

## Repository layout

    frontend/   Next.js application, deployed to Vercel
    backend/    FastAPI application, deployed to Railway
    db/         schema.sql
    docs/       design and decision notes
    CLAUDE.md   working rules for this repository
    CHANGELOG.md
