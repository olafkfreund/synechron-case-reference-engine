---
status: approved
issue: 45
spec: spec/2026-10-09-45-harden-research-jobs.md
---

# Plan: Harden research jobs

Branch `fix/45-harden-research-jobs`, rebased on `origin/main` 8538b42.
All line numbers below are from that base.

## Approved decisions

Carried over from the spec; the intent and spec are not needed to implement this.

1. **Docling time limit: a forked child per fetched page, research only.**
   Docling's own `document_timeout` was measured (docling 2.135, made-up
   files):
   - PDF: it holds loosely. A 3 s limit stopped after 6.1 s with a partial
     result.
   - HTML: it is ignored. A 1 s limit still took 41.7 s on a 3.8 MB page.

   So the conversion runs in a child process that is killed on timeout.
   - Use fork, not spawn. A fork from a warm parent costs 1.9 s per small
     PDF and 0.4 s per HTML page. Spawn reloads the models every time:
     15.7 s.
   - The parent loads the models once, through a new `ingest.warm()`,
     before its first fork.
   - `ingest.to_markdown` itself is unchanged, so crawls and uploads keep
     their current behaviour.
2. **The limit is 120 s per page** (`PAGE_CONVERT_TIMEOUT = 120`), decided
   by the user. On 2 CPUs (the worker's `worker_cpu = 2048`) a 40-page
   text-only PDF took 76.6 s, so 60 s would drop legitimate PDFs. Docling
   time per job is bounded at 8 × 120 s = 16 min.
   - A page that times out is skipped as `ConvertTimeout`.
   - A page that fails or whose child dies is skipped as `ConvertFailed`.
   - Both go in `results.skipped` as `{domain, error: <type name>}`. The job
     carries on with the remaining pages.
   - `MAX_PDF_PAGES = 40` stays.

   *Done (coder):* `tests/test_research.py` 38 passed. *Deviation:* the
   hang test holds the lock in the test's own thread, taken before
   `rs.run` and released after, not in a separate background thread. The
   forked child still gets a copy of a held lock that nothing in it can
   release, so it hangs the same way and is killed. The test passes with a
   1 s limit, finishes in under 10 s, and leaves no child process.

3. **Shared per-domain rate limit: deferred.** With one worker running one
   job per process, it changes nothing today. The only changes are a
   comment at `Fetcher.__init__` and a note on `worker_desired_count`.
   Shared state, when it is built later, goes in Postgres.
4. **Brave retry.** Up to `BRAVE_RETRIES = 2` retries, so 3 attempts at most.
   - Retry on HTTP 429 or 5xx, and on `httpx.TransportError` (which
     includes `TimeoutException`).
   - Before each retry, sleep for `Retry-After` if it is all digits,
     otherwise 1 s. Cap the wait at `BRAVE_MAX_WAIT = 10`.
   - No retry on any other 4xx, or when the key is missing.
   - When attempts run out:
     - HTTP status: `ResearchError(f"search failed ({status})")`, as today.
     - Transport error: re-raise it, so `run()` records its type name, as
       today.
   - Every attempt sends the same scrubbed query and headers.
5. **Never repeat page fetches.** No retry on `Fetcher`, robots.txt or page
   GETs (#1 step 18). The SSRF protections and the 10 s / 5 MB caps are
   unchanged.

## Steps

1. **`app/ingest.py` lines 34-44: add `warm()`.**
   - Import `from docling.datamodel.base_models import InputFormat` inside
     the function, as `_converter` does.
   - Add, under `@lru_cache`:

     ```python
     @lru_cache
     def warm():
         """Load the Docling models once in this process, so forked children share them."""
         _converter().initialize_pipeline(InputFormat.PDF)
     ```

   → verify by `docker compose build app && docker compose run --rm --no-deps -T app python -c "import time; from app import ingest; t=time.monotonic(); ingest.warm(); print(round(time.monotonic()-t,1))"`.
   It prints a load time of several seconds. A second call in the same
   process returns at once.

   Traps:
   - No bind mount: always `docker compose build app` before running
     anything.
   - Run from the worktree directory. Compose has no `name:`, so the
     project is `refs-engine-45`, separate from the user's live stack.
   - Never `docker compose up` or `down`.

2. **`app/research.py`: forked conversion with a hard kill.**
   - Line 5: add `import multiprocessing`.
   - After `BadContentType` (line 52), add:

     ```python
     class ConvertTimeout(ResearchError):
         pass


     class ConvertFailed(ResearchError):
         pass
     ```

   - Next to `MAX_PDF_PAGES` (line 290), add
     `PAGE_CONVERT_TIMEOUT = 120  # seconds per fetched page; 40 text pages took 77 s on 2 vCPU (#45)`.
   - Add `convert(body, name)` below it:
     - `ingest.warm()`.
     - `parent, child = multiprocessing.get_context("fork").Pipe(duplex=False)`.
     - A `Process` target `_convert_child(child, body, name)`. It sends
       `("ok", ingest.to_markdown(body, name, max_pages=MAX_PDF_PAGES)[:MAX_MARKDOWN])`,
       or `("err", type(e).__name__)` on `Exception`.
     - `p.start()`, then `child.close()` in the parent.
     - `if not parent.poll(PAGE_CONVERT_TIMEOUT): p.kill(); p.join(); raise ConvertTimeout("conversion took too long")`.
     - Otherwise `kind, val = parent.recv()` inside `try/except EOFError`
       (the child died), then `p.join()`.
     - `"ok"` returns `val`. Anything else raises
       `ConvertFailed("conversion failed")`.
     - Receive before join. Joining first deadlocks when the markdown
       fills the pipe buffer.
     - Close `parent` in a `finally`.
   - Line 378: replace
     `md = ingest.to_markdown(body, name, max_pages=MAX_PDF_PAGES)[:MAX_MARKDOWN]`
     with `md = convert(body, name)`.
   - Line 217, above `self.robots, self.last = {}, {}`, add:
     `# shortcut: per job only; needs a shared (Postgres) limit before worker_desired_count > 1 (#45)`.
   - `tests/test_research.py`:
     - The `web` fixture (lines 131-135) also does
       `monkeypatch.setattr(ingest, "warm", lambda: None)`, so no test
       loads real models.
     - Add `test_slow_conversion_is_killed_and_the_page_skipped`:
       - Monkeypatch `rs.PAGE_CONVERT_TIMEOUT = 1`.
       - The `to_markdown` stub blocks forever on a `threading.Lock` that
         a background thread in the parent holds during the fork. This
         reproduces the fork-after-threads deadlock: the child copies the
         held lock and no thread in the child ever releases it.
       - Two pages: the first uses the hanging stub (switch on the name),
         the second converts normally.
       - Assert `results["skipped"]` contains
         `{"domain": ..., "error": "ConvertTimeout"}`.
       - Assert the second page is stored.
       - Assert `rs.run` returns in under 10 s (wall clock with
         `time.monotonic`, not the patched `rs.time.sleep`).
       - Assert `multiprocessing.active_children() == []` (the child was
         killed and reaped).
     - Add `test_conversion_error_and_large_output`:
       - A stub that raises gives `ConvertFailed`.
       - A stub returning `"x" * 2_000_000` comes back as exactly
         `MAX_MARKDOWN` characters, with no hang.

   → verify by `docker compose build app && docker compose run --rm app pytest tests/test_research.py -q`.
   All tests pass, the existing ones included.

   Traps:
   - **Fork after threads.** The worker has a heartbeat thread
     (`app/worker.py` line 67) and torch has its own thread pools. A
     forked child can inherit a held lock and hang. The design accepts
     this because `kill()` turns any hang into `ConvertTimeout`. The
     lock-held test above is the proof, and must not be dropped or
     weakened.
   - **The child must never touch the DB.** It inherits the parent's open
     file descriptors. `multiprocessing` ends a fork child with
     `os._exit`, so nothing is closed or flushed. Don't add `atexit` or
     DB work to `_convert_child`.
   - **Monkeypatched stubs reach the child only through fork.** That is why
     the tests work; don't switch to spawn.
   - **Test data is made up.** Use the fake hosts in the `web` fixture,
     and no real sites.

3. **`app/research.py` lines 279-287: Brave retry.** Replace the body of
   `brave_search`:
   - Keep the missing-key check first. It raises before any request.
   - Create the `httpx.Client(transport=TRANSPORT, timeout=TIMEOUT,
     trust_env=False)` once, outside the loop.
   - Loop `for attempt in range(BRAVE_RETRIES + 1)`:
     - Run the `get` inside `try/except httpx.TransportError`. On the last
       attempt, re-raise.
     - On 200, return the results.
     - On 429 or `>= 500`, if attempts remain:
       `time.sleep(min(int(ra) if ra.isdigit() else 1, BRAVE_MAX_WAIT))`,
       where `ra = r.headers.get("Retry-After", "")`, then `continue`.
     - Otherwise `raise ResearchError(f"search failed ({r.status_code})")`.
   - After a transport error, sleep 1 s before the next attempt.
   - Constants next to `BRAVE` (line 27):
     `BRAVE_RETRIES, BRAVE_MAX_WAIT = 2, 10  # quota: each retry is a billed call`.
   - `tests/test_research.py`:
     - `Web.__init__` gets `self.brave_replies = []`.
     - In `__call__` (line 117), when the list is non-empty, `pop(0)` and
       either `raise` it (an exception instance) or return it (an
       `httpx.Response`). Otherwise reply 200 as now.
     - Add tests:
       - `[Response(429, headers={"Retry-After": "3"})]` gives done, 2
         Brave calls, and `3` in `web.sleeps`.
       - `[Response(503)] * 3` gives failed with
         `"search failed (503)"` after 3 calls.
       - `[Response(401)]` gives failed after 1 call.
       - `[httpx.ConnectError("x")]` gives done after 2 calls.
       - `Retry-After: 999` sleeps 10.
     - The existing key-not-stored assertion stays.

   → verify by `docker compose build app && docker compose run --rm app pytest tests/test_research.py -q`.
   All pass. Also run `test_one_request_per_domain_per_second` (line 203),
   which still checks `0 < s <= 1` only for fetches. Brave sleeps happen
   only in the new tests.

   Traps:
   - `web.sleeps` records every `rs.time.sleep`. Assert on the specific
     values, not on the list length shared with fetch sleeps.
   - Never retry a fetch (decision 5).

4. **`infra/variables.tf` lines 90-93: add a description to
   `worker_desired_count`:**
   `"Research jobs rate-limit each domain per job only; add a shared limit before raising this above 1 (#45)."`

   → verify by `terraform -chdir=infra fmt -check` if terraform is
   installed. Otherwise review the diff: one added line, no value changed.

   Traps: none. Description only, so there is no plan or apply impact
   beyond metadata.

5. **Runtime check with real Docling (no edit).**

   → verify by:

   ```sh
   docker compose run --rm --no-deps -T app python - <<'EOF'
   import time
   from app import research as rs
   rs.PAGE_CONVERT_TIMEOUT = 5
   rows = "".join(f"<tr><td>r{i}</td><td>made-up {i}</td><td><ul><li>a</li><li>b</li></ul></td></tr>" for i in range(40000))
   big = f"<html><body><table>{rows}</table></body></html>".encode()  # 3.8 MB, synthetic
   rs.ingest.warm()
   t = time.monotonic()
   try:
       rs.convert(big, "s.html")
       print("NOT KILLED")
   except rs.ConvertTimeout:
       print("timeout", round(time.monotonic() - t, 1))
   print(len(rs.convert(b"<html><body><h1>Made up</h1><p>ok</p></body></html>", "x.html")))
   EOF
   ```

   Expected:
   - `timeout` printed at about 5-6 s after warm-up.
   - Then a positive length for the small page, which shows the warm
     parent still forks a working child after a kill.

   Then record on #45:
   - Docling time per job is bounded at 16 min.
   - Adding about 2 s fork cost per page, about 10 min of network (#60)
     and one LLM call gives the job's worst case.
   - The shared-rate-limit deferral note.

   Traps:
   - Synthetic HTML only.
   - `--no-deps`, so no DB container is started.

## Tests

- `docker compose build app && docker compose run --rm app pytest`: the
  whole suite is green.
- New tests:
  - the timeout kill with a lock held across the fork;
  - error and large output;
  - five Brave retry cases.
- Step 5's runtime check prints `timeout ~5s` and then a non-zero length.

## Rollback

- `git revert` the implementation commits.
- There is no schema or data migration. Old rows in `results.skipped`
  holding `ConvertTimeout`/`ConvertFailed` are plain strings, and old code
  reads them fine.
- The `infra/variables.tf` description change needs no apply to roll back.

## Handoff

Steps 1-4 edit four files: `app/ingest.py`, `app/research.py`,
`infra/variables.tf` and `tests/test_research.py`. That is 3 or more steps
that edit files, so per the model split this goes to the `coder` agent:
- one `coder` started with this plan path and step 1;
- later steps sent with `SendMessage`;
- review by a fresh Opus agent given only this plan path and the diff.

Step 5 (runtime check and the issue comment) is done by the session model.
