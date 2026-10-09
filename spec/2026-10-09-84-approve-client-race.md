---
status: draft
issue: 84
intent: intent/2026-10-09-84-approve-client-race.md
---

# Spec: Approval returns 409, not 500, if a client is deleted mid-approval

## Design

Option A from the intent: catch the foreign-key error on the link update and
answer 409, the same way `delete` already handles the opposite ordering.

`app/review.py`, `approve` (lines 209-236):

- Wrap only the `update cases set status='approved', ... client_id=%s ...`
  statement (lines 234-235) in `try` / `except ForeignKeyViolation`, and raise
  `HTTPException(409, "client changed; reload the page") from None`. The
  wording is the one `app/clients.py` uses at lines 76 and 98. `from None`
  keeps the database error out of the response, as `delete` does (line 100).
- The `raise` happens inside `with db.connect() as conn:`, so the connection
  context exits on an exception and rolls the whole transaction back
  (`db.connect()` is not autocommit, `app/db.py` line 38). That undoes the
  earlier `save(conn, cid, case)` (line 230) too: nothing is written, the case
  stays `extracted` at its old version, and a reload shows it unchanged.
- Import: add `from psycopg.errors import ForeignKeyViolation` next to the
  existing `from psycopg.types.json import Jsonb` (line 8), as in
  `app/clients.py` line 5.

Unchanged: `anonymise.load_clients` and `anonymise.resolve`
(`app/anonymise.py` lines 15-20, 83-86) and their other callers
(`app/review.py` lines 135, 289, 322). No schema change, no new dependency.

Why: the race is rare, the rollback is already safe, and the only thing wrong
today is the 500 with no explanation. A reload then re-resolves against the
current registry, so a retry approves with `client_id` null, as it does today.

## Alternatives rejected

- B. Lock the matched client row `for share` before the update. Needs a
  separate locked read just for `approve` (the shared `load_clients` must not
  change), and makes an admin delete wait on a reviewer's transaction. More
  code for a rare race whose failure is already harmless.
- C. On the error, re-resolve and approve with the client unlinked. Silently
  drops a link the reviewer expected to see; the reviewer should decide.
- Catching `ForeignKeyViolation` around the whole `with` block. Wider than
  needed: it could mask an unrelated foreign-key failure from `save` as
  "client changed".

## Risks

- Another foreign key on `cases` written by that same `update` would also
  become a 409. The statement sets only `status`, `approved_by`,
  `approved_at`, `client_id` and `review_due`; only `client_id` is a foreign
  key (`sql/schema.sql` line 51), so the message is accurate.
- Postgres aborts the transaction on the error; nothing may run on `conn`
  after it. The handler only raises, so the `with` exit rolls back cleanly.
- No host-specific risk: app code only, no migration.

## Verification

New test in `tests/test_review.py`, after
`test_approve_links_case_to_registry_client` (line 182), built like it
(invented client name `Acme <random tag>`, mention `Acme` sourced from
`Q_TITLE`, cleanup in `finally`):

- `monkeypatch.setattr(anonymise, "load_clients", ...)` with a wrapper that
  calls the real `load_clients(conn)`, then deletes the new client on a second
  connection (`with db.connect() as c: c.execute("delete from clients where
  id=%s", ...)`, committed on exit), then returns the rows it read. The
  approval resolves to the deleted id; no threads, deterministic. The delete
  does not block: no case links to the client yet.
- `client(R).post(f"/review/{cid}/approve", data={"v": ver(cid)},
  follow_redirects=False)` returns 409 with "client changed; reload the
  page".
- The case row still has `status = 'extracted'`, `client_id` null and the
  same `ver(cid)` as before the post (the `save` was rolled back).
- Without the fix the same test fails with a 500 / raised
  `ForeignKeyViolation`, which shows the test exercises the race.

Run: `docker compose build app && docker compose run --rm app pytest`; the
whole suite stays green, including
`test_approve_links_case_to_registry_client`.
