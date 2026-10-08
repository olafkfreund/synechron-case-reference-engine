-- web and worker both run this at start; serialise concurrent create-if-not-exists
select pg_advisory_xact_lock(1);

create table if not exists sources (
  id bigserial primary key,
  kind text not null check (kind in ('s3','upload','sharepoint','confluence')),
  name text not null unique,
  config jsonb not null default '{}',
  acl_groups text[] not null default '{}',
  cursor text,
  enabled boolean not null default true,
  last_run_at timestamptz,
  last_counts jsonb
);

create table if not exists clients (
  id bigserial primary key,
  name text not null unique,
  aliases text[] not null default '{}',
  anonymised_label text not null,
  referenceable boolean not null default false,
  logo_allowed boolean not null default false
);

create table if not exists documents (
  id bigserial primary key,
  source_id bigint not null references sources(id) on delete cascade,
  external_id text not null,
  title text not null default '',
  checksum text not null,
  s3_key text,
  text text not null default '',
  kind text check (kind in ('case','contract','proposal','deck','other')),
  acl_groups text[] not null default '{}',
  deleted_at timestamptz,
  created_at timestamptz not null default now(),
  tsv tsvector generated always as (
    -- left(): tsvector caps at 1 MB, a huge proposal must not fail ingest
    to_tsvector('english', coalesce(title, '') || ' ' || left(coalesce(text, ''), 500000))) stored,
  -- a new version updates this row in place, so its case can be re-opened
  unique (source_id, external_id)
);
create index if not exists documents_tsv_idx on documents using gin (tsv);
create index if not exists documents_checksum_idx on documents (checksum);
-- ACL lives on documents only: crawls refresh it, search joins to it
create index if not exists documents_acl_idx on documents using gin (acl_groups);

create table if not exists cases (
  id bigserial primary key,
  document_id bigint not null unique references documents(id) on delete cascade,
  client_id bigint references clients(id),
  status text not null default 'extracted'
    check (status in ('extracted','approved','rejected')),
  summary text not null default '',
  data jsonb not null default '{}',
  -- field values only (no JSON keys or source quotes), written at extraction
  search_text text not null default '',
  approved_by text,
  approved_at timestamptz,
  review_due timestamptz,
  tsv tsvector generated always as (
    to_tsvector('english', coalesce(summary, '') || ' ' || coalesce(search_text, ''))) stored
);
create index if not exists cases_tsv_idx on cases using gin (tsv);
create index if not exists cases_data_idx on cases using gin (data);

-- triage may now classify a statement of work or change order as 'contract'
alter table documents drop constraint if exists documents_kind_check;
alter table documents add constraint documents_kind_check
  check (kind in ('case','contract','proposal','deck','other'));

-- what a case claims: work delivered, or scope contracted (signed SOW / change order)
alter table cases add column if not exists basis text not null default 'delivered';
alter table cases drop constraint if exists cases_basis_check;
alter table cases add constraint cases_basis_check check (basis in ('delivered','engagement'));

create table if not exists jobs (
  id bigserial primary key,
  kind text not null,
  payload jsonb not null default '{}',
  status text not null default 'queued'
    check (status in ('queued','running','done','failed')),
  attempts int not null default 0,
  error text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index if not exists jobs_queued_idx on jobs (id) where status = 'queued';

create table if not exists research (
  id bigserial primary key,
  query text not null unique,
  claims jsonb not null default '[]',
  retrieved_at timestamptz not null default now()
);

-- online research: one row per question, filled by the worker. Additive changes to the original table;
-- the query is looked up for the 30-day cache, so it is no longer unique.
alter table research drop constraint if exists research_query_key;
alter table research add column if not exists created_by text not null default '';
alter table research add column if not exists status text not null default 'queued';
alter table research add column if not exists error text;
alter table research add column if not exists results jsonb not null default '{}';
alter table research add column if not exists case_id bigint references cases(id) on delete set null;
create index if not exists research_query_idx on research (query, retrieved_at);

create table if not exists generations (
  id bigserial primary key,
  user_id text not null,
  format text not null,
  case_ids bigint[] not null default '{}',
  anonymised boolean not null default true,
  industry_context_ack boolean not null default false,
  created_at timestamptz not null default now()
);
-- which research (if any) fed an output's industry context: part of what was generated and for whom
alter table generations add column if not exists research_id bigint references research(id) on delete set null;

-- how sensitive a source's documents are: decides which models may read them (app/llm.py)
alter table sources add column if not exists data_class text not null default 'confidential';
alter table sources drop constraint if exists sources_data_class_check;
alter table sources add constraint sources_data_class_check
  check (data_class in ('confidential','sanitised','public'));

-- admin-approved exceptions: one exact model may read one data class until expires_at
create table if not exists model_approvals (
  id bigserial primary key,
  model text not null,
  data_class text not null check (data_class in ('confidential','sanitised','public')),
  approved_by text not null,
  approved_at timestamptz not null default now(),
  expires_at timestamptz not null,
  note text not null default ''
);

-- who ended an approval early
alter table model_approvals add column if not exists revoked_by text;

-- append-only: lowering a source's class opens its documents to more models, so every change is recorded
create table if not exists source_class_changes (
  id bigserial primary key,
  source_id bigint not null,
  old_class text,
  new_class text not null,
  changed_by text not null,
  changed_at timestamptz not null default now()
);
