-- Tearsheet schema.
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
  cik            text          references companies (cik) on delete set null,
  status         report_status not null default 'queued',
  -- Set only when status is 'failed'. States what happened and what to do.
  error_message  text,
  created_at     timestamptz   not null default now(),
  completed_at   timestamptz,

  constraint reports_failed_has_message
    check (status <> 'failed' or error_message is not null)
);

create index if not exists reports_ticker_created_idx
  on reports (upper(ticker), created_at desc);
create index if not exists reports_status_idx on reports (status);

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

  -- Provenance. Not one of these is nullable, by design.
  tier           smallint    not null,
  source_type    source_type not null,
  source_url     text        not null,
  accession_no   text        not null,
  filed_date     date        not null,

  -- Extraction trail.
  resolved_tag   text,
  taxonomy       taxonomy,
  is_calculated  boolean     not null default false,
  formula        text,

  created_at     timestamptz not null default now(),

  constraint facts_tier_range check (tier between 1 and 4),
  -- A derived figure is rendered with its formula, or it is not rendered.
  constraint facts_calculated_has_formula
    check (not is_calculated or formula is not null),
  -- Never blank. A missing figure reads "Not disclosed".
  constraint facts_display_value_not_blank check (length(trim(display_value)) > 0),
  constraint facts_source_url_not_blank check (length(trim(source_url)) > 0),
  constraint facts_accession_no_not_blank check (length(trim(accession_no)) > 0),
  -- One value per metric per period per report. NULLS NOT DISTINCT so that
  -- instant facts, which have no period_start, cannot be inserted twice.
  -- Requires Postgres 15 or later.
  constraint facts_unique_metric_period
    unique nulls not distinct (report_id, metric, period_end, period_start)
);

create index if not exists facts_report_metric_idx on facts (report_id, metric);
create index if not exists facts_report_tier_idx on facts (report_id, tier);
create index if not exists facts_accession_idx on facts (accession_no);
create index if not exists facts_period_end_idx on facts (report_id, period_end desc);

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

create index if not exists run_logs_report_created_idx
  on run_logs (report_id, created_at desc);
create index if not exists run_logs_level_idx on run_logs (level);

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

-- market_cache and run_logs carry no policies: no browser role can read them.
