---
status: approved
issue: 45
author: olafkfreund
---

# Intent: Harden research jobs

## Problem

Three limits were recorded when research was built (#1 plan, steps 18 and
19). All three are in `app/research.py`.

1. **Docling has no time limit on fetched pages.** `run()` (line 378) passes
   every fetched page to `ingest.to_markdown(..., max_pages=MAX_PDF_PAGES)`.
   The only bound is 40 pages per PDF (`MAX_PDF_PAGES`, line 290).
   `to_markdown` (`app/ingest.py` lines 40-44) calls Docling in the worker
   process, and nothing stops a conversion that runs long. A page from the
   open web can be built to be slow, on purpose or not. The worker
   heartbeat (`app/worker.py` lines 42-50, from #60) keeps a live job out of
   the stale window, and its comment puts research on 8 large PDFs at about
   75 minutes. So one slow page holds the worker for as long as Docling
   runs. At the default of one worker (`infra/variables.tf`
   `worker_desired_count = 1`, and one job per process per `app/worker.py`
   line 94), every other job waits behind it, including extracts and crawls.

2. **The per-domain rate limit covers one job only.** `Fetcher` keeps the
   last request time per host in `self.last` (lines 217, 224-227). That is
   1 request per second per domain inside one job. Two research jobs in two
   worker containers can hit the same site together, and each one starts
   its own clock. Today this cannot happen: there is one worker and it runs
   one job at a time. It starts to matter once `worker_desired_count` goes
   above 1.

3. **A Brave failure fails the whole job.** `brave_search()` (lines
   279-287) makes one request. Any status other than 200 raises
   `ResearchError("search failed (<status>)")`. A timeout or connection
   error is not caught there, so it surfaces as its exception type.
   `run()` records the failure on the row (lines 389-392), and the worker
   never retries research by design (plan #1 step 18: "a retry would repeat
   the fetches"). The user sees "failed" for a short Brave outage or a 429,
   and has to send the query again.

## Proposed outcome

- A fetched page whose Docling conversion passes a set time limit is
  skipped. It is recorded in `results.skipped` as domain plus error type,
  like any other bad page, and the job carries on with the other pages.
- The worst-case runtime of a research job has an upper bound that can be
  stated, and the issue records it.
- Two research jobs running together never send more than the set rate to
  one domain. (If the approver decides to defer this, the issue says it
  does not apply while there is one worker.)
- A temporary Brave failure (429, 5xx, timeout, connection error) is retried
  a bounded number of times before the job fails. A permanent one (missing
  key, 401/403, 4xx) still fails at once with the same message as today.
- Page fetches are still never repeated: the retry covers the single Brave
  call only.

## Affected users and systems

- `app/research.py`: `Fetcher`, `brave_search()`, `run()`.
- Possibly `app/ingest.py` `to_markdown()`. It is shared with document
  ingest for crawls and uploads.
- Possibly `sql/schema.sql`, if the shared rate limit keeps its state in
  Postgres.
- The worker on ECS, and the jobs that queue behind a research job.
- Bid team users of industry research: fewer failed jobs, and possibly
  fewer pages per result when slow pages are skipped.
- `tests/test_research.py`.

## Constraints

- Confidential documents are never sent to a third-party model. A Brave
  retry sends exactly the same scrubbed, user-previewed query and nothing
  else. Docling stays local.
- No retries against the fetched sites (#1 step 18). The retry covers the
  Brave search request only, never page fetches or robots.txt.
- The SSRF protections in `Fetcher` (vetted IP, SNI, redirect re-vetting,
  `trust_env=False`) and the 10 s / 5 MB fetch caps stay as they are.
- Errors stored on the row are still domain plus exception type, or a fixed
  message. They never hold URLs, content or the Brave key.
- No new dependency. Shared state goes in Postgres, which we already run,
  not Redis or similar.
- Test data is public or made up: `httpx.MockTransport` and synthetic
  documents. No live Brave call and no real site in tests.
- Tests pass with `docker compose build app && docker compose run --rm app pytest`.
- Do not stop or restart the dev stack (`docker compose up`/`down`).

## Open questions

1. **How to enforce the Docling time limit.**
   (a) Docling's own pipeline timeout (`document_timeout`, docling 2.135
   per `requirements.lock`). No new code path, but it is checked between
   pages, so one stuck page or the HTML backend may not honour it.
   (b) Run the conversion in a child process and kill it on timeout, as
   `app/render.py` does for PDF (lines 235-260). This is a hard bound, but
   each page pays for a process and for loading the Docling models.
   (c) Both. **Lean: (a) if a test with a made-up slow document shows it
   holds for PDF and HTML; otherwise (b), for research only.**
2. **Research only, or all ingest?** **Lean: research only.** Fetched pages
   are untrusted. Crawled documents are internal, and capping them could
   drop a real long proposal.
3. **Shared per-domain limit: now or deferred?** With one worker running
   one job at a time it has no effect, and it needs shared state (a
   Postgres row per host, locked while waiting). **Lean: defer.** Close
   that box with a note that it applies when `worker_desired_count > 1`,
   unless the approver plans to scale workers soon. If it is built, use
   Postgres.
4. **Which Brave failures to retry, and how often.** **Lean: 429, 5xx,
   timeouts and connection errors; at most 2 retries; wait for Retry-After,
   capped (the same pattern as `crawl.py` lines 316-327); no retry on
   other 4xx.** Each retry uses Brave quota, so the cap must stay small.
5. **The time limit value.** **Lean: decide in the spec from a measurement
   on made-up documents (for example 60 s per page),** and record the
   resulting worst-case job runtime on the issue.
