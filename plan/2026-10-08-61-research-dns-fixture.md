---
status: approved
issue: 61
spec: spec/2026-10-08-61-research-dns-fixture.md
---

# Plan: Research tests run outside compose

## Approved decisions (self-contained)

- The fake `getaddrinfo` in the `Web` fixture passes through the host the app
  connects to, read once from `db.url()` with
  `psycopg.conninfo.conninfo_to_dict`. This replaces the literal `"db"`.
- Every other host keeps the fake answer. No app change.
- `tests/test_research_claims.py` reuses `Web`, so it needs no edit.
- The session model implements this itself: 1 step, 1 file.

## Steps

1. **Pass through the configured DB host.** `tests/test_research.py`:
   - imports: `from psycopg.conninfo import conninfo_to_dict`;
   - `Web.__init__` (line 96): `self.db_host =
     conninfo_to_dict(db.url()).get("host")`;
   - `getaddrinfo` (line 107): `if host == self.db_host:` with the comment
     "psycopg resolves the database through the same function".

   → verify by:
   - on today's code (before the edit): `docker compose build app && docker
     compose run --rm -e
     DATABASE_URL=postgresql://postgres:dev@synechron-case-referances-engine-db-1/refs
     app timeout 120 pytest -q tests/test_research.py
     tests/test_research_claims.py` hangs until `timeout` ends it;
   - the same command after the edit passes;
   - then `docker compose run --rm app pytest`.

   Traps:
   - compose has no bind mount: build before each run;
   - `db.url()` with `DB_SECRET_ARN` would call AWS. The tests set
     `DATABASE_URL`, so this doesn't apply, but don't call `url()` per
     lookup: read it once in `__init__`.

## Tests

- The research and claims tests pass with the database at a host other than
  `db`, and the full suite is green.

## Rollback

- Revert the PR. Tests only.
