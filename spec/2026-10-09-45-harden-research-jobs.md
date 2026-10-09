---
status: approved
issue: 45
intent: intent/2026-10-09-45-harden-research-jobs.md
---

# Spec: Harden research jobs

## Measurement: does Docling's `document_timeout` hold?

Run on 2026-10-09 in the `app` image (docling 2.135.0), with made-up
documents only: a generated 40-page text PDF (149 KB) and a generated HTML
table page (3.8 MB, under the 5 MB fetch cap). The models were loaded before
timing started.

| Input | `document_timeout` | Wall time | Result |
| ----- | ------------------ | --------- | ------ |
| PDF, 40 pages | none | 46.4 s | SUCCESS |
| PDF, 40 pages | 3 s | 6.1 s | PARTIAL_SUCCESS, 40 TIMEOUT errors, 3 KB of markdown |
| HTML, 3.8 MB | none | 40.6 s | SUCCESS |
| HTML, 3.8 MB | 1 s | 41.7 s | SUCCESS: **the timeout is ignored** |

Why: the PDF pipelines check the clock between page batches
(`docling/pipeline/standard_pdf_pipeline.py` ~line 897,
`base_pipeline.py` ~line 343). HTML goes through `SimplePipeline`, which
makes one `backend.convert()` call and never checks the clock. Even for PDF
the bound is soft: one batch overran the 3 s limit by 3 s.

So option (a) fails for HTML. Under the approved answer to Q1, research
uses (b): a child process that is killed on timeout.

Child-process cost, same image, made-up files:

| Child process | Small PDF | Small HTML | Slow HTML, 5 s limit |
| ------------- | --------- | ---------- | -------------------- |
| spawn (cold, loads the models) | 15.7 s | – | – |
| fork from a warm parent | 1.9 s | 0.4 s | killed at 5.1 s |

On 2 CPUs (`docker run --cpus 2`, which matches `worker_cpu = 2048`) without
a child: the 40-page PDF took 76.6 s, a 10-page PDF 21.7 s, a 1 MB HTML page
15.8 s and the 3.8 MB HTML page 62.3 s. Real PDFs with tables and images
are slower than this text-only one.

## Design

### 1. Docling time limit: forked child per page, research only

In `app/research.py`:

- `PAGE_CONVERT_TIMEOUT = 120` seconds per fetched page. 60 s, the lean in
  the intent, would cut a legitimate 40-page PDF on the 2-vCPU worker
  (76.6 s measured, text only). 120 s leaves headroom.
- New `convert(body, name) -> str`:
  - Fork a child with `multiprocessing.get_context("fork")` and a `Pipe`.
  - The child runs `ingest.to_markdown(body, name, max_pages=MAX_PDF_PAGES)`
    and sends back `("ok", markdown[:MAX_MARKDOWN])`. On an exception it
    sends `("err", type name)`.
  - The parent waits with `conn.poll(PAGE_CONVERT_TIMEOUT)`, then `recv()`,
    then `join()`. Receiving before joining avoids the pipe-buffer deadlock
    on large markdown.
  - On timeout the parent calls `kill()` then `join()`, and raises
    `ConvertTimeout(ResearchError)`.
  - If the child sends an error, or dies without sending one, the parent
    raises `ConvertFailed(ResearchError)`.
- Fork, not spawn. Spawn reloads the Docling models for every page (about
  14 s more per page, about 2 minutes on 8 pages). Fork shares the parent's
  loaded models copy-on-write.
- The parent warms up once per process, before its first fork:
  `ingest.warm()` (new, 2 lines in `app/ingest.py`). It runs
  `_converter().initialize_pipeline(InputFormat.PDF)` under `lru_cache`.
  Without it, every child would load the models itself.
- `run()` line 378 calls `convert(body, name)` instead of
  `ingest.to_markdown(...)[:MAX_MARKDOWN]`. The existing per-page `except`
  (lines 381-382) records `{"domain", "error": "ConvertTimeout"}` and
  carries on.
- `ingest.to_markdown` is not changed, so crawls and uploads keep their
  current behaviour (approved Q2).
- `MAX_PDF_PAGES = 40` stays.

Bound: Docling time per job is at most 8 × 120 s = 16 minutes. The issue
records the total worst case of a research job:

- Docling: at most 16 minutes.
- Fork cost: about 2 s per page.
- Network: about 10 minutes, as estimated in #60.
- One LLM call.

### 2. Per-domain rate limit shared across jobs: deferred (approved Q3)

No code change. There is one worker (`infra/variables.tf`
`worker_desired_count = 1`) and it runs one job per process
(`app/worker.py` line 94), so two research jobs never run at once.

- Add one comment above `self.last` in `Fetcher.__init__`
  (`app/research.py` line 217):
  `# shortcut: per job only; needs a shared (Postgres) limit before worker_desired_count > 1 (#45)`.
- Add the same note to the `worker_desired_count` variable's description in
  `infra/variables.tf`, so whoever scales up sees it.
- Tick the box on #45 with a comment saying why.

### 3. Brave retry (approved Q4)

`brave_search()` (`app/research.py` lines 279-287):

- One `httpx.Client`, up to `BRAVE_RETRIES = 2` retries, so 3 attempts at
  most.
- Retry on HTTP 429 and 5xx, and on `httpx.TimeoutException` /
  `httpx.TransportError`.
- Before each retry, `time.sleep(min(int(Retry-After) if it is digits else
  1, BRAVE_MAX_WAIT))`, with `BRAVE_MAX_WAIT = 10`. Brave's free plan is
  1 request/s. This is the same shape as `Confluence.get` (`app/crawl.py`
  lines 316-327).
- No retry on other 4xx, or on a missing key (raised before any request).
- When attempts run out:
  - HTTP status: raise the same `ResearchError(f"search failed
    ({status})")` as today.
  - Transport error: re-raise it, so `run()` records its type name as
    today (lines 389-392).
- Every attempt sends the same scrubbed query and the same headers.
  Nothing new leaves the VPC.

## Alternatives rejected

- **Docling `document_timeout` only.** The measurement shows HTML ignores
  it, and HTML is most of what research fetches.
- **`document_timeout` inside the child as well,** to keep a partial PDF
  rather than dropping it. It adds a second path and needs the partial text
  flagged as partial. Not asked for; revisit if many PDFs time out.
- **Spawn per page.** It is a cleaner process, but costs 15.7 s per page
  against 1.9 s for fork.
- **A long-lived converter child reused across pages.** That needs a
  restart-on-kill protocol, which is more code than fork-per-page for
  2 s per page saved.
- **A thread with a timeout.** A thread can't be killed, so the worker
  stays busy and the CPU stays in use.
- **A smaller HTML size cap instead of a time limit.** Time per byte varies
  with markup, so it bounds time only loosely and would also drop
  legitimate large pages.
- **Applying the limit in `ingest.to_markdown` for every caller.** Rejected
  by approved Q2: crawled documents are internal, and a long proposal must
  not be dropped.
- **Shared rate limit in Postgres now.** It does nothing at one worker
  (approved Q3).
- **Retrying the whole research job in the worker.** That repeats page
  fetches, against #1 step 18.

## Risks

- **Fork after torch has started threads** can deadlock a child (OpenMP
  thread pools, locks held during the fork). This held in the tests above,
  and the kill bounds it anyway: a hung child counts as `ConvertTimeout`
  and the page is skipped. The worker is never stuck.
- **The heartbeat thread may hold a DB socket when the child forks.** The
  child gets a copy of the file descriptor, never uses the DB, and exits
  through `multiprocessing`'s `os._exit`. It doesn't close or flush the
  parent's connection.
- **Memory.** One child at a time, sharing the parent's pages
  copy-on-write. A hostile page can grow the child, but `kill()` frees it
  and the ECS task limit (`worker_memory = 8192`) still applies.
- **The error type changes in `results.skipped`.** A Docling failure was
  recorded as Docling's exception class; it is now `ConvertFailed`. These
  rows are only shown as a count, so the loss is small.
- **Brave quota.** Up to 3 calls per job instead of 1, and only on
  failures.
- **Worst-case job time grows,** but has a bound. It stays covered by the
  heartbeat (#60), so it is never claimed twice.
- **Hosts.** Only the ECS worker, and the local compose `worker`. The web
  task never runs research.

## Verification

- `tests/test_research.py`, via `docker compose build app && docker compose
  run --rm app pytest`:
  - **Timeout:** the `web` fixture's `to_markdown` stub sleeps past a
    monkeypatched `PAGE_CONVERT_TIMEOUT = 1`. The page lands in `skipped`
    as `ConvertTimeout`, the next page is still stored, and the call
    returns in about 1 s.
  - **Child failure:** a stub that raises gives `ConvertFailed`. A stub
    that returns 30,000 characters is capped at `MAX_MARKDOWN` with no
    deadlock.
  - **Brave retry:**
    - 429 then 200 succeeds, with 2 Brave calls and a sleep of 1 s or the
      Retry-After value, capped at 10.
    - 503 three times fails with `search failed (503)` after 3 calls.
    - 401 fails after 1 call.
    - A `ConnectError` then 200 succeeds.
    - The key never appears in results or errors (existing assertion).
  - The existing tests stay green. The `web` fixture also stubs
    `ingest.warm`, so tests never load real models.
- A runtime check in the image with made-up files (the scripts used for
  the measurement above): a real `convert()` on the 3.8 MB HTML with a 5 s
  limit raises `ConvertTimeout` in about 5 s. A small real PDF converts
  through the forked child.
- The issue records the measured bound and the deferral note for the
  shared rate limit.
