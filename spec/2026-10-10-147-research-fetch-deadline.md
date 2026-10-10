---
status: draft
issue: 147
intent: intent/2026-10-10-147-research-fetch-deadline.md
---

# Spec: a slow web server cannot hold the research worker

## Design

Every request hop in `Fetcher._get` (`app/research.py`) runs under a wall-clock
limit of `TIMEOUT` (10 s), from before the request is sent to the last body byte.

- **New helper in `app/research.py`, `_within(seconds, fn)`.** It runs `fn` in a
  daemon thread and waits with `Thread.join(seconds)`. If `fn` raised, it re-raises
  that exception. If the thread is still running, it raises
  `ResearchError("fetch took too long")`, the same error the body loop already
  raises. The abandoned thread holds only its own socket, and it ends on the
  socket's next read: httpx's per-read `timeout=TIMEOUT` is still set, and the
  response is closed (next bullet). It is a daemon, so it never blocks worker
  shutdown.
- **`_get` splits each hop into `_hop(u, ip, limit)`,** which holds the current
  body of the `with self.http.stream(...)` block: status, redirect location,
  content type and the body loop with its byte limit. `_get` calls
  `_within(TIMEOUT, lambda: self._hop(...))` per hop. Redirect handling
  (`urljoin`, the robots check on the new URL, `continue`) stays in `_get`
  after the hop returns, so vetting every hop is unchanged.
- **The stream is closed on timeout.** `_hop` stores its open response on
  `self._open`, and `_within`'s timeout path calls `self._open.close()` before
  raising, so the connection is not left reading.
- **What is unchanged:** `vet_url`, `public_ip`, the pinned IP with Host and SNI,
  `follow_redirects=False`, `trust_env=False`, and the per-domain one-second
  pacing. The pacing stays in `_get`, outside the limit.
- **robots.txt.** `allowed()` already catches `ResearchError`. Anything other
  than an `HTTP 4xx` parses as `Disallow: /`, so a timed-out robots.txt is
  disallowed, as the intent requires.
- **The run carries on.** `run()` already records a failed page under
  `skipped` and moves to the next. A whole job is therefore bounded by about
  `MAX_RESULTS × (MAX_REDIRECTS + 1) × TIMEOUT` plus robots fetches, plus
  `brave_search`, which already has its own timeout and retries.

## Alternatives rejected

- **`signal.setitimer` / `SIGALRM`.** It interrupts the read directly, but it
  works only in the main thread, it shares one timer per process with any other
  user, and an alarm raised inside httpcore's cleanup can leave the pool in an
  odd state.
- **A custom httpcore network backend that shortens each read to the time
  left.** This is the cleanest interrupt, but it relies on private httpx and
  httpcore internals (`HTTPTransport._pool`), which break on upgrade.
- **A process per fetch, like Docling's (#45).** It is heavy for 8 to 30 small
  requests per job, and the SSRF client would have to be rebuilt in each child.
- **One deadline for the whole `_get`, redirects included.** The intent asks
  for a limit per request. Per hop is also simpler to reason about, since
  `MAX_REDIRECTS` already bounds the hop count.

## Risks

- **An abandoned thread lingers** until its socket's next read fails, at most
  one per-read `TIMEOUT` after the close. With one worker and at most about 40
  hops per job, a hostile site could leave a handful of short-lived threads. That
  is acceptable; the alternative is a stuck worker.
- **Thread safety.** `httpx.Client`'s pool is thread-safe for concurrent
  requests, and an abandoned hop and the next hop may briefly overlap. The
  per-host pacing dict is touched only by the calling thread.
- **Behaviour.** Only pages that were slower than 10 s overall now fail. Before,
  those succeeded only if the body loop happened to pass the check.

## Verification

New tests in `tests/test_research.py`. Each must fail on main, and must finish in
about a second:

- `test_slow_headers_hit_the_wall_clock_limit`:
  - Patch `rs.TIMEOUT` to `0.3`.
  - The `Web` fake's handler blocks on `threading.Event().wait(3)` for the page,
    before returning a response.
  - `Fetcher().fetch(url)` raises `ResearchError("fetch took too long")` in under
    1.5 s.
  - On main it blocks for the whole 3 s and then succeeds.
- `test_slow_robots_txt_is_disallowed`:
  - The same fake blocks on `/robots.txt`.
  - `Fetcher().allowed(url)` returns False within the limit.
- `test_run_skips_a_slow_page_and_keeps_the_others`:
  - Brave returns two URLs, and one host blocks.
  - `run()` records one page and one skipped entry with the timeout error.

Trap: the `Web` fixture patches `time.sleep` globally, through `rs.time.sleep`,
so the blocking handler must use `threading.Event().wait`, not `time.sleep`.

Then the full suite: `docker compose build app && docker compose run --rm app
timeout 900 pytest -q -p no:cacheprovider`.
