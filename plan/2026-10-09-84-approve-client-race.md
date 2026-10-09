---
status: approved
issue: 84
spec: spec/2026-10-09-84-approve-client-race.md
---

# Plan: Approval returns 409, not 500, if its client is deleted mid-approval

## Approved decisions (self-contained)

- `approve` (`app/review.py`) catches `psycopg.errors.ForeignKeyViolation`
  only around the `update cases set status='approved' … client_id`
  statement (lines 234–235). It raises `HTTPException(409, "client changed;
  reload the page") from None`.
- The raise is inside `with db.connect()`, so the transaction rolls back,
  including the earlier `save()`: nothing is written and the case stays
  open.
- `load_clients` and `resolve` are unchanged, with no row lock (rejected:
  admin deletes would wait on reviewers). Approving with no client
  linked is rejected (it drops a link silently), and so is catching around
  the whole block (it hides unrelated errors).
- The test is deterministic: it monkeypatches `anonymise.load_clients` to
  read the registry, then delete that client on a second connection.
- **Coder handoff:** 2 steps, 2 files, so the session model implements
  it.

## Steps

1. **`app/review.py`:**
   - line 8 area: `from psycopg.errors import ForeignKeyViolation`;
   - lines 234–235: wrap the `conn.execute(...)` in `try:` / `except
     ForeignKeyViolation: raise HTTPException(409, "client changed; reload
     the page") from None`.

   → verify by `docker compose build app && docker compose run --rm app
   pytest tests/test_review.py -q`.
   Traps: no bind mount, so build first; don't catch around `save()`.
2. **`tests/test_review.py`:**
   `test_approve_409_if_client_deleted_mid_approval`:
   - create a registry client "Acme <tag>" (referenceable, alias
     `Acme`), and a case whose `client_mention` is Acme with the quote
     `Q_TITLE`;
   - monkeypatch `app.review.anonymise.load_clients` with a wrapper that
     calls the real one, then deletes the client on its own
     `db.connect()`, then returns the list;
   - POST approve gives 409 with "client changed";
   - the case is still `extracted`, `client_id` is null, and `md5(data)`
     is unchanged.

   → verify by the full suite, `docker compose run --rm app timeout 900
   pytest`.
   Traps: made-up names; clean up the client in `finally` if it still
   exists.

## Tests

- The full suite is green, including the new test.

## Rollback

- Revert the PR: one `try`/`except` and one test.
