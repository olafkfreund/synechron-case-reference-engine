---
status: approved
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
   Done (deviations): the apt install adds `libgl1 libglib2.0-0` (rapidocr imports cv2).
   CPU-only torch and torchvision are installed in their own layer from the
   PyTorch CPU index. Models go to `/opt/docling-models`, with
   `DOCLING_ARTIFACTS_PATH` set and `HF_HUB_OFFLINE=1`; this was verified by a
   PDF conversion under `--network none`. The image runs as the non-root user
   `app` (code copied with `--chown`, pytest cache off since `/app` is read-only). `.dockerignore` excludes `.env*` and the design folders.
   `tests/test_smoke.py` exists because pytest exits 5 with 0 tests. CI runs
   compose build + pytest. Image is ~6.5 GB.
2. **Schema.** Write `sql/schema.sql` with the tables `sources, documents, cases,
   clients, jobs, research, generations` as in the decisions above. Include the
   `tsv` generated columns + GIN indexes and the unique `documents.checksum`.
   `app/db.py` applies it on start (idempotent `create ... if not exists`).

   → verify by `docker compose up -d db && pytest tests/test_db.py` (tables exist).
   Traps: no migration framework until a second schema change exists.
   Done (deviations after review):
   - Document identity is `unique (source_id, external_id)`; `checksum` is a
     plain index, not unique. The same file in two sources stays two documents,
     each with its own ACL and deletion tracking. A new version updates its row
     in place.
   - ACL groups live on `documents` only (GIN index). Cases join to them, so a
     crawl's ACL refresh applies at once.
   - `cases.document_id` is unique (one case per document).
   - `cases.search_text` (field values only) feeds `cases.tsv`, not `data::text`.
   - `documents.tsv` indexes `left(text, 500000)` (tsvector 1 MB cap).
   - `schema.sql` starts with `pg_advisory_xact_lock(1)`. Concurrent `init()`
     on an empty db otherwise fails 3/3 with a `pg_type` UniqueViolation;
     `tests/test_db.py::test_concurrent_init` proves the fix.
3. **ReferenceCase model.** Write `app/schema.py` (pydantic) with every field
   carrying a `source_quote`, and a `quote_in(text, quote)` helper (whitespace
   normalised).

   → verify by `pytest tests/test_schema.py` (round-trip, invented quote rejected).
   Done (deviations after review):
   - Fields are `Sourced[T]` (value + source_quote). `summary` is unquoted, at
     most 80 words; `client_mention` is the raw name (client_id is resolved later).
   - `quote_in` is case-insensitive. It folds curly quotes, dashes, ellipsis,
     soft hyphens and zero-width spaces, and also matches across PDF
     line-break hyphenation.
   - `sourced(value, quote, text)` also requires the value's numbers to be in
     the quote, and a quote of ≥ 4 words unless it contains the value.
   - `summary_sourced()`: every summary number must appear in some source quote.
   - Every model forbids extra keys (`additionalProperties: false`).
     `unsourced` is hidden from the LLM schema. `llm_schema()` strips pydantic
     defaults, which would otherwise leak it.
4. **LLM layer.** Write `app/llm.py`:
   - `complete_json(alias, system, user, model_cls)` via LiteLLM with structured
     output, and a prompt cache marker on the system block
   - aliases read from env

   → verify by `pytest tests/test_llm.py` (LiteLLM mock), plus one manual call to
   Bedrock in the target account.
   Traps: confirm the exact Bedrock model IDs and that their data terms are
   acceptable before setting the env defaults. Send `schema.llm_schema()`, not
   `model_json_schema()`, and confirm in the manual Bedrock call that it is
   accepted (strict mode, `$defs`).
   Done (deviations after review, LiteLLM 1.104.0 source checked):
   - `complete_json` sends `temperature=0` and `max_tokens=8192`. A reply cut
     off at the limit (`finish_reason=length`) raises "truncated"; it is never
     parsed.
   - Validation errors omit input values, so document text never reaches
     stored job errors. `litellm.turn_off_message_logging = True` and
     `LITELLM_LOG=WARNING` are set in the image.
   - On Bedrock Converse, LiteLLM uses native structured output for the
     Claude models in its cost map. Others fall back to a forced tool call, so
     only use model IDs LiteLLM knows. `strict` is ignored by LiteLLM. Prompt
     caching needs about 4,096 tokens on the small model, so short prompts
     won't cache (harmless).
   - Pending, needs AWS access: the live check in the target account.
     ```
     docker compose run --rm -e AWS_REGION_NAME -e AWS_ACCESS_KEY_ID \
       -e AWS_SECRET_ACCESS_KEY -e AWS_SESSION_TOKEN \
       -e EXTRACT_MODEL=bedrock/<small-claude-model-id> app python -c "
     from app.llm import complete_json; from app.schema import ReferenceCase
     doc = 'Acme Bank cut onboarding from 12 days to 3 days using a KYC workflow built on AWS.'
     print(complete_json('EXTRACT_MODEL', 'Extract a ReferenceCase. Quote the source verbatim for every field.', doc, ReferenceCase).model_dump_json(indent=1))"
     ```
     It passes if it returns a valid `ReferenceCase` with no BadRequest. Repeat
     it with `DRAFT_MODEL`. If Bedrock rejects `$defs`, inline them in
     `llm_schema()` and update this step.
5. **Ingest + triage.** Write `app/ingest.py`:
   - fetch bytes → sha256 → skip if seen; a new version re-opens its case
   - store the original in S3 and convert with Docling to markdown
   - triage via `EXTRACT_MODEL` → `documents.kind`
   - enqueue `extract` for case-like kinds

   Write `app/crawl.py` with the S3 prefix crawler (list since cursor) and the
   upload route.

   → verify by `pytest tests/test_ingest.py` (dedupe, non-case routed away,
   S3 via moto or a local bucket).
   Traps: a changed checksum = `update documents set checksum, text, acl_groups,
   deleted_at = null where (source_id, external_id)` + `update cases set
   status = 'extracted' where document_id = …`, never a new row.
   Done (deviations after review):
   - The upload route moved to step 8, so it never exists without login.
   - `ingest()` upserts on (source_id, external_id). Same bytes → skip (ACL
     refreshed, `deleted_at` cleared). A changed document re-opens its case and
     queues an extract job, or sets the case `rejected` if triage says it no
     longer describes delivered work.
   - Triage reads the first 8,000 characters (a plain cut). Extraction runs
     when kind = case, or proposal/deck with `describes_delivered_work`.
   - `crawl_s3()`:
     - takes `pg_try_advisory_lock(2, source_id)`, so a second concurrent
       crawl returns `{"status": "running"}`.
     - fetches unknown keys and keys modified since cursor − 1 day (multipart
       uploads are dated at upload start).
     - catches one failing object, counts it (`failed_keys` holds the key and
       exception type, never the message) and continues; the cursor advances.
     - applies the source ACL and live/deleted state to every document in one
       statement over the full listing.
     - treats an empty listing with live documents as a mistake, not a mass delete.
   - The S3 write and Docling run outside the DB transaction. Orphan
     `originals/<sha256>` objects after a crash are harmless (content-addressed).
6. **Extraction.** Write `app/extract.py`: `EXTRACT_MODEL` → `ReferenceCase`,
   quote check per field, write `cases` with status `extracted`.

   → verify by `pytest tests/test_extract.py` (invented metric → `unsourced`).
   Traps: write `cases.search_text` from field values only (no keys or quotes);
   upsert on `document_id`. Use `schema.sourced(value, quote, markdown)` per
   field and ALWAYS assign `unsourced = not ok`, never OR it with the model's
   output. If `summary_sourced()` is false, blank the summary and flag the case
   for review.
   Done (deviations after review):
   - The document is cut at 150,000 characters (no chunking) and wrapped in
     `<document>` tags.
   - `check()` sets `unsourced` from the document alone; empty values are not
     flagged.
   - Literal fields (client_mention, tech_stack) must also appear in their quote.
   - Outcomes are checked as "metric value". The period is checked by years only
     ("2023-01" vs "January 2023").
   - `ReferenceCase.needs_attention` (hidden from the LLM, always assigned)
     records truncation and a blanked summary. Steps 9, 11 and 12 rebuild cases
     with `ReferenceCase.model_validate(data)`, and step 9 shows these notes.
   - `quotes()` ignores unsourced quotes, so a fake quote can't vouch for a
     summary number.
   - Fixed `numbers(0)`, which had read as empty.
   - Known gaps, left to the reviewer: number words ("fourteen") are flagged,
     and the European "1.200" format is flagged.
7. **Worker.** Write `app/worker.py`: a job loop that claims with
   `FOR UPDATE SKIP LOCKED`, retries 3 times, and records the error.

   → verify by `pytest tests/test_worker.py` (two workers never take the same job).
   Traps: also reclaim `running` jobs whose `updated_at` is older than 15 min
   (a worker died).
   Done (deviations after review):
   - The claim commits before the handler runs.
   - Stale window: 15 min for `extract`, 6 h for `crawl_%` (first crawls run
     for hours; the daily schedule covers a dead crawl worker).
   - `finish()` updates `where id and attempts`, so a worker whose job was
     reclaimed can't overwrite the newer status.
   - At most 3 runs. A reclaim beyond that is marked `failed` ("abandoned").
     An unknown kind fails on its first run.
   - SIGTERM exits cleanly (stop takes ~1 s instead of a SIGKILL), and the
     interrupted job is requeued with its attempt given back.
   - Errors are stored as type + 200 characters. Job kinds are `extract` and
     `crawl_s3`. The compose `worker` service runs `python -m app.worker`.
   - `crawl_s3`'s lock connection is autocommit, so it holds no open
     transaction during long crawls.
   - Known gap: if a worker dies after `extract` succeeds but before `done`,
     the rerun resets the case to `extracted`. An approval given within that
     window is lost. This is rare; accepted.
8. **Auth.** In `app/main.py`:
   - OIDC login with authlib and a session cookie
   - roles from the groups claim
   - a `current_user` dependency exposing groups
   - the upload route (moved from step 5): `admin` role, stores the file under
     the upload prefix of the S3 source, then calls `crawl_s3` for that source

   → verify by `pytest tests/test_auth.py` (role guard).
   Traps: do not trust group headers from the client, only the token.
   Done (deviations after security review):
   - App: `create_app()` factory, run with `uvicorn --factory`. Startup
     requires `SESSION_SECRET` (≥ 32 characters when https-only) and
     `APP_ORIGIN`.
   - Session cookie: 8 h, `same_site=lax`, holding sub, name and groups. Groups
     are filtered to role-mapped plus ACL groups, so the cookie stays small.
   - Login refuses an Entra groups overage with a 403 and a log line. A
     cancelled or failed login returns 401, not a 500. `OIDC_REDIRECT_URI`
     sets a fixed callback URL.
   - Request guard middleware, which runs before any body is read or any auth
     dependency runs:
     - Unsafe methods need `Origin == APP_ORIGIN`, or `Sec-Fetch-Site:
       same-origin` (CSRF).
     - The body must have a Content-Length within the cap: upload 50 MB,
       otherwise 1 MB. Verified live: a 200 MB anonymous upload gets 413 in
       12 ms.
   - `/logout` is a POST. `/me` returns the current user.
   - `POST /admin/upload` (admin): docx/pptx/pdf only (no html), checked by
     magic bytes. The S3 key is a uuid plus a sanitised name of at most ~110
     characters. It queues `crawl_s3` and returns 202.
   - Known gap: the session lives 8 h, and logout cannot revoke a stolen
     cookie. This is within the accepted one-day ACL lag.
9. **Review queue.** Traps: every page uses `require("user")` or stronger,
   never bare `current_user`. Add the `/` landing page (login redirects there),
   and turn browser 401s into a redirect to `/login`. Forms POST same-origin,
   so the CSRF guard passes them unchanged.
   Add pages under `app/templates/`. A reviewer sees each field
   next to its source quote, can edit, approve or reject, and approval sets
   `review_due`.

   → verify by `pytest tests/test_review.py`.
   Done (deviations after review):
   - Plain HTML forms (POST + 303), not HTMX; Jinja autoescape is on. Pages:
     `/` landing, `/review` list, `/review/{id}` detail. Browser 401s redirect
     to `/login` (not `/auth`).
   - Need-to-know applies to every review query: `d.deleted_at is null and
     d.acl_groups && user.groups`. A case the reviewer can't see returns 404,
     never confirming that it exists.
   - Every write form carries `v = md5(cases.data)`. A case that changed
     since the page loaded (e.g. re-extracted) returns 409 "reload", so nothing
     is approved unseen. Writes lock the row (`for update`). A decided case
     returns 409.
   - Edits re-run `extract.check()` on the document text; `unsourced` is never
     read from the form.
   - Approve empties the unsourced fields, drops unsourced list items, blanks
     an unsupported summary, and clears `needs_attention`. It refuses an empty
     title and sets `review_due` = +12 months.
   - Not yet possible: adding or removing list items (reviewers edit existing
     ones).
   - `pytest` uses `pythonpath = ["."]`, so single test files run on their own.
10. **Client registry + anonymiser.** Add an admin CRUD page and `app/anonymise.py`:
    - `apply(text, clients)` replaces names and aliases with labels unless
      `referenceable`
    - `blocked(text)` → list of leaked names
    - `flag_unlisted` → LLM list of organisation names not in the registry,
      shown at review

    → verify by `pytest tests/test_anonymise.py` (aliases, case-insensitive,
    word boundaries, blocked names).
    Done (deviations after review):
    - Matcher:
      - Words inside a name may be joined by any spacing or dash, so "AcmeBank"
        and "jane@acmebank.co.uk" match "Acme Bank".
      - Short all-caps aliases are case-sensitive.
      - `blocked()` matches on folded text (NFKC, casefold, accents removed),
        so it fails closed: "Straße" ↔ "STRASSE", "İ", decomposed accents.
      - `apply()` is a best effort on the text as written; `blocked()` must run
        after it.
      - `scrub()` returns folded (lowercase, accent-free) text.
    - Unlisted organisations come from a new `ReferenceCase.organisations`
      field that extraction fills (step 6 change, no extra LLM call). It is
      compared by exact folded equality, so a sister company like "Acme
      Insurance" is not hidden by the alias "Acme". No LLM call on page view.
    - Registry admin (`/admin/clients`): plain forms, md5 version → 409. The
      label must not contain the client's own names or any other protected
      client's names. The page tells admins to add domain stems as aliases and
      to avoid common-word aliases.
    - Known gaps: an alias can't contain a comma (the form splits on commas);
      no delete.
11. **Search.** Write `app/search.py`:
    - `websearch_to_tsquery` + jsonb filters, restricted to `status = approved`,
      `review_due > now()`, and ACL overlap → top 20
    - `DRAFT_MODEL` picks 3, with a reason and tailored text
    - reject the tailored text if it contains a number not present in the record

    → verify by `pytest tests/test_search.py` (ACL hides doc; expired hidden;
    new number rejected).
    Traps: ACL and deletion filter via `join documents d` with
    `d.acl_groups && :groups and d.deleted_at is null`; cases have no ACL column.
    "No new numbers" compares against `numbers()` of sourced field values only,
    never the summary.
    Done (deviations after review):
    - Query: `plainto_tsquery` rewritten to OR (not `websearch_to_tsquery`,
      which ANDs and emits phrase operators). Ranked with
      `ts_rank_cd(..., 1)`. Stop-words-only text lets the filters decide.
      Bid text is capped at 4,000 characters, filters at 200 (`position()`,
      no wildcards).
    - Every query is limited to approved, in-date cases that pass the ACL.
    - `pick()` (DRAFT_MODEL) sends sourced values plus the summary, after
      `apply()`, capped at 1,500 characters per case.
    - Pick checks:
      - unknown or duplicate ids are dropped, at most 3 are kept, and a note
        appears when all are dropped.
      - Both `tailored` and `reason` must use only numbers from sourced field
        values. A failing tailored text falls back to the approved summary; a
        failing reason is blanked.
      - Everything shown goes through `apply()` then `blocked()`; leftovers
        are dropped or shown as `[withheld]`.
      - An LLM failure falls back to rank order with a note. No internal case
        ids appear in user-facing notes.
    - `/search`: GET shows the form, POST runs the search, so confidential bid
      text stays out of URLs, access logs and history.
    - Approve now links `cases.client_id` through `anonymise.resolve()`, an
      exact folded match on name or alias of the sourced client mention.
12. **docx + Markdown output.** Traps: always `apply()` then `blocked()`,
    and refuse to render if `blocked()` is non-empty. Never render
    `ReferenceCase.organisations`, and keep it out of search. The client
    name is shown only when the linked `clients.referenceable` is true;
    otherwise use its `anonymised_label`, or "a client" when unlinked. The
    tailored text can contain a prospect name from the bid text that the
    registry doesn't know; that is acceptable, because the user typed it.
    Write `app/render.py`:
    - `to_docx(cases, anonymised)` with docxtpl
    - `to_markdown`
    - a `generations` audit row
    - blocked if `anonymise.blocked()` finds anything

    The download streams back and is not stored.

    → verify by `pytest tests/test_render.py` (docx reopens with python-docx, no
    `{{`, blocked name stops render).
    Traps: until marketing delivers, use a placeholder `brand/reference.docx`
    clearly marked DRAFT.
    Done (deviations after review):
    - Downloads hold the approved record only: no AI-tailored search text,
      quotes, organisations or needs_attention. The server reloads the records
      from `case_ids`; no client text reaches a document.
    - `protect()` = `apply()` on every string, then `blocked()` over the whole
      output → 409 "output withheld", with no audit row. A grep of every docx
      part found no protected name.
    - Interfaces: `to_docx(sections)` and `to_markdown(sections)`. The template
      context is `cases[]` with title, client, summary, `details[]` (label,
      value), `blocks[]` (heading, text) and `lists[]` (heading, bullets).
      Marketing's template must use this context. docxtpl runs with
      autoescape; core properties are set to neutral values (no template
      metadata).
    - Markdown escaping covers `\ ` * _ [ ] < > # | ~` and a leading list or
      numbered marker. Newlines are folded, so values can't create structure.
    - `POST /generate` (user): 1–3 ids, the same restrictions as search (404
      before any 409), format docx|md, attachment with a fixed filename. The
      audit row has `anonymised` = any case shown with a label or "a client".
    - Client display: a linked referenceable client shows its name, otherwise
      its label, otherwise "a client". The registry now refuses a name or
      alias that another client already uses (folded), which would attribute
      cases to the wrong client.
    - `scripts/make_reference_template.py` regenerates the placeholder.

**Phase 1 exit:** on docker compose, upload 5 sanitised case docs → extracted with
quotes → approve → bid search returns the expected case in the top 3 → docx/md
download.
Result (2026-10-07, LLM, S3 and the top-3 pick mocked, because there's no AWS
access yet):
- 5 generated docx went through real Docling `ingest()` → `extract()` →
  approve (303), all linked to the registry client.
- `POST /search` returned the expected case, and the client name was absent.
- `/generate` docx (37 KB) and md both came back 200, with "a UK bank" shown.
- Still to run on real Bedrock and real sample cases, once the prerequisites
  are met: AWS access and 5 sanitised cases from the bid team.

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
    Traps: catch per-item errors inside the crawler like `crawl_s3` does,
    storing only the key and exception type. Graph and Confluence error
    messages contain site paths and file names. Job kind `crawl_sharepoint`
    gets the 6 h stale window automatically (`crawl_%`).
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

18. **Search + fetch.** Traps: `anonymise.scrub()` returns folded text
    (lowercase, accents removed). That is fine for a query; show the user the
    scrubbed query before sending it.
    Write `app/research.py`:
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
    Traps from step 8, all required for login to work:
    - `APP_ORIGIN` = the public https URL.
    - `OIDC_REDIRECT_URI` = `<APP_ORIGIN>/auth`.
    - `FORWARDED_ALLOW_IPS` = the VPC CIDR, never `*` unless the security
      group allows only the ALB.
    - `SESSION_SECRET` (≥ 32 random characters) from Secrets Manager.
    - Entra app: "Groups assigned to the application", so tokens don't
      overflow.
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
