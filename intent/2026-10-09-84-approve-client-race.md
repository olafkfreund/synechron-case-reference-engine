---
status: approved
issue: 84
author: olafkfreund
---

# Intent: Approval returns 500 if a client is deleted mid-approval

## Problem

`approve` (`app/review.py` lines 209-236) links the case to its registry
client in two statements on one connection: it reads the registry with
`anonymise.load_clients(conn)` (`app/anonymise.py` lines 15-20, a plain
`select` with no lock) and picks an id with `anonymise.resolve()` (lines
83-86); then it runs `update cases set ... client_id=%s` (`app/review.py`
lines 233-235). `cases.client_id` references `clients(id)`
(`sql/schema.sql` line 51).

An admin can delete a client in between. `delete` (`app/clients.py` lines
82-101) removes a referenceable client that no case links to yet, which is
exactly the state of the client this approval is about to link. If that
delete commits after the registry read, the update raises psycopg
`ForeignKeyViolation`. `approve` does not catch it, so the reviewer gets a
500. The transaction rolls back (`db.connect()` is not autocommit,
`app/db.py` line 38), so nothing is lost and a retry approves with
`client_id` null. The reviewer just sees a server error with no hint why.

The opposite ordering is already handled: if the approval's update wins,
`delete` catches `ForeignKeyViolation` and returns 400 (`app/clients.py`
lines 99-100).

`approve` is the only place that writes `cases.client_id`.

## Proposed outcome

- A client deleted during an approval never produces a 500. The reviewer
  gets a 409 "client changed; reload the page" (the wording already used in
  `app/clients.py` line 76 and 98), or the race cannot happen at all.
- Nothing is written in that case: the case stays open, as today.
- A test in `tests/test_review.py`, next to
  `test_approve_links_case_to_registry_client` (line 182), covers the race.

## Affected users and systems

- Reviewers approving cases (`POST /review/{cid}/approve`).
- Admins deleting clients (`POST /admin/clients/{cid}/delete`) only if the
  lock option is chosen (their delete may wait for an approval to commit).
- Code: `app/review.py` `approve`; possibly `app/anonymise.py`
  `load_clients`; `tests/test_review.py`.

## Constraints

- No new dependencies; no schema change.
- `load_clients` is shared by other callers (e.g. `candidates`,
  `app/review.py` line 289), so any change there must not alter their
  behaviour.
- Test data must be made up (invented client names, no real ones).
- Tests run with `docker compose build app && docker compose run --rm app pytest`.

## Open questions

1. How to close the race?
   - **A. Catch `ForeignKeyViolation` around the update, return 409**
     "client changed; reload the page". Few lines, same pattern as
     `delete`, no locking, no effect on other `load_clients` callers.
   - B. Lock the matched registry row `for share` before the update, so a
     concurrent delete waits. Needs a locked read just for `approve` (not
     the shared `load_clients`), and makes admin deletes block on reviews.
   - C. Re-resolve and approve with the client unlinked. Silently drops a
     link the reviewer expected; rejected unless you want it.
   Lean: **A**. The race is rare, the rollback is already safe, and 409
   matches how the rest of the app reports concurrent edits.
2. How to test it? Lean: monkeypatch `anonymise.load_clients` in the test so
   it returns the registry and then deletes that client on a second
   connection, then assert 409 and the case still open. Deterministic, no
   threads.
