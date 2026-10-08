---
status: draft
issue: 61
intent: intent/2026-10-08-61-research-dns-fixture.md
---

# Spec: Research tests run outside compose

## Design

In the `Web` fixture (`tests/test_research.py` lines 95–112), the fake
`getaddrinfo` passes through the host the app actually connects to, instead
of the literal `"db"`:

- `__init__` reads it once:
  `self.db_host = conninfo_to_dict(db.url()).get("host")`, using
  `psycopg.conninfo.conninfo_to_dict`. This handles both a `postgresql://`
  URL and the key/value string that `db.url()` builds from `DB_HOST`.
- `getaddrinfo` passes the call to the real resolver when
  `host == self.db_host`. Every other host still gets the fake answer.

Cases:

| DB host | Today | After |
| --- | --- | --- |
| `db` (compose, CI) | real | real |
| `localhost` or any name | fake `93.184.216.34`, hang | real |
| `127.0.0.1` | fake returns the same IP: works | real |
| none (Unix socket) | psycopg doesn't resolve | the same |

`tests/test_research_claims.py` imports `Web` from `test_research.py`, so it
is fixed with no edit.

## Alternatives rejected

- **Patch a resolver seam in `app/research.py`** instead of the global
  `socket`. It's the cleaner isolation, but it changes app code to suit a
  test, which the intent rules out.
- **Pass through every name that isn't a test page host.** The tests use
  made-up hosts on purpose to check SSRF handling. Real DNS on those would
  leak lookups and make the results depend on the network.
- **Read `DATABASE_URL` directly.** That misses the `DB_HOST` form; `db.url()`
  covers both.

## Risks

- The database host is now resolved for real, as it must be. If a test page
  host ever equals the database host, that test resolves for real too. No
  test does that today (they use `example.com`-style hosts and IPs).
- Tests only. Rollback is a revert.

## Verification

- **Reproduce outside the `db` name**, using the db container's own name on
  the compose network (no host networking needed):
  `docker compose run --rm -e
  DATABASE_URL=postgresql://postgres:dev@synechron-case-referances-engine-db-1/refs
  app timeout 120 pytest -q tests/test_research.py
  tests/test_research_claims.py`.
  - Today it hangs, and `timeout` ends it.
  - After the fix, it passes.
- `docker compose build app && docker compose run --rm app pytest` is green.
