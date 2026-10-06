---
status: draft
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

### Components

One Python 3.12 service with two entrypoints from the same container image:

| Entrypoint | Role |
| ---------- | ---- |
| `web` | FastAPI + Jinja + HTMX portal: search, review queue, client registry, downloads |
| `worker` | Ingest and extraction jobs, pulled from a Postgres job table |

Repo layout (flat; split only when a file outgrows itself):

```
app/
  main.py        FastAPI routes + OIDC login
  db.py          psycopg connection, queries
  schema.py      ReferenceCase pydantic model (also the LLM JSON schema)
  connectors.py  SharePoint (Graph), Confluence (REST), S3 upload drop
  ingest.py      fetch → checksum → Docling → markdown → enqueue extract
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

- `documents`: id, source (`sharepoint|confluence|upload`), source_uri,
  checksum (sha256, unique, for dedupe), version, acl_groups `text[]`, s3_key of
  the original, markdown, fetched_at.
- `cases`: id, document_id, `data jsonb` (the `ReferenceCase`), status
  (`extracted|approved|rejected`), approved_by, approved_at, review_due
  (approved_at + 12 months), `tsv tsvector` (generated from title, industry,
  capabilities, tech, summary), plus GIN indexes on `tsv` and `data`.
- `clients`: id, name, aliases `text[]`, anonymised_label ("a Tier-1 UK bank"),
  referenceable `bool`, logo_allowed `bool`, owner.
- `jobs`: id, kind, payload jsonb, status, attempts, error. Workers claim jobs with
  `FOR UPDATE SKIP LOCKED`, so v1 needs no SQS.
- `generations`: audit record of who generated which output, from which case IDs,
  in which format, and with which anonymisation setting.

### `ReferenceCase` schema

title, client_id (nullable), industry, region, engagement_type, challenge,
solution, capabilities[], tech_stack[], outcomes[] {metric, value, quote},
duration_months, team_size, period {start, end}, summary (≤80 words).
Every non-empty field carries `source_quote`. `extract.py` rejects any field whose
quote does not appear verbatim (after whitespace normalisation) in the
document markdown, and marks it `unsourced` for the reviewer.

### Ingest flow

1. Connectors list changed items since the last run. Sources are SharePoint via
   Microsoft Graph (app registration with `Sites.Selected`), Confluence via REST
   (CQL on configured spaces), and manual upload to S3 from the portal for file
   shares and anything else. Each document stores its source ACL groups.
2. Checksum the content. An unchanged checksum is skipped, and a changed one
   becomes a new version that re-opens the case for review.
3. Docling converts docx/pptx/pdf/html to markdown. The original goes to S3.
4. An extract job calls the extraction model with structured output
   (the pydantic schema). The cached prefix holds the system prompt, schema and
   style guide. A bulk backfill uses the Bedrock batch API.
5. Anonymise-check: match names and aliases from the client registry, and the LLM
   flags organisation names that are not in the registry. The reviewer resolves
   both.
6. The case lands in the review queue as `extracted`.

A daily EventBridge schedule starts the `worker` sync. Uploads enqueue a job
immediately.

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
- Secrets Manager for Graph, Confluence and OIDC secrets.
- A Bedrock model-access IAM policy, scoped to the two model IDs.
- Private subnets with a VPC endpoint for Bedrock, so traffic stays off the public
  internet.
- CloudWatch logs. Document text is never logged.

The container image includes LibreOffice (headless) and the Docling models,
baked in so they are not downloaded at runtime.

### Delivery phases

1. **Core loop:** S3 upload ingest → extract → review → search → docx/md output.
2. **Brand outputs:** pptx and pdf on the real brand templates.
3. **Connectors:** SharePoint, then Confluence, with ACL capture.
4. **Hardening:** audit views, review-due reminders, recall test set, and
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
- **Stale cases.** `review_due` hides expired cases from search until a reviewer
  re-approves them.

## Verification

- `pytest`:
  - schema round-trip
  - quote check rejects an invented metric
  - anonymiser replaces names and aliases and blocks non-referenceable ones
  - ACL filter hides a document from a user outside its groups
  - render produces docx and pptx that reopen cleanly and contain no unfilled `{{ }}`
- End to end on a local stack (docker compose with Postgres): upload 5 real
  (sanitised) case documents → records extracted with quotes → approve → a search
  for a bid context returns the expected case in the top 3 → docx, pptx, pdf and
  md download and open.
- Recall set: 50 bid queries with expected cases, written with the bid team.
  Target ≥ 90% expected-case-in-top-3 before rollout.
- `terraform plan` is clean in the target account. A smoke test after deploy
  covers login via SSO, one ingest, and one generation.
- Security review: no document text in logs, S3 private with KMS encryption,
  Bedrock reached only through the VPC endpoint.
