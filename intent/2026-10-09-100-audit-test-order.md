---
status: approved
issue: 100
author: olafkfreund
---

# Intent: Audit page test depends on test order

## Problem

`tests/test_hardening.py::test_audit_view_is_admin_only_paged_and_filtered`
counts every `<tr>` on `/admin/audit` (51 on page 1, 6 on page 2, 28 for
`?user=u1`). That page renders four tables: generations, model approvals,
source class changes and source ACL changes (`app/audit.py`,
`app/templates/audit.html`). The test clears only `generations`. If another
test has left a row in any of the other three tables, the count is wrong and
the test fails.

Evidence. Each run used a 527-test suite on `origin/main` (abc072a), in the
`refs-engine-100` compose project, with `-p no:cacheprovider` and explicit
node-id orders:

| Order | Result |
| --- | --- |
| default (file order), fresh DB | 527 passed |
| fully reversed | audit test fails, 526 passed |
| shuffled, seeds 1–4 | audit test fails each time |
| shuffled, seed 5 | 527 passed |
| audit test paired after `test_models_admin.py`, `test_sources.py`, `test_review.py`, `test_ui.py` or `test_seed_demo.py` | fails, e.g. `assert (56 == 51)` |
| audit test alone, after the runs above | fails, `56 == 51` |

Why:

- `app/sources.py:39,44` writes a `source_class_changes` or
  `source_acl_changes` row each time a source is created or edited. The tests
  in the five files above create sources. They clean up `sources` but leave
  the change rows. `test_models_admin.py:81,100` deletes the change rows only
  for its own source, so that is not enough either.
- Those rows add the extra `<tr>`s: the two change-table header rows plus
  one row per change. After the runs above there were 1 class change and
  2 ACL changes: 2 + 3 = 5 extra, so 56.
- The DB container lasts across `docker compose run`. Once one run has left
  change rows behind, the audit test fails even when it runs alone. This is
  also why the default order passes only on a fresh DB: `test_hardening.py`
  sorts before the files that write change rows.
- Two smaller faults in the same test: it runs `delete from generations` for
  every row, not only its own, and its final cleanup is not in a `finally`.
  After a failure it leaves its 55 `generations` rows behind.

In the reversed run and in all five shuffled runs, no other test failed.
So this is the only order-dependent test found (done-when item 2).

## Proposed outcome

- The audit test passes in any order, with any data that other tests left
  in the shared DB.
- It checks the generations table only, paging (50 per page, older/newer
  links) and the `?user=` filter, as it does today.
- The reversed run and the shuffled runs above pass in full.

## Affected users and systems

- `tests/test_hardening.py` (the audit test). Possibly the change-row
  cleanup in `tests/test_models_admin.py`, depending on the option chosen.
- No app code and no runtime behaviour. The `refsdemo` compose project is
  not touched.

## Constraints

- No new dependencies. pytest-randomly and pytest-xdist are not installed,
  and we will not add them. Order checks use explicit node-id lists.
- All tests share one Postgres DB (`postgresql://postgres:dev@db/refs`).
  Nothing truncates it between runs, so any fix must also work on a DB that
  already has data in it.
- `source_*_changes` are append-only for the app role, and
  `test_audit_log_tables_are_append_only_for_the_app` checks this. Tests
  that clean up must keep using the owner connection (`db.connect()`), and
  must not weaken that grant.
- Run tests with `docker compose run --rm app` in the task worktree. Never
  run `up` or `down`.

## Open questions

Which fix:

1. **Make the test count only its own data (lean).** Count rows in the
   generations table only. For example, slice the HTML up to the first
   `</table>`, or count rows that link to `/review/{cid}`. Use unique user
   ids (`u{uuid}-0/1`) and filter `?user=`. The pagination checks also need
   a fixed set of generations. Two ways to get that: delete only its own
   rows in a `finally`, or keep today's `delete from generations` but move
   it into a `finally`. This is the smallest change and puts the fix in the
   one test that has the bug.
2. **Per-test transaction rollback.** Give each test one connection with
   rollback at the end. The app opens its own connections through
   `db.connect()` in TestClient requests, and some tests check the
   app-role grants on separate connections. So every caller would need the
   shared connection, which means a large change to the fixtures and to the
   app's DB entry point. Too much for one flaky test.
3. **Truncate the audit tables in a fixture.** Simple, but it hides the
   rows that other tests leave, and it deletes data that other tests might
   rely on later in the same run. It also needs owner rights on
   append-only tables. This keeps the shared-state coupling.

Also to decide: should the test files that leave `source_*_changes` rows
clean them up as well? My lean is no. With option 1 those rows do no harm,
and the audit table is meant to keep history.
