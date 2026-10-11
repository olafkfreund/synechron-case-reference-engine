---
status: approved
issue: 147
spec: spec/2026-10-10-147-research-fetch-deadline.md
---

# Plan: a slow web server cannot hold the research worker

Approved decisions, carried over from the spec:

- Every request hop in `Fetcher._get` (`app/research.py`) runs under a wall-clock
  limit of `TIMEOUT` (10 s), from before the request is sent to the last body byte.
- New helper `_within(seconds, fn)`: runs `fn` in a daemon thread, waits with
  `Thread.join(seconds)`. If `fn` raised, re-raise that exception. If the thread is
  still running, raise `ResearchError("fetch took too long")`.
- `_get` moves the body of its `with self.http.stream(...)` block into a new
  method `_hop`, and calls it through `_within(TIMEOUT, ...)` once per hop.
  Redirect handling (`urljoin`, the robots check on the new URL, `continue`)
  stays in `_get` after the hop returns, so every hop is still vetted.
- On timeout the open response is closed, so the connection stops reading.
- Unchanged: `vet_url`, `public_ip`, the pinned IP with Host and SNI,
  `follow_redirects=False`, `trust_env=False`, and the one-second per-domain
  pacing. The pacing stays in `_get`, outside the limit.
- robots.txt: `allowed()` already turns any `ResearchError` other than `HTTP 4xx`
  into `Disallow: /`, so a timed-out robots.txt is disallowed. No change there.
- `run()` already records a failed page under `skipped` and carries on. No change
  there.
- No new dependency (`threading` is stdlib). No network in tests; use `TRANSPORT`.

One refinement of the spec's wording: the spec stores the open response on
`self._open`. An abandoned hop thread could set `self._open` after the next hop
has started, and then the next hop's timeout would close the wrong response. So
`_hop` gets a per-call holder list instead (`opened`), which `_get` passes in and
closes on timeout. The behaviour is the spec's.

## Steps

1. `app/research.py`: add the helper and split the hop. → verify by
   `python -c "import ast,sys; ast.parse(open('app/research.py').read())"` and the
   existing tests in step 3.
   - Add `import threading` to the stdlib imports (lines 1–10, alphabetical:
     after `import socket`).
   - Above `class Fetcher` (line 240), add:
     ```python
     def _within(seconds, fn):
         """fn() in a daemon thread, at most `seconds` of wall clock: httpx's timeout is per read, so a
         server trickling one byte at a time never trips it (#147)."""
         out = {}
         def call():
             try:
                 out["v"] = fn()
             except BaseException as e:  # noqa: BLE001 - re-raised in the caller
                 out["e"] = e
         t = threading.Thread(target=call, daemon=True)
         t.start()
         t.join(seconds)
         if t.is_alive():
             raise ResearchError("fetch took too long")
         if "e" in out:
             raise out["e"]
         return out["v"]
     ```
   - In `Fetcher`, add `_hop(self, u, target, limit, opened)`. It holds today's
     `with self.http.stream(...)` block (lines 260–279) unchanged except:
     - right after `as r:`, `opened.append(r)`;
     - the redirect branch returns `(None, None, r.headers["location"])` instead of
       doing the `urljoin`/robots/`continue`;
     - the success path returns `(body, ctype, str(u))`;
     - the `HTTP 4xx`/`5xx` raise, the `TooLarge` limit and the in-loop
       `deadline` check stay as they are (the in-loop check is now redundant
       but harmless; keep it, it costs nothing).
   - `_get` keeps the loop, `vet_url`, `public_ip`, the pacing and `target`. It
     drops the `deadline` line and the `with` block, and instead:
     ```python
     opened = []
     try:
         body, ctype, location = _within(TIMEOUT, lambda: self._hop(u, target, limit, opened))
     except ResearchError:
         for r in opened:
             r.close()  # a timed-out hop stops reading
         raise
     if body is None:
         url = urljoin(str(u), location)
         if check_robots and not self.allowed(url):  # a new host has its own robots.txt
             raise RobotsDisallowed("disallowed by robots.txt")
         continue
     return body, ctype, location
     ```
     (the success tuple's third value is `str(u)`; name it `final` if clearer).
   Traps:
   - The redirect condition stays `status in (301, 302, 303, 307, 308) and a
     location header`. A 3xx without location falls through to the body path as
     today.
   - `body` is `b""` for an empty 200, so test `body is None`, never `not body`.
   - `Blocked`, `RobotsDisallowed` and `TooLarge` are `ResearchError`
     subclasses: they pass through `_within`'s re-raise unchanged, and closing an
     already-closed response is harmless.
   - `self.allowed()` for a redirect runs in the calling thread, outside the
     hop's limit; each of its own fetches has its own limit.

2. `tests/test_research.py`: a blocking page in the `Web` fake. → verify by the
   tests in step 3.
   - `Web.__init__` (line 93): add `self.slow = set()` ((host, path) pairs) and
     `self.gate = threading.Event()`.
   - `Web.__call__` (line 114): after `self.seen.append(...)`, add
     `if (host, request.url.path) in self.slow: self.gate.wait(3)`.
   - The `web` fixture (line 134): `w = Web(monkeypatch); yield w; w.gate.set()`,
     so blocked threads end when the test does.
   - `import threading` at the top.
   Traps:
   - The fixture patches `rs.time.sleep` (it is the global `time.sleep`), so the
     block must be `Event.wait`, never `time.sleep`.

3. `tests/test_research.py`: three tests, after
   `test_redirect_limit_and_normal_redirect` (around line 173) and the run tests.
   → verify by `docker compose build -q app && docker compose run --rm app pytest
   -q -p no:cacheprovider tests/test_research.py`.
   - `test_slow_headers_hit_the_wall_clock_limit(web, monkeypatch)`:
     `monkeypatch.setattr(rs, "TIMEOUT", 0.3)`; `web.html("slow.example",
     "/robots.txt", "")`; `web.html("slow.example", "/p")`;
     `web.slow.add(("slow.example", "/p"))`; time `rs.Fetcher().fetch(
     "https://slow.example/p")` under `pytest.raises(rs.ResearchError,
     match="took too long")`, assert elapsed `< 1.5`.
   - `test_slow_robots_txt_is_disallowed(web, monkeypatch)`: same, but the slow
     path is `/robots.txt`; `assert rs.Fetcher().allowed("https://slow.example/p")
     is False` in under 1.5 s.
   - `test_run_skips_a_slow_page_and_keeps_the_others(web, cleanup, monkeypatch)`:
     `TIMEOUT` 0.3; `web.results = ["https://slow.example/p",
     "https://docs.example/guide"]`; both hosts get an empty robots.txt and a
     page; `slow.example /p` is slow. Model on
     `test_run_stores_pages_skips_bad_ones_and_never_stores_the_key` (line 238,
     `make_row`, `row`). Assert one page from `docs.example` and
     `results["skipped"] == [{"domain": "slow.example", "error": "ResearchError"}]`
     (`run()` records the exception type only).
   Traps:
   - `TIMEOUT` is also the httpx client timeout; the patch must be in place
     before `Fetcher()` is built, which the order above gives.
   - Each new test must fail on main: on main the first two block 3 s and then
     succeed. Check with the scratchpad `check.sh` after committing.

## Tests

- The three new tests fail on main and pass on the branch, each in about a second.
- Full suite: `docker compose build -q app && docker compose run --rm app timeout
  900 pytest -q -p no:cacheprovider`, all green.

## Rollback

Revert the branch's commits. No schema or data change.
