# Deployment

How Trisheet is deployed, monitored, refreshed and scaled.

## Topology

```
  browser
     │
     ▼
  Vercel ─────────── Next.js 15, App Router, static + RSC
     │                trisheet.vercel.app
     │ HTTPS
     ▼
  Railway ────────── FastAPI, Python 3.11, Docker
     │                report generation, EDGAR client, LLM calls
     ├──────────────► SEC EDGAR      (the only hard dependency)
     ├──────────────► Anthropic API  (prose generation)
     └──────────────► Supabase       (Postgres + Storage)
```

Deploys are git-triggered from `main`: Vercel builds the frontend, Railway
builds the backend from `backend/Dockerfile`. There is no separate release
step, and no CI workflow to keep in sync with local checks.

**Why the backend is not on Vercel.** A cold report run takes tens of seconds
— dozens of rate-limited EDGAR requests, XBRL extraction, several model calls,
verification. That is a long-running job, not a request handler. Railway runs
it as an ordinary always-on process. Moving it to serverless would mean
decomposing the pipeline into steps behind a queue, which is the right design
at volume and unnecessary below it.

## Configuration

All secrets come from environment variables. Nothing is committed, nothing is
logged.

| Variable | Where | Purpose |
|---|---|---|
| `EDGAR_CONTACT_EMAIL` | Railway | Sent as the SEC User-Agent. EDGAR returns 403 without it |
| `ANTHROPIC_API_KEY` | Railway | Prose generation |
| `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY` | Railway | Persistence. Server-side only — bypasses row level security |
| `CORS_ALLOWED_ORIGINS` | Railway | The Vercel production origin |
| `NEXT_PUBLIC_API_BASE_URL` | Vercel | The Railway URL |
| `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY` | Vercel | Browser reads of published report data, restricted by RLS |

The service role key is never exposed to the browser. The browser holds only
the publishable key, and row level security restricts it to reading published
reports.

## The EDGAR constraint

The SEC permits 10 requests per second across all clients identifying as you,
and enforces it. Exceeding it earns a block, not a warning.

- A **shared token bucket**, not a per-process sleep. A per-process sleep is
  wrong the moment there is more than one worker, and quietly so.
- Retry on 429 with exponential backoff, honouring `Retry-After`, capped at
  three attempts.
- **Filing responses cache permanently.** A filing is immutable once accepted —
  a 2024 10-K will never change — so a cached response is not stale, it is
  correct. This is the single largest reason a second report on the same
  company is fast.
- Submissions and company-facts responses carry a TTL, since those documents
  do change as new filings land.

Scaling the backend horizontally requires moving the token bucket out of
process — Redis, or a single-writer gateway. Until then the deployment is
deliberately one instance, because two would silently double the request rate
and neither would know.

## Monitoring

- **Structured JSON logging**, correlated by `report_id`. No `print()`. Every
  module logs which rung answered, which tag resolved, which source was
  rejected and why — so a report that came out thin can be explained after the
  fact rather than reproduced.
- **`GET /health`** for the platform's own check.
- **`GET /metrics`** reports per-step timings — p50, p95, run count and failure
  count per module. This is what tells you that peer selection has started
  timing out, or that narrative extraction has begun missing on a class of
  filer, before a user reports it.
- Failures are typed. Every external call is wrapped, and the failure that
  reaches a reader states what happened and what to do — "No annual filing
  found for this CIK", not "something went wrong".

What is deliberately absent: there is no alerting, no error tracker and no
uptime monitor. For a prototype serving demonstrations, `/metrics` plus
platform logs are proportionate. Production would add Sentry for exceptions
and an alert on the p95 of the whole pipeline.

## Refresh

Filings are immutable, so nothing needs re-fetching — but companies file
again, and a report is a snapshot of a moment.

- Each report records `completed_at` and renders it. A reader always knows how
  old what they are looking at is.
- Market data is fetched per run and labelled "as at" its timestamp. It is
  never cached across runs; a stale price is worse than no price.
- Re-running a ticker produces a fresh report against the current manifest.

**A restart loses the rendered document, not the report.** `runlog` persists
the report record and every fact durably; the assembled document and the
PDF/XLSX links it serves are held in process memory only, deliberately —
`record_document`'s own docstring is explicit that re-persisting a projection
of facts already in the database risks a second copy that disagrees with
them. The practical consequence: a backend restart or a redeploy — including
one prompted by an unrelated code change — makes every report generated
before it briefly unreachable by document or download link, even though
`GET /reports/{id}` still resolves and the facts are intact. Re-running the
same ticker regenerates both immediately. Worth knowing before a live demo:
avoid deploying between generating a report and presenting it.

At production scale the natural design is a nightly job that walks the EDGAR
daily index, notices which tracked filers have filed since the last pass, and
invalidates only those companies' derived data. The permanent filing cache
means such a refresh costs one submissions request per filer, not a re-read of
every document.

## Scaling

In the order the constraints actually bind:

1. **EDGAR rate limit** — the first ceiling, and shared across the whole
   deployment. Fixed by moving the token bucket to Redis, after which backend
   instances can scale horizontally.
2. **Report generation is a job, not a request.** Above a handful of concurrent
   reports, `POST /reports` should enqueue and workers should consume. The
   pipeline is already structured as discrete steps with a progress feed, so
   this is a change of executor rather than a rewrite.
3. **Model cost.** The largest per-report cost. Prompt caching is already in
   place; the next lever is caching generated prose per (accession set, depth),
   since two reports on the same filings should not be written twice.
4. **Postgres** is nowhere near a constraint at this shape.

## Guardrails on a public URL

The demo URL is public and every report costs EDGAR requests and model tokens.
A per-IP token bucket on `POST /reports` bounds that. The chat endpoint has
its own rate limit already.

Anything beyond a demonstration would put authentication in front of report
creation rather than rate-limiting anonymous access.
