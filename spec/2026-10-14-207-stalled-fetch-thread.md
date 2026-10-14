---
status: approved
issue: 207
intent: intent/2026-10-14-207-stalled-fetch-thread.md
---

# Spec: a stalled research fetch frees its thread at the time limit

## Design

**Measured first,** in the app image (httpx 0.28.1, httpcore 1.0.9), with a
local server that accepts a connection and never answers. These are the
seconds the fetch thread stayed blocked after the caller gave up:

| On give-up | Thread blocked for |
|---|---|
| nothing (today) | 9.0 s |
| `client.close()` (closes the pool) | 9.0 s |
| `socket.shutdown(SHUT_RDWR)` on the connection's socket | 0.0 s |

Closing the pool doesn't work: closing a socket from another thread doesn't
wake a blocked `recv` on Linux. Shutting the socket down does.

**Change (`app/research.py`, `Fetcher`):**

1. **A small `httpcore.SyncBackend` subclass** records the socket of every
   TCP connection it opens. `connect_tcp` calls super, then stores
   `stream.get_extra_info("socket")`.
2. **`Fetcher.__init__` installs it** on the real transport:
   `httpx.HTTPTransport()._pool._network_backend`. This is used only when
   `TRANSPORT` is None. Tests keep injecting `MockTransport`.
   - httpx 0.28 has no public `network_backend` argument, so this reaches
     one private attribute.
   - A test asserts the attribute exists, so an httpx upgrade that renames it
     fails CI instead of silently dropping the fix.
3. **On the `ResearchError` in `_get`**, after closing any opened
   responses, the Fetcher:
   - shuts down every recorded socket, ignoring `OSError` for ones already
     closed
   - clears the list
   - replaces `self.http` with a fresh client, so later hops don't reuse a
     dead pool

   The shortcut comment at `:397` goes.

The Fetcher is per job and sequential, so the only connection in use at
give-up is the stalled one. Shutting down idle pooled connections costs at
most one reconnect.

**SSRF settings are unchanged:** the pinned IP, Host and SNI,
`trust_env=False`, manual redirects and per-hop vetting. Only the backend
that opens the TCP socket is wrapped, and it still connects to the same
vetted address.

## Alternatives rejected

- **Close the pool or the client.** Measured above: it doesn't wake the
  thread.
- **Our own `httpx.BaseTransport` around
  `httpcore.ConnectionPool(network_backend=…)`.** It's public API, but it
  means copying about 40 lines of httpx's request/response mapping, which is
  more code to keep in step than one attribute guarded by a test.
- **A lower per-read timeout.** It doesn't bound a trickling host, which
  `_within` already handles, and it makes slow but honest hosts fail.
- **Keep it open as watched.** You approved fixing it.

## Risks

- **An httpx or httpcore upgrade renames `_pool` or `_network_backend`.**
  The guard test fails in CI.
- **A shutdown races with a hop that just finished.** That hop's result has
  already been discarded, because `_within` raised. The next hop uses the
  fresh client.

## Verification

- **New test in `tests/test_research.py`.** It uses a real loopback server
  that accepts and never answers, with `public_ip` and `vet_url`
  monkeypatched to allow 127.0.0.1 and port, and `TIMEOUT=0.3`.
  - `fetch` raises "took too long".
  - The fetch thread has ended within 1 s of the raise. This is checked by
    counting live threads, or by the server's side of the socket seeing EOF.
  - **Negative control:** without the socket close, the thread is still
    alive at that point.
- **Guard test:** a `Fetcher()` built without `TRANSPORT` has the tracking
  backend on its pool.
- **Existing tests:** `test_slow_headers_hit_the_wall_clock_limit` and the
  other research tests pass unchanged.
- **Full suite:** `docker compose build app && docker compose run --rm app
  pytest`.
