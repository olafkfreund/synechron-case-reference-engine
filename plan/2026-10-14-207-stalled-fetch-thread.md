---
status: draft
issue: 207
spec: spec/2026-10-14-207-stalled-fetch-thread.md
---

# Plan: a stalled research fetch frees its thread at the time limit

## Approved decisions

- **Mechanism.** On give-up, shut down the connection's socket
  (`SHUT_RDWR`). Measured: closing a socket from another thread doesn't wake
  a blocked `recv`, so closing the pool leaves the thread stuck for 9 s,
  while a shutdown frees it at once.
- **Recording sockets.** An `httpcore.SyncBackend` subclass records each
  socket it opens. It is installed on
  `httpx.HTTPTransport()._pool._network_backend`, the one private attribute,
  because httpx 0.28 has no public argument for it. A guard test catches a
  rename.
- **Mocks untouched.** This applies only when `rs.TRANSPORT` is None. Tests
  that inject `MockTransport` don't change.
- **On give-up in `_get`,** after closing opened responses:
  - shut down every recorded socket, ignoring `OSError`
  - clear the list
  - replace `self.http` with a fresh client
- **The shortcut comment goes.**
- **SSRF settings are unchanged:** the pinned IP, Host and SNI,
  `trust_env=False`, manual redirects and per-hop vetting.

## Steps

1. **`app/research.py`, near `Fetcher` (about `:371-400`).**
   - Add a class `_Sockets(httpcore.SyncBackend)`, using
     `import httpcore`, a dependency httpx already pulls in. It holds a
     `socks` list, and its `connect_tcp` calls super and appends
     `stream.get_extra_info("socket")`.
   - Add a method `Fetcher._client()`. It builds `httpx.Client(transport=…,
     timeout=TIMEOUT, follow_redirects=False, trust_env=False)`, where the
     transport is `TRANSPORT` if set. Otherwise it is
     `httpx.HTTPTransport()` with `_pool._network_backend =
     self.backend = _Sockets()`.
   - `__init__` calls it.
   - In `_get`'s `except ResearchError`, after the `r.close()` loop, shut
     down the backend's sockets (only when it exists), clear the list, and
     set `self.http = self._client()`.
   - Delete the shortcut comment.

   Verify with `docker compose build app && docker compose run --rm app
   pytest -q tests/test_research.py`.

   Traps:
   - `TIMEOUT` is read at call time, because tests monkeypatch it. Keep
     using the module global.
   - Keep `trust_env=False` and `follow_redirects=False`.
   - The commit guard blocks a Bash heredoc that contains the word
     "shutdown" (it reads it as a host command). Edit with Edit or Write.

2. **`tests/test_research.py`, after
   `test_slow_headers_hit_the_wall_clock_limit`.** Add two tests.
   - **`test_header_stall_frees_the_thread_at_the_limit(monkeypatch)`:**
     - A loopback server accepts one connection and never answers.
     - Monkeypatch `rs.TIMEOUT=0.3` and `rs.public_ip` to return
       `"127.0.0.1"`.
     - Monkeypatch `rs.vet_url` to `lambda url: (httpx.URL(url), 443)`.
       This is needed because the real one refuses the server's port.
     - Call `rs.Fetcher()._get(f"http://stall.example:{port}/p", 1000)`,
       straight to `_get` so robots.txt isn't fetched, and expect
       `ResearchError` "took too long".
     - Then, with a 1 s socket timeout, the server's accepted socket must
       read to EOF: the request bytes, then `b""`.
     - Don't use the `web` fixture, since its `MockTransport` would bypass
       the real backend.
   - **`test_fetcher_tracks_sockets_on_the_real_transport()`:** `rs.TRANSPORT`
     is None by default, and `isinstance(Fetcher().http._transport._pool._network_backend,
     rs._Sockets)` holds.

   Verify: both pass. **Negative control:** comment out the shutdown loop;
   the first test fails on the 1 s read timeout. Restore it.

## Tests

```sh
docker compose build app && docker compose run --rm app pytest -q tests/test_research.py
docker compose build app && docker compose run --rm app pytest -q   # full suite: all pass
```

## Rollback

Revert the commit. Behaviour returns to the bounded, roughly 10 s
lingering thread.
