---
status: draft
issue: 1
spec: spec/2026-10-06-1-reference-engine.md
---

# Plan: Customer reference engine

## Approved decisions (self-contained; the spec need not be opened)

- **What it is:** an org-wide AWS-hosted portal that:
  - crawls SharePoint/OneDrive, Confluence and file shares
  - extracts one reviewed `ReferenceCase` record per client engagement
  - finds the best cases for a bid
  - renders them as branded docx, pptx, PDF and Markdown
  - researches capabilities online, with cited public sources
- **No vector DB / chunk RAG.** Extract once at ingest → Postgres jsonb +
  `tsvector` full-text + filters → top 20 → LLM picks and tailors the top 3.
  `pgvector` on `summary` only if recall on the 50-query set is < 90%.
- **Every extracted field and research claim carries a verbatim `source_quote`**.
  A quote not found in the source text (after whitespace normalisation) is
  marked `unsourced` (cases) or dropped (research).
- **Human approval** before a case is usable. `review_due = approved_at + 12
  months`. Expired cases leave search until re-approved.
- **Anonymisation by default** from the `clients` registry (name, aliases,
  anonymised_label, referenceable, logo_allowed). Rendering is blocked if the
  output contains a registry name that is not `referenceable`.
- **Access:** OIDC SSO, with roles `user | reviewer | admin` from IdP groups.
  A user sees a document only if their groups overlap its `acl_groups`, which
  are captured at crawl time. Up to one day of ACL lag is accepted.
- **Crawling:**
  - SharePoint uses Graph `delta` (app permission `Sites.Selected`, never
    `Sites.Read.All`).
  - Confluence uses REST + CQL on `lastmodified`.
  - File shares are copied by AWS DataSync to an S3 prefix, with ACL groups
    configured per prefix.
  - Uploads go to an S3 prefix.
  - Crawls resume from stored cursors, run daily via EventBridge plus a manual
    trigger, mark deletions, and back off on 429.
- **Triage** (small model, first ~2,000 tokens) sets
  `kind = case|proposal|deck|other`. Only work-describing docs are extracted.
  The rest stay searchable as "related documents".
- **Outputs:** the LLM supplies JSON only.
  - Word: `docxtpl` + `brand/reference.docx`.
  - PowerPoint: `python-pptx` filling *named* placeholders of the "Reference
    case" layout in `brand/master.pptx`.
  - PDF: LibreOffice headless.
  - Markdown: a Jinja template.
- **Online research:**
  - The query is built from capability/product terms only, scrubbed against
    the registry, and previewed by the user before sending.
  - Brave Search API (zero data retention) is the default, behind one env key.
  - Fetch the top ~8 results with `httpx` and convert with Docling.
  - Claims are typed `out_of_the_box | configuration | industry_practice |
    vendor_claim`, each with URL, publisher, quote and retrieval date.
  - Results are never written to `cases`. They render only in a separate
    "Industry context" section.
  - An output with no supporting case needs the "industry context only"
    acknowledgement, which is logged.
- **LLM:** LiteLLM library (not the proxy), with env aliases `EXTRACT_MODEL`
  (small Claude model on Bedrock) and `DRAFT_MODEL` (mid-tier Claude on Bedrock).
  Other providers are a config change. Prompt caching on system prompt + schema.
  Bedrock batch for backfill. Only models whose Bedrock terms keep data out of
  provider retention.
- **Stack:**
  - Python 3.12, FastAPI + Jinja + HTMX, psycopg 3, Postgres 16, Docling, Terraform.
  - One image with entrypoints `web` and `worker`.
  - Jobs live in a Postgres table claimed with `FOR UPDATE SKIP LOCKED`.
- **AWS:**
  - ECS Fargate (web behind an internal ALB, worker), RDS Postgres 16
    (encrypted, single-AZ v1).
  - S3 (SSE-KMS, private, versioned), Secrets Manager.
  - Bedrock through a VPC endpoint with IAM scoped to the two model IDs.
  - NAT for worker outbound HTTPS, a DataSync agent, CloudWatch.
  - Document text is never logged.

## Prerequisites (owners to confirm at kickoff)

| Needed by | Item | Owner |
| --------- | ---- | ----- |
| Step 1 | GitHub repo + issue number: done, private repo olafkfreund/synechron-case-reference-engine, issue #1 | Engineering lead |
| Step 4 | AWS account, region, Bedrock model access enabled | Cloud team |
| Step 8 | IdP app registration (OIDC), group names for the three roles | IT / identity |
| Step 10 | Who approves cases, who sets `referenceable` | Sales leadership + legal |
| Step 10 | Seed client list with reference permissions | Sales ops |
| Step 13 | Brand `reference.docx` and `master.pptx` with a "Reference case" layout | Marketing |
| Step 15 | Graph `Sites.Selected` consent per site; list of sites | M365 admin |
| Step 16 | Confluence service account + spaces | Confluence admin |
| Step 17 | File shares to include, DataSync agent host | Infra |
| Step 18 | Brave Search API key (ZDR plan) | Procurement |
| Step 22 | 50 bid queries with expected cases | Bid team |

## Steps

### Phase 1: Core loop (S3 crawl/upload → triage → extract → review → search → docx/md)

1. **Repo skeleton.** Add:
   - `pyproject.toml` (fastapi, uvicorn, jinja2, psycopg[binary], pydantic,
     litellm, docling, docxtpl, python-pptx, httpx, authlib, boto3; dev: pytest)
   - `app/__init__.py`, `Dockerfile` (python:3.12-slim + `libreoffice-core
     libreoffice-writer libreoffice-impress`, Docling models pre-fetched at build)
   - `docker-compose.yml` (postgres:16 + app)
   - `.github/workflows/ci.yml` (pytest)

   → verify by `docker compose build && docker compose run app pytest` (0 tests ok).
   Traps: download the Docling models at build time, not at runtime (no NAT for web).
2. **Schema.** Write `sql/schema.sql` with the tables `sources, documents, cases,
   clients, jobs, research, generations` as in the decisions above. Include the
   `tsv` generated columns + GIN indexes and the unique `documents.checksum`.
   `app/db.py` applies it on start (idempotent `create ... if not exists`).

   → verify by `docker compose up -d db && pytest tests/test_db.py` (tables exist).
   Traps: no migration framework until a second schema change exists.
3. **ReferenceCase model.** Write `app/schema.py` (pydantic) with every field
   carrying a `source_quote`, and a `quote_in(text, quote)` helper (whitespace
   normalised).

   → verify by `pytest tests/test_schema.py` (round-trip, invented quote rejected).
4. **LLM layer.** Write `app/llm.py`:
   - `complete_json(alias, system, user, model_cls)` via LiteLLM with structured
     output, and a prompt cache marker on the system block
   - aliases read from env

   → verify by `pytest tests/test_llm.py` (LiteLLM mock), plus one manual call to
   Bedrock in the target account.
   Traps: confirm the exact Bedrock model IDs and that their data terms are
   acceptable before setting the env defaults.
5. **Ingest + triage.** Write `app/ingest.py`:
   - fetch bytes → sha256 → skip if seen; a new version re-opens its case
   - store the original in S3 and convert with Docling to markdown
   - triage via `EXTRACT_MODEL` → `documents.kind`
   - enqueue `extract` for case-like kinds

   Write `app/crawl.py` with the S3 prefix crawler (list since cursor) and the
   upload route.

   → verify by `pytest tests/test_ingest.py` (dedupe, non-case routed away,
   S3 via moto or a local bucket).
6. **Extraction.** Write `app/extract.py`: `EXTRACT_MODEL` → `ReferenceCase`,
   quote check per field, write `cases` with status `extracted`.

   → verify by `pytest tests/test_extract.py` (invented metric → `unsourced`).
7. **Worker.** Write `app/worker.py`: a job loop that claims with
   `FOR UPDATE SKIP LOCKED`, retries 3 times, and records the error.

   → verify by `pytest tests/test_worker.py` (two workers never take the same job).
8. **Auth.** In `app/main.py`:
   - OIDC login with authlib and a session cookie
   - roles from the groups claim
   - a `current_user` dependency exposing groups

   → verify by `pytest tests/test_auth.py` (role guard).
   Traps: do not trust group headers from the client, only the token.
9. **Review queue.** Add pages under `app/templates/`. A reviewer sees each field
   next to its source quote, can edit, approve or reject, and approval sets
   `review_due`.

   → verify by `pytest tests/test_review.py`.
10. **Client registry + anonymiser.** Add an admin CRUD page and `app/anonymise.py`:
    - `apply(text, clients)` replaces names and aliases with labels unless
      `referenceable`
    - `blocked(text)` → list of leaked names
    - `flag_unlisted` → LLM list of organisation names not in the registry,
      shown at review

    → verify by `pytest tests/test_anonymise.py` (aliases, case-insensitive,
    word boundaries, blocked names).
11. **Search.** Write `app/search.py`:
    - `websearch_to_tsquery` + jsonb filters, restricted to `status = approved`,
      `review_due > now()`, and ACL overlap → top 20
    - `DRAFT_MODEL` picks 3, with a reason and tailored text
    - reject the tailored text if it contains a number not present in the record

    → verify by `pytest tests/test_search.py` (ACL hides doc; expired hidden;
    new number rejected).
12. **docx + Markdown output.** Write `app/render.py`:
    - `to_docx(cases, anonymised)` with docxtpl
    - `to_markdown`
    - a `generations` audit row
    - blocked if `anonymise.blocked()` finds anything

    The download streams back and is not stored.

    → verify by `pytest tests/test_render.py` (docx reopens with python-docx, no
    `{{`, blocked name stops render).
    Traps: until marketing delivers, use a placeholder `brand/reference.docx`
    clearly marked DRAFT.

**Phase 1 exit:** on docker compose, upload 5 sanitised case docs → extracted with
quotes → approve → bid search returns the expected case in the top 3 → docx/md
download.

### Phase 2: Brand outputs

13. **pptx.** `render.to_pptx` opens `brand/master.pptx`, adds one slide per case
    from the "Reference case" layout, and fills placeholders **by name**.

    → verify by `pytest tests/test_render.py::pptx` (reopens; every named
    placeholder filled).
    Traps: never index placeholders by position. Fail loudly if a name is missing.
14. **PDF.** `render.to_pdf(path)` runs `soffice --headless --convert-to pdf` in a
    temp dir with a timeout. A CI job renders a sample against the real `brand/`
    files.

    → verify by a CI render test producing non-empty PDFs from docx and pptx.

### Phase 3: Crawlers

15. **SharePoint.** In `crawl.py`:
    - Graph `drives/{id}/root/delta` per configured drive, storing `deltaLink`
      as the cursor
    - item permissions → `acl_groups`
    - deletions → `deleted_at`
    - honour `Retry-After` on 429/503

    Add a sources admin page with the last run and counts.

    → verify against a test site: full crawl finds every file, the second run
    fetches 0, one edit re-ingests 1.
16. **Confluence.** In `crawl.py`: CQL `space in (...) and lastmodified >
    cursor`, page body (storage HTML) and attachments, with space/page
    restrictions → `acl_groups`.

    → verify the same three checks against a test space.
17. **File shares.** Terraform the DataSync task (share → `s3://.../shares/<name>/`)
    on a schedule. The S3 crawler from step 5 reads it, and ACL groups come from
    the source config.

    → verify a test share's files appear in `documents` after one sync + crawl.

### Phase 4: Online research

18. **Search + fetch.** Write `app/research.py`:
    - `build_query(question, case?)` uses capability/product terms only and runs
      `anonymise.scrub`
    - Brave Search API → top ~8 → `httpx` fetch (robots.txt, per-domain limit,
      10 s timeout) → Docling markdown

    → verify by `pytest tests/test_research.py` (registry name stripped from
    query; robots-disallowed URL skipped).
19. **Claims + comparison.** `DRAFT_MODEL` → typed claims with quote, URL,
    publisher and date. Drop claims whose quote is not in the fetched page.
    Rank sources vendor docs/standards > analyst > blog. Cache in `research`
    for 30 days. Add a UI page with the query preview, then the claims table and
    the comparison (with our case's approach beside it when started from a case).

    → verify on 10 known out-of-the-box questions: every claim's URL contains
    its quote.
20. **Industry context in outputs.** Add a separate "Industry context" section to
    the docx/pptx/md templates with footnoted links and dates, in fixed wording.
    Output with no case requires the acknowledgement checkbox, which is logged
    in `generations`.

    → verify by `pytest` (no-case output refused without the acknowledgement;
    research text never appears in `cases`).

### Phase 5: Deploy and harden

21. **Terraform `infra/`:**
    - VPC (private subnets, NAT, Bedrock VPC endpoint), ECS Fargate web + worker,
      internal ALB with ACM cert
    - RDS Postgres 16, S3 + KMS, Secrets Manager, IAM scoped to the two Bedrock
      models
    - EventBridge daily crawl schedule, CloudWatch

    → verify `terraform plan` is clean, then deploy to staging, then smoke-test
    SSO login, one crawl, and one generation.
    Traps: the web task has no internet egress; the worker egress is HTTPS only.
22. **Recall set + hardening.** Add `tests/recall.yaml` (50 bid queries →
    expected case IDs) and `scripts/recall.py` to report top-3 hit rate. Add an
    audit view, review-due reminders (email list on the review page), and a log
    check that no document text is logged.

    → verify recall ≥ 90% before org rollout. Below that, add `pgvector` on
    `summary` and re-measure.

## Tests

- `docker compose run app pytest` is green, covering every test named above.
- CI runs pytest plus a brand render test on every PR.
- `python scripts/recall.py` reports ≥ 0.90 top-3 recall.
- Staging smoke test: SSO login → crawl a test site → approve → generate docx,
  pptx, pdf and md → research one question → check the "Industry context" section.

## Rollback

- Each phase ships behind its own deploy. Roll back with the previous ECS task
  definition revision.
- The schema is additive only. RDS automated snapshots cover data rollback.
- Crawlers are read-only against sources. Disabling a source in the admin page
  stops crawling it, and deleting its documents removes its cases from search.
- To remove the system entirely, run `terraform destroy` on staging/prod.
  Originals stay in S3 (versioned) until the bucket is deleted deliberately.
