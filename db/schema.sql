-- Trisheet schema.
--
-- Run against a fresh Supabase Postgres database. Idempotent: safe to re-run.
--
-- The shape of `facts` is the schema's whole point. Every provenance column is
-- NOT NULL, so a fact that cannot name its source cannot be inserted. The
-- application discards such facts at write time; this is the second wall.

create extension if not exists "pgcrypto";

-- ---------------------------------------------------------------------------
-- Enumerated types
-- ---------------------------------------------------------------------------

-- domestic files 10-K, foreign files 20-F, canadian files 40-F under the
-- Multijurisdictional Disclosure System.
do $$ begin
  create type filer_type as enum ('domestic', 'foreign', 'canadian');
exception when duplicate_object then null; end $$;

-- For databases created before 'canadian' existed.
alter type filer_type add value if not exists 'canadian';

do $$ begin
  create type source_type as enum (
    'sec_filing',
    'sec_xbrl',
    'company_site',
    'investor_presentation',
    'press_release',
    'market_data',
    'news'
  );
exception when duplicate_object then null; end $$;

do $$ begin
  create type taxonomy as enum ('us-gaap', 'ifrs-full', 'dei');
exception when duplicate_object then null; end $$;

-- How a fact came to exist. Part of provenance: a reader who disputes a figure
-- needs to know whether it was read off a filing, parsed out of prose or
-- computed before they know which part of the pipeline to distrust.
do $$ begin
  create type extraction_method as enum (
    'xbrl_company_facts',
    'xbrl_dimensional',
    'narrative',
    'calculated',
    'market_data',
    'not_disclosed'
  );
exception when duplicate_object then null; end $$;

do $$ begin
  create type report_status as enum (
    'queued',
    'resolving',
    'extracting',
    'analysing',
    'writing',
    'verifying',
    'complete',
    'failed'
  );
exception when duplicate_object then null; end $$;

-- How far the pipeline goes. Depth changes how many periods are extracted and
-- which optional modules run; it never changes a provenance rule.
do $$ begin
  create type analysis_depth as enum ('brief', 'standard', 'full');
exception when duplicate_object then null; end $$;

-- 'custom' lets a caller request an exact period count instead of one of the
-- three fixed budgets. Added after the type first shipped, so an existing
-- deployment picks it up via ADD VALUE rather than the CREATE TYPE above.
-- Must run outside an explicit transaction block (Postgres restriction on
-- ALTER TYPE ... ADD VALUE).
alter type analysis_depth add value if not exists 'custom';

-- Where one pipeline step got to. 'skipped' is not 'failed': an optional
-- source with nothing to give is a finding about the filer, not an error.
do $$ begin
  create type step_state as enum (
    'pending', 'running', 'done', 'skipped', 'failed'
  );
exception when duplicate_object then null; end $$;

do $$ begin
  create type artifact_kind as enum ('pdf', 'xlsx');
exception when duplicate_object then null; end $$;

-- ---------------------------------------------------------------------------
-- companies
-- ---------------------------------------------------------------------------

create table if not exists companies (
  cik              text primary key,
  ticker           text        not null,
  name             text        not null,
  filer_type       filer_type  not null,
  sic_code         text,
  sector           text,
  fiscal_year_end  text,
  -- ISO 4217. Null when it could not be determined from XBRL units.
  reporting_currency text,
  created_at       timestamptz not null default now(),
  updated_at       timestamptz not null default now(),

  -- EDGAR always addresses a CIK zero-padded to 10 digits.
  constraint companies_cik_is_padded check (cik ~ '^[0-9]{10}$'),
  constraint companies_fiscal_year_end_is_mmdd
    check (fiscal_year_end is null or fiscal_year_end ~ '^[0-9]{4}$')
);

-- For databases created before reporting_currency existed.
alter table companies add column if not exists reporting_currency text;

create unique index if not exists companies_ticker_key on companies (upper(ticker));
create index if not exists companies_sic_code_idx on companies (sic_code);

-- ---------------------------------------------------------------------------
-- filings
-- ---------------------------------------------------------------------------

create table if not exists filings (
  accession_no      text primary key,
  cik               text        not null references companies (cik) on delete cascade,
  form              text        not null,
  filed_date        date        not null,
  period_of_report  date,
  primary_doc_url   text        not null,
  created_at        timestamptz not null default now()
);

create index if not exists filings_cik_form_filed_idx
  on filings (cik, form, filed_date desc);
create index if not exists filings_cik_period_idx
  on filings (cik, period_of_report desc);

-- ---------------------------------------------------------------------------
-- reports
-- ---------------------------------------------------------------------------

create table if not exists reports (
  id             uuid primary key default gen_random_uuid(),
  ticker         text          not null,
  -- Not a foreign key. The endpoint registers a report the instant it answers
  -- with a job id — before resolution has run, so no companies row for this
  -- cik necessarily exists yet. m01 upserts companies once it resolves; nothing
  -- here may block on that order.
  cik            text,
  status         report_status not null default 'queued',
  -- Set only when status is 'failed'. States what happened and what to do.
  error_message  text,
  created_at     timestamptz   not null default now(),
  completed_at   timestamptz,

  constraint reports_failed_has_message
    check (status <> 'failed' or error_message is not null)
);

-- For databases created before this was a plain column. See comment above.
alter table reports drop constraint if exists reports_cik_fkey;

-- Run accounting. Added nullable because a report that already ran has no way
-- to know what it cost, and guessing is exactly what this system does not do.
--
-- These columns are what the monitoring endpoint aggregates. They are stored
-- per report rather than recomputed on read because a report's cost is a fact
-- about that run: re-deriving it later from a price list that has since changed
-- would quietly restate history.
alter table reports add column if not exists depth analysis_depth not null default 'standard';
alter table reports add column if not exists started_at timestamptz;
-- End-to-end wall time. The latency figure; p95 is taken over this column.
alter table reports add column if not exists duration_ms integer;
alter table reports add column if not exists fact_count integer;
alter table reports add column if not exists figure_count integer;
alter table reports add column if not exists cited_figure_count integer;
-- Citation coverage as m11 measured it, 0-1. Never recomputed downstream.
alter table reports add column if not exists coverage_ratio numeric;
alter table reports add column if not exists compliance_passed boolean;
alter table reports add column if not exists input_tokens integer;
alter table reports add column if not exists output_tokens integer;
-- Model spend for this report, in US dollars, priced at the rates in force
-- when the run happened.
alter table reports add column if not exists cost_usd numeric;

alter table reports drop constraint if exists reports_coverage_ratio_range;
alter table reports add constraint reports_coverage_ratio_range
  check (coverage_ratio is null or coverage_ratio between 0 and 1);

alter table reports drop constraint if exists reports_duration_not_negative;
alter table reports add constraint reports_duration_not_negative
  check (duration_ms is null or duration_ms >= 0);

create index if not exists reports_ticker_created_idx
  on reports (upper(ticker), created_at desc);
create index if not exists reports_status_idx on reports (status);
-- The monitoring window is "settled runs, newest first".
create index if not exists reports_completed_at_idx
  on reports (completed_at desc nulls last);

-- ---------------------------------------------------------------------------
-- facts
--
-- tier: 1 SEC filings | 2 company sources | 3 market data | 4 news
-- Section 3 accepts tiers 1 and 2 only; that rule is enforced in application
-- code (m06, m11) because it depends on which section a fact is bound for.
-- ---------------------------------------------------------------------------

create table if not exists facts (
  id             uuid primary key default gen_random_uuid(),
  report_id      uuid        not null references reports (id) on delete cascade,

  metric         text        not null,
  label          text        not null,
  value          numeric,
  display_value  text        not null,
  unit           text,

  period_start   date,
  period_end     date        not null,
  fiscal_year    integer,

  -- Segmentation. Null for a consolidated figure, which is what the sum of its
  -- segments must match. Set together or not at all.
  segment_axis   text,
  segment_member text,
  segment_label  text,

  -- Provenance. Not one of these is nullable, by design.
  tier              smallint          not null,
  source_type       source_type       not null,
  source_url        text              not null,
  accession_no      text              not null,
  filed_date        date              not null,
  extraction_method extraction_method not null,
  -- How far down the tag fallback ladder the figure was found. 1.0 is an exact
  -- hit on the metric's preferred tag; a NOT_DISCLOSED marker carries 0.
  confidence        numeric           not null,

  -- Extraction trail.
  resolved_tag   text,
  taxonomy       taxonomy,
  is_calculated  boolean     not null default false,
  formula        text,

  created_at     timestamptz not null default now(),

  constraint facts_tier_range check (tier between 1 and 4),
  constraint facts_confidence_range check (confidence between 0 and 1),
  -- A derived figure is rendered with its formula, or it is not rendered.
  constraint facts_calculated_has_formula
    check (not is_calculated or formula is not null),
  -- Never blank. A missing figure reads "Not disclosed".
  constraint facts_display_value_not_blank check (length(trim(display_value)) > 0),
  constraint facts_source_url_not_blank check (length(trim(source_url)) > 0),
  constraint facts_accession_no_not_blank check (length(trim(accession_no)) > 0),
  -- A member without its axis is unattributable.
  constraint facts_segment_axis_and_member
    check ((segment_axis is null) = (segment_member is null)),
  -- Nothing is estimated: a fact marked not disclosed carries no value.
  constraint facts_not_disclosed_has_no_value
    check (extraction_method <> 'not_disclosed' or value is null),
  -- One value per metric per period per segment per report. NULLS NOT DISTINCT
  -- so that instant facts, which have no period_start, and consolidated facts,
  -- which have no segment, cannot be inserted twice.
  -- Requires Postgres 15 or later.
  constraint facts_unique_metric_period
    unique nulls not distinct (
      report_id, metric, period_end, period_start, segment_axis, segment_member
    )
);

-- For databases created before these columns existed. New columns are added
-- nullable first, then filled, because an existing row has no way to know how
-- it was extracted — and guessing is exactly what this system does not do.
alter table facts add column if not exists segment_axis text;
alter table facts add column if not exists segment_member text;
alter table facts add column if not exists segment_label text;
alter table facts add column if not exists extraction_method extraction_method;
alter table facts add column if not exists confidence numeric;

create index if not exists facts_report_metric_idx on facts (report_id, metric);
create index if not exists facts_report_tier_idx on facts (report_id, tier);
create index if not exists facts_accession_idx on facts (accession_no);
create index if not exists facts_period_end_idx on facts (report_id, period_end desc);
create index if not exists facts_report_fiscal_year_idx
  on facts (report_id, fiscal_year);
create index if not exists facts_segment_idx
  on facts (report_id, metric, segment_axis);

-- ---------------------------------------------------------------------------
-- market_cache
--
-- Tier 3 only, and unlike filings it expires: market data is not immutable.
-- ---------------------------------------------------------------------------

create table if not exists market_cache (
  cache_key   text primary key,
  ticker      text        not null,
  payload     jsonb       not null,
  fetched_at  timestamptz not null default now(),
  expires_at  timestamptz not null
);

create index if not exists market_cache_ticker_idx on market_cache (upper(ticker));
create index if not exists market_cache_expires_idx on market_cache (expires_at);

-- ---------------------------------------------------------------------------
-- run_logs
--
-- Structured pipeline log, correlated by report_id.
-- ---------------------------------------------------------------------------

create table if not exists run_logs (
  id          bigint generated always as identity primary key,
  report_id   uuid        references reports (id) on delete cascade,
  module      text        not null,
  level       text        not null,
  message     text        not null,
  context     jsonb       not null default '{}'::jsonb,
  created_at  timestamptz not null default now(),

  constraint run_logs_level_known
    check (level in ('DEBUG', 'INFO', 'WARNING', 'ERROR'))
);

-- Step accounting, first class.
--
-- The same step event is also written into `context.step`, which is the shape
-- the browser's progress feed parses. That is deliberate duplication with one
-- reason each way: aggregation wants columns it can index and percentile over,
-- and Realtime wants one row the client can render without a join. Both are
-- written from the same struct in app/services/runlog.py, so they cannot
-- disagree.
alter table run_logs add column if not exists step text;
alter table run_logs add column if not exists state step_state;
alter table run_logs add column if not exists duration_ms integer;
-- What the step produced: 142 facts, 31 filings. Never a percentage.
alter table run_logs add column if not exists count integer;

alter table run_logs drop constraint if exists run_logs_duration_not_negative;
alter table run_logs add constraint run_logs_duration_not_negative
  check (duration_ms is null or duration_ms >= 0);

-- A settled step states how long it took. A step still running has no duration
-- yet, and one that never started has none to state.
alter table run_logs drop constraint if exists run_logs_settled_has_duration;
alter table run_logs add constraint run_logs_settled_has_duration
  check (
    state is null
    or state in ('pending', 'running')
    or duration_ms is not null
  );

create index if not exists run_logs_report_created_idx
  on run_logs (report_id, created_at desc);
create index if not exists run_logs_level_idx on run_logs (level);
create index if not exists run_logs_step_idx on run_logs (step, state);

-- ---------------------------------------------------------------------------
-- artifacts
--
-- The rendered outputs. Rows exist only for artifacts that were built; an
-- artifact that could not be rendered is reported on the report, not stored
-- here as an empty row.
-- ---------------------------------------------------------------------------

create table if not exists artifacts (
  id            uuid primary key default gen_random_uuid(),
  report_id     uuid          not null references reports (id) on delete cascade,
  kind          artifact_kind not null,
  -- Path within the storage bucket. The URL may expire; this does not.
  storage_path  text          not null,
  url           text,
  size_bytes    integer       not null,
  content_type  text          not null,
  created_at    timestamptz   not null default now(),

  constraint artifacts_size_positive check (size_bytes > 0),
  -- One current artifact of each kind per report. A re-run replaces it.
  constraint artifacts_unique_kind unique (report_id, kind)
);

create index if not exists artifacts_report_idx on artifacts (report_id);

-- ---------------------------------------------------------------------------
-- chat_messages
--
-- One question about a completed report and the assistant's answer, restricted
-- to tiers 1 and 2 in this build (facts already stored for the report, or
-- derivable from the same reported facts under a wider metric selection).
-- Company-website and web-search tiers are separate, deliberately deferred
-- phases; highest_tier_used, cost_usd and tool_calls are provisioned now so
-- that phase needs no second migration.
-- ---------------------------------------------------------------------------

create table if not exists chat_messages (
  id                 bigint generated always as identity primary key,
  report_id          uuid        not null references reports (id) on delete cascade,
  turn_index         integer     not null,
  role               text        not null,
  -- Plain-text rendering. Populated for both roles; the user's own question,
  -- or the assistant's claims joined into continuous text for simple display.
  content            text        not null,
  -- ChatClaim[] for an assistant turn. Null for a user turn, which cites
  -- nothing.
  claims             jsonb,
  -- Audit trail of what was tried before this turn's answer was reached.
  tool_calls         jsonb       not null default '[]'::jsonb,
  highest_tier_used  smallint,
  not_found          boolean     not null default false,
  input_tokens       integer,
  output_tokens      integer,
  cost_usd           numeric,
  created_at         timestamptz not null default now(),

  constraint chat_messages_role_known check (role in ('user', 'assistant')),
  constraint chat_messages_tier_range
    check (highest_tier_used is null or highest_tier_used between 1 and 2),
  -- An assistant turn always carries claims (even if only a not-found claim);
  -- a user turn carries none, because it is a question, not an answer.
  constraint chat_messages_assistant_has_claims
    check (role = 'user' or claims is not null)
);

create index if not exists chat_messages_report_turn_idx
  on chat_messages (report_id, turn_index);
create index if not exists chat_messages_report_tier_created_idx
  on chat_messages (report_id, highest_tier_used, created_at desc);

-- ---------------------------------------------------------------------------
-- report_runs
--
-- The monitoring view, for humans reading the database directly. The /metrics
-- endpoint does not read this: it aggregates the underlying rows in Python, so
-- that the figures it publishes and the figures here are computed from the same
-- columns rather than by two dialects that could drift.
-- ---------------------------------------------------------------------------

create or replace view report_runs as
select
  r.id,
  r.ticker,
  r.cik,
  r.status,
  r.depth,
  r.created_at,
  r.completed_at,
  r.duration_ms,
  r.fact_count,
  r.figure_count,
  r.cited_figure_count,
  r.coverage_ratio,
  r.compliance_passed,
  r.input_tokens,
  r.output_tokens,
  r.cost_usd,
  (select count(*) from artifacts a where a.report_id = r.id) as artifact_count,
  (select count(*) from run_logs l
    where l.report_id = r.id and l.state = 'failed') as failed_steps
from reports r;

-- ---------------------------------------------------------------------------
-- updated_at maintenance
-- ---------------------------------------------------------------------------

create or replace function set_updated_at() returns trigger
language plpgsql
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

drop trigger if exists companies_set_updated_at on companies;
create trigger companies_set_updated_at
  before update on companies
  for each row execute function set_updated_at();

-- ---------------------------------------------------------------------------
-- Row level security
--
-- Enabled on every table. The backend uses the service role key, which bypasses
-- RLS; the browser uses the publishable key and is granted read access to
-- published company data only. There is no client-side write path anywhere —
-- that is what stops provenance enforcement being bypassed from the browser.
-- ---------------------------------------------------------------------------

alter table companies    enable row level security;
alter table filings      enable row level security;
alter table reports      enable row level security;
alter table facts        enable row level security;
alter table market_cache enable row level security;
alter table run_logs     enable row level security;
alter table artifacts    enable row level security;
alter table chat_messages enable row level security;

drop policy if exists companies_read on companies;
create policy companies_read on companies
  for select to anon, authenticated using (true);

drop policy if exists filings_read on filings;
create policy filings_read on filings
  for select to anon, authenticated using (true);

drop policy if exists reports_read on reports;
create policy reports_read on reports
  for select to anon, authenticated using (true);

drop policy if exists facts_read on facts;
create policy facts_read on facts
  for select to anon, authenticated using (true);

drop policy if exists artifacts_read on artifacts;
create policy artifacts_read on artifacts
  for select to anon, authenticated using (true);

-- run_logs is readable because the progress feed streams it over Realtime.
-- It carries no figures — only step names, states and counts — so nothing here
-- exposes a fact that has not passed the provenance gate.
drop policy if exists run_logs_read on run_logs;
create policy run_logs_read on run_logs
  for select to anon, authenticated using (true);

-- market_cache carries no policy: no browser role can read it.

-- chat_messages is readable so a returning visitor's chat history can be
-- restored, but there is no insert/update policy for any browser role: a
-- message reaches this table only through the backend's service-role write,
-- the same rule that keeps provenance enforcement from being bypassed from
-- the browser everywhere else in this schema.
drop policy if exists chat_messages_read on chat_messages;
create policy chat_messages_read on chat_messages
  for select to anon, authenticated using (true);

-- ---------------------------------------------------------------------------
-- Realtime
--
-- The progress feed subscribes to run_logs inserts and reports updates. Both
-- must be in the publication or the browser silently falls back to polling.
-- ---------------------------------------------------------------------------

do $$ begin
  alter publication supabase_realtime add table run_logs;
exception when duplicate_object then null; end $$;

do $$ begin
  alter publication supabase_realtime add table reports;
exception when duplicate_object then null; end $$;

-- An UPDATE payload carries only the changed columns unless the row's replica
-- identity is full. The feed reads status and error_message off the payload,
-- so it needs the whole row.
alter table reports replica identity full;

-- ---------------------------------------------------------------------------
-- Storage
--
-- One public bucket for rendered reports. Public because a trisheet is a
-- document meant to be handed to someone; nothing in it is private that was not
-- already public on EDGAR. Writes come only from the service role.
-- ---------------------------------------------------------------------------

insert into storage.buckets (id, name, public)
values ('reports', 'reports', true)
on conflict (id) do update set public = excluded.public;

drop policy if exists report_artifacts_read on storage.objects;
create policy report_artifacts_read on storage.objects
  for select to anon, authenticated using (bucket_id = 'reports');
