---
status: approved
issue: 0
intent: intent/2026-10-06-0-reference-engine.md
---

# Spec: Customer reference engine

## Design

### Core idea: extract once, search records, render from templates

There is no chunk-level RAG and no separate vector database. Each case document is read
**once** at ingest. An LLM turns it into one structured `ReferenceCase` record,
with a source quote for every field. A human approves the record. Search runs over
approved records, and an LLM only picks and tailors the best ones for a bid.
Outputs are rendered by filling the corporate templates with the record's JSON.
At hundreds to low thousands of documents, a bid query ("bank, cloud migration,
UK") is a filter plus a ranking over a few thousand records. Postgres does this
directly, and every record stays reviewable and auditable. Two independent
research passes (Fable web research, Codex) reached the same conclusion.

Two additions sit beside that core:
- A **crawler** walks the configured sources and decides which documents
  describe client work before anything is extracted.
- An **online research** module answers "how does the industry or a vendor do
  X?" with cited public sources. Its output is kept apart from case records and
  is never presented as our own delivery.

### Components

One Python 3.12 service with two entrypoints from the same container image:

| Entrypoint | Role |
| ---------- | ---- |
| `web` | FastAPI + Jinja + HTMX portal: search, research, review queue, client registry, sources, downloads |
| `worker` | Crawl, triage, extraction and research jobs, pulled from a Postgres job table |

Repo layout (flat; split only when a file outgrows itself):

```
app/
  main.py        FastAPI routes + OIDC login
  db.py          psycopg connection, queries
  schema.py      ReferenceCase pydantic model (also the LLM JSON schema)
  crawl.py       crawlers: SharePoint (Graph delta), Confluence (REST/CQL), S3 prefixes
  ingest.py      fetch → checksum → Docling → markdown → triage → enqueue extract
  research.py    web search → fetch → cited claims → comparison
  extract.py     LLM structured extraction + provenance check
  search.py      FTS + filters → candidates → LLM rank/tailor
  anonymise.py   client registry replacement + unlisted-name flagging
  render.py      docxtpl / python-pptx / LibreOffice PDF / markdown
  llm.py         LiteLLM calls, model aliases from env
  worker.py      job loop
  templates/     Jinja HTML for the portal
brand/           corporate reference.docx and master.pptx (from marketing)
sql/schema.sql
infra/           Terraform
tests/
```

### Data model (Postgres 16, `sql/schema.sql`)

- `sources`: id, kind (`sharepoint|confluence|s3`), root (site/drive, space key,
  or bucket prefix), include/exclude patterns, enabled, schedule, and the crawl
  cursor (Graph delta link, last-modified time, or S3 listing marker).
- `documents`: id, source_id, source_uri, title, checksum (sha256, unique, for
  dedupe), version, acl_groups `text[]`, s3_key of the original, markdown,
  `kind` from triage (`case|proposal|deck|other`), `tsv` over markdown,
  fetched_at, deleted_at.
- `cases`: id, document_id, `data jsonb` (the `ReferenceCase`), status
  (`extracted|approved|rejected`), approved_by, approved_at, review_due
  (approved_at + 12 months), `tsv tsvector` (generated from title, industry,
  capabilities, tech, summary), plus GIN indexes on `tsv` and `data`.
- `clients`: id, name, aliases `text[]`, anonymised_label ("a Tier-1 UK bank"),
  referenceable `bool`, logo_allowed `bool`, owner.
- `jobs`: id, kind, payload jsonb, status, attempts, error. Workers claim jobs with
  `FOR UPDATE SKIP LOCKED`, so v1 needs no SQS.
- `research`: id, question, the query text actually sent out, results jsonb
  (claims with URL, publisher, quote, retrieved_at), created_by, created_at.
  Results are cached for 30 days and re-run on request.
- `generations`: audit record of who generated which output, from which case IDs,
  in which format, and with which anonymisation setting.

### `ReferenceCase` schema

title, client_id (nullable), industry, region, engagement_type, challenge,
solution, capabilities[], tech_stack[], outcomes[] {metric, value, quote},
duration_months, team_size, period {start, end}, summary (≤80 words).
Every non-empty field carries `source_quote`. `extract.py` rejects any field whose
quote does not appear verbatim (after whitespace normalisation) in the
document markdown, and marks it `unsourced` for the reviewer.

### Crawl and ingest flow

Admins register sources in the portal. Every crawl is resumable from its
stored cursor, so the first run is a full crawl and later runs are incremental.

| Source | How it is crawled |
| ------ | ------------------ |
| SharePoint / OneDrive | Microsoft Graph `drive/root/delta` per configured site or drive. App registration with `Sites.Selected`, granted per site. The delta link is stored as the cursor. Item permissions are read for `acl_groups`. |
| Confluence | REST API with CQL `space in (...) and lastmodified > cursor`. This covers pages (body as HTML) and attachments. Space and page restrictions are read for `acl_groups`. |
| File shares | Synced to an S3 prefix with AWS DataSync (scheduled). The crawler lists the prefix. ACL groups are configured per prefix, because NTFS ACLs are not carried over. |
| Manual upload | Lands in the S3 upload prefix and is crawled immediately. |

Rules for every source:
- Only include/exclude patterns and file types we can parse (docx, pptx, pdf,
  html, Confluence pages) are fetched. Archives and media are skipped.
- Items deleted at the source get `deleted_at`, and their cases leave search.
- Crawls are rate-limited and back off on HTTP 429 (Graph and Confluence both throttle).

Per document:
1. Checksum the content. An unchanged checksum is skipped, and a changed one
   becomes a new version that re-opens the case for review.
2. Docling converts docx/pptx/pdf/html to markdown. The original goes to S3.
3. **Triage:** the extraction model reads the first ~2,000 tokens and returns a
   `kind` (structured output). Only `case` (and `proposal`/`deck` when it
   describes delivered work) goes on to extraction. Everything else stays
   indexed in `documents.tsv` as searchable background information, and is shown
   to users as "related documents", but never turned into a reference.
4. An extract job calls the extraction model with structured output
   (the pydantic schema). The cached prefix holds the system prompt, schema and
   style guide. A bulk backfill uses the Bedrock batch API.
5. Anonymise-check: match names and aliases from the client registry, and the LLM
   flags organisation names that are not in the registry. The reviewer resolves
   both.
6. The case lands in the review queue as `extracted`.

A daily EventBridge schedule enqueues a crawl job per enabled source. Admins can
also start a crawl from the sources page, which shows the last run, counts
(found, new, changed, triaged as case, failed) and errors.

### Query and generate flow

1. The user enters a bid context in free text, with optional filters (industry,
   region, tech).
2. `search.py` runs a Postgres FTS (`websearch_to_tsquery`) plus jsonb filters,
   restricted to `status = approved` and to `acl_groups` that overlap the user's
   groups from the OIDC token. It returns the top 20.
3. The drafting model gets the 20 summaries plus the bid context. It returns the
   top 3 IDs, with a 1-line reason each and tailored wording. Tailoring may
   rephrase but must not add facts: the output is checked so that every metric
   value already exists in the record.
4. The user picks a case (or several) and a format. `render.py` applies
   anonymisation by default: the registry label replaces the client name unless
   `referenceable`. It then renders:
   - **docx**: `docxtpl` with the brand Word template
   - **pptx**: `python-pptx` filling *named* placeholders in a dedicated
     "Reference case" layout of the brand master, one slide per case
   - **pdf**: LibreOffice headless on the docx or pptx
   - **markdown**: a Jinja text template, shown with a copy button
5. An audit row goes to `generations`. The file streams back to the user and is
   not stored.

### Online research (`research.py`)

Purpose: cited public evidence that a capability is standard, out of the box,
or common industry practice, and a comparison of how others approach it. It
covers capabilities we have delivered as well as ones we have not.

Flow:
1. The user asks a question in the portal, either free text or "Research
   this" on a case, which seeds the question from the case's capabilities and
   tech stack.
2. Before anything leaves AWS, the query is built **only from capability and
   product terms**. The anonymiser removes every client name and alias in the
   registry, and the query is shown to the user before it is sent.
3. Web search API: **Brave Search API**, chosen for its independent index and
   zero-data-retention option. It is one module behind an env key, so it can be
   swapped for Tavily, Exa or another provider. Bedrock's Claude has no built-in
   web search tool, so the app calls the search API itself.
4. Fetch the top ~8 results with `httpx` and convert them to markdown with
   Docling. Results are ranked to prefer vendor documentation, standards bodies
   and analyst sources over blogs. Fetches respect robots.txt and a per-domain
   rate limit.
5. The drafting model returns structured claims. Each claim carries the
   statement, the source URL, the publisher, a verbatim quote and a type
   (`out_of_the_box | configuration | industry_practice | vendor_claim`). The
   quote check from extraction is reused, so a claim whose quote is not on the
   fetched page is dropped.
6. Comparison view: a table of approaches across sources. When started from a
   case, our approach sits beside them.

Guardrails:
- Research output is never written into `cases`.
- Rendered outputs put it in a separate **"Industry context"** section, with
  footnoted links and the retrieval date. Wording is fixed to "is standard in /
  is supported out of the box by", never "we have delivered".
- Generating a docx or pptx with industry context and **no** supporting case
  requires the user to tick an "industry context only" acknowledgement, which is
  recorded in `generations`.

### LLM layer (`llm.py`)

LiteLLM as a library, not the proxy. Two aliases come from env:
`EXTRACT_MODEL` (a small Claude model on Bedrock) and `DRAFT_MODEL` (a mid-tier
Claude model on Bedrock). The defaults go through Bedrock in our own AWS account
and region. The Anthropic API, Azure OpenAI and Ollama are configuration changes
only. Only models whose Bedrock terms keep data out of provider retention may be
configured; this is checked once at setup and noted in the README.

### Auth and access

OIDC login against the corporate IdP, with an authlib session cookie. Roles come
from IdP groups:
- `user`: search and generate
- `reviewer`: approve and edit cases
- `admin`: client registry and sources

Document visibility is the intersection of the user's groups and the document's
`acl_groups`, as captured at ingest.

### AWS deployment (`infra/`, Terraform)

- ECS Fargate: `web` service behind an internal ALB with HTTPS. `worker` runs as
  a service with 1 task plus the scheduled sync.
- RDS PostgreSQL 16, single-AZ in v1, encrypted, automated backups.
- S3 bucket for originals: SSE-KMS, private, versioned.
- Secrets Manager for Graph, Confluence, OIDC and search API secrets.
- A Bedrock model-access IAM policy, scoped to the two model IDs.
- Private subnets with a VPC endpoint for Bedrock, so traffic stays off the public
  internet.
- NAT gateway for outbound HTTPS only (Graph, Confluence, search API, research
  page fetches). The worker reaches the internet; the web service does not need to.
- DataSync agent and task syncing on-prem file shares to the S3 crawl prefix.
- CloudWatch logs. Document text is never logged.

The container image includes LibreOffice (headless) and the Docling models,
baked in so they are not downloaded at runtime.

### Delivery phases

1. **Core loop:** S3 prefix crawl and upload → triage → extract → review → search
   → docx/md output.
2. **Brand outputs:** pptx and pdf on the real brand templates.
3. **Crawlers:** SharePoint (Graph delta), then Confluence, with ACL capture,
   plus DataSync for file shares.
4. **Online research:** search, cited claims, comparison view, and the
   "Industry context" output section.
5. **Hardening:** audit views, review-due reminders, recall test set, and
   `pgvector` only if recall < 90%.

## Alternatives rejected

- **Chunked RAG + vector DB (Qdrant, Chroma, OpenSearch):** returns fragments of
  cases and has to re-extract fields on every query. It is also more infrastructure,
  and it's harder to review what the system "knows". `pgvector` stays available as a
  same-database add-on if needed.
- **Long-context stuffing of the whole corpus:** about 500K tokens per query at
  1,000 cases is too costly and slow. It is used only on the top 20.
- **MarkItDown / Unstructured / LlamaParse:** MarkItDown is weak on PDFs and tables.
  Unstructured is heavier than needed. LlamaParse is SaaS, so data would leave AWS.
- **pandoc for outputs:** weak on PowerPoint masters.
- **LLM-written python-pptx/docx code, Claude docx/pptx skills, Gamma/Presenton:**
  non-deterministic layout that drifts off brand, plus data egress for the SaaS
  options.
- **Streamlit/Gradio UI:** awkward for SSO, roles, a review workflow and file
  downloads at org scale.
- **LiteLLM Proxy as a separate service:** another service to run. Adopt it only when
  central budgets or keys across apps are needed.
- **SQS/Step Functions for jobs:** a Postgres job table is enough at this volume.
- **Webhooks / change notifications instead of scheduled crawls:** lower lag,
  but subscriptions expire and need renewal infrastructure. Delta crawls are
  enough with a daily lag. Revisit if same-day freshness matters.
- **Crawling file shares directly over SMB from Fargate:** needs network paths
  into on-prem and SMB credentials in the app. DataSync to S3 keeps the app
  cloud-only.
- **Extracting every crawled document:** this wastes LLM spend on non-case
  documents and floods the review queue. A cheap triage step goes first.
- **Claude's built-in web search tool:** not available on Bedrock. It would also
  tie research to the Anthropic API route.
- **Tavily / Exa as the default search provider:** both are viable and easy to
  swap in. Brave is the default because of its zero-data-retention option.
- **Buy (Loopio, Responsive, AutogenAI):** these are RFP answer-library tools that
  don't generate branded reference cases from closure reports. Revisit if the bid
  team wants a full answer library.

## Risks

- **Invented or inflated metrics in a bid.** Mitigated by the verbatim-quote check,
  the human approval gate, and the "no new numbers" check on tailored text. Residual
  risk: a reviewer approves without reading. Mitigation: show the source quote next
  to each field.
- **Confidential client named without permission.** Anonymisation is on by default
  and driven by the registry. Unlisted names are flagged at review, and a
  generated output is blocked if it contains any registry name that is not
  `referenceable`.
- **ACL drift.** Permissions change in SharePoint or Confluence after ingest. The
  daily sync re-reads ACLs, so there is up to a day of lag; the owner must accept
  this or we move to per-request checks.
- **Template brittleness.** Marketing changes the master and placeholder names
  break. A render test runs against `brand/` in CI, and the layout and placeholder
  names are documented for marketing.
- **Parser misses** on scanned PDFs or image-heavy decks. Docling OCR is
  enabled, and low-text documents are flagged in the review queue.
- **Model availability or terms on Bedrock change.** Models are env aliases, and the
  provider can be swapped through LiteLLM.
- **Overstated industry claims.** A bid says "standard" based on a vendor blog
  or an outdated page. Mitigated by verbatim quotes, source type ranking,
  retrieval dates in the output, and a fixed "Industry context" section that
  never merges with our case claims.
- **Query leakage.** A client name or confidential detail goes to the search
  provider. Mitigated by building queries from capability terms only, scrubbing
  them with the registry, previewing the query, and the zero-retention plan.
- **Crawl volume and throttling.** The first SharePoint crawl can be large, and
  Graph throttles. Mitigated by resumable cursors, backoff, include patterns,
  and triage keeping LLM cost proportional to case documents only.
- **Over-broad Graph permissions.** Use `Sites.Selected` per site, never
  tenant-wide `Sites.Read.All`.
- **Stale cases.** `review_due` hides expired cases from search until a reviewer
  re-approves them.

## Verification

- `pytest`:
  - schema round-trip
  - quote check rejects an invented metric
  - anonymiser replaces names and aliases and blocks non-referenceable ones
  - ACL filter hides a document from a user outside its groups
  - render produces docx and pptx that reopen cleanly and contain no unfilled `{{ }}`
  - crawl resumes from its cursor and marks deleted items
  - triage routes a non-case document away from extraction
  - research drops a claim whose quote is not on the fetched page, and strips a
    registry client name from the outgoing query
- End to end on a local stack (docker compose with Postgres): upload 5 real
  (sanitised) case documents → records extracted with quotes → approve → a search
  for a bid context returns the expected case in the top 3 → docx, pptx, pdf and
  md download and open.
- Crawl check: point a test SharePoint site and Confluence space with known
  contents at the crawler. The full crawl finds every file, a second run fetches
  nothing new, and editing one file re-ingests only that file.
- Research check: 10 capability questions with known out-of-the-box answers
  (e.g. vendor docs). Each claim links to a page containing its quote.
- Recall set: 50 bid queries with expected cases, written with the bid team.
  Target ≥ 90% expected-case-in-top-3 before rollout.
- `terraform plan` is clean in the target account. A smoke test after deploy
  covers login via SSO, one ingest, and one generation.
- Security review: no document text in logs, S3 private with KMS encryption,
  Bedrock reached only through the VPC endpoint.
