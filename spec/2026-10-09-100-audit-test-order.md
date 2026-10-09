---
status: approved
issue: 100
intent: intent/2026-10-09-100-audit-test-order.md
---

# Spec: Audit page test depends on test order

## Design

Option 1 from the intent: the test counts only its own data. One test
changes: `tests/test_hardening.py::test_audit_view_is_admin_only_paged_and_filtered`
(lines 117–131). No app, template or fixture change.

Three fixes:

1. **Count only the generated-outputs section.** `app/templates/audit.html`
   puts each table in its own `section-card`, with the headings
   `<h2>Generated outputs</h2>` and then `<h2>Model approvals</h2>`. These
   headings come from the template, not from data, so they are stable. The
   test slices the page between the two headings and counts `<tr>` only in
   that slice. The slice holds one header row plus one row per generation.
   Rows in the approvals, class-change and ACL-change tables are no longer
   counted.
2. **Use its own users, and delete only its own rows.** Two user ids, unique
   per run: `a = f"u{uuid.uuid4().hex[:8]}-0"` and `b = ...-1` (one uuid,
   two suffixes). Paging is checked through `?user=a`. With the filter, the
   test sees only its own rows, even when other tests have left
   `generations` rows (`test_render.py` and `test_industry.py` do). The
   `audit_page` query applies the same `limit`/`offset` with or without a
   filter (`app/audit.py:19-21`). The link builder keeps `user` on the older
   and newer links (`app/audit.py:29`), so checking that is a gain. The
   blanket `delete from generations` at the start goes.
3. **Cleanup in `finally`.** Everything after the inserts runs in
   `try:`. The `finally:` runs `delete from generations where user_id =
   any(%s)` with `[a, b]` on the owner connection (`db.connect()`). It
   deletes nothing else.

Data: 55 rows for `a` and 3 for `b`, all with `case_ids=[cid]`.

Assertions, with `gen(t) = t.split("<h2>Generated outputs</h2>")[1].split("<h2>Model approvals</h2>")[0]`:

| Request | Assertion |
| --- | --- |
| `client(R)` `/admin/audit` | `status_code == 403` (unchanged) |
| admin `/admin/audit` | `status_code == 200` and `f'href="/review/{cid}"' in t` (our newest rows are on page 1, since ids are `desc`) |
| admin `/admin/audit?user={a}` | `gen(t).count("<tr>") == 51`, `"older &raquo;" in t`, `"&laquo; newer" not in t`, `f"p=2&amp;user={a}" in t` (the filter stays on the older link) |
| admin `/admin/audit?p=2&user={a}` | `gen(t).count("<tr>") == 6`, `"&laquo; newer" in t`, `"older &raquo;" not in t` |
| admin `/admin/audit?user={b}` | `gen(t).count("<tr>") == 4`, `b in t`, `a not in t` |

The last row replaces the old `u0 not in ...` check. It has no
`replace("user (exact", "")` workaround now, because `a` and `b` are unique
strings that the label text cannot contain.

The unfiltered page gets no `<tr>` count and no pager check. Its contents
depend on rows that other tests leave behind, which is the bug. The
filtered requests run the same paging code.

The intent's side question: the test files that leave `source_*_changes`
rows do not change. Once the count stays inside the section, those rows do
no harm, and the change tables are meant to keep history.

## Alternatives rejected

- **Per-test transaction rollback** (intent option 2). Every app connection
  from `db.connect()` inside TestClient requests, and the grant checks on
  their own connections, would need one shared connection. That is a large
  fixture and DB-entry change for one flaky test.
- **Truncate the audit tables in a fixture** (intent option 3). It hides the
  leftover rows rather than ignoring them, it needs owner rights on
  append-only tables, and it deletes data that later tests in the run might
  read.
- **Count `href="/review/{cid}"` links instead of slicing.** Every row has
  the same `cid`, so this works today. But it counts links, not rows, and it
  breaks if a row ever links a case twice or the template adds a second
  link. The section slice counts what the old test meant to count.
- **Slice up to the first `</table>`.** Simpler, but when there are no rows
  the generations section has no table, and the first `</table>` is then the
  approvals table. The heading pair always marks the right section.
- **Keep `delete from generations` and move it into `finally`.** It deletes
  rows that other tests wrote, and the unfiltered paging counts would still
  depend on their rows.
- **Clean up `source_*_changes` in the five test files.** Not needed with
  this design, and they are append-only history.

## Risks

- **Heading text changes.** If `<h2>Generated outputs</h2>` or
  `<h2>Model approvals</h2>` is renamed or reordered, `split(...)[1]` raises
  `IndexError` or the slice is wrong. The test fails loudly, not silently,
  and the fix is to update the test with the template.
- **Leftover rows from an interrupted run.** If pytest is killed in the
  middle, the `finally` does not run. The rows use unique user ids, so no
  later run counts them.
- **Weaker unfiltered check.** Unfiltered paging is no longer counted. The
  same query and link code runs with the filter, with `who` set rather than
  empty. The `who = ''` branch is still covered by the unfiltered 200 and
  link check.
- No host or runtime impact. Tests only, in the `refs-engine-100` compose
  project. `refsdemo` and `refsdev` are not touched.

## Verification

Use the intent's method: the full 527-test suite in the `refs-engine-100`
compose project, `docker compose run --rm app pytest -p no:cacheprovider
<node ids>`, with the order set by an explicit list of node ids. Never `up`
or `down`.

1. Default (file) order: all pass.
2. Fully reversed order: all pass (failed before).
3. Shuffled, seeds 1–5 (same seeding as the intent): all pass (seeds 1–4
   failed before).
4. Alone on a dirty DB: after the runs above, check that the DB has rows
   from other tests: `select count(*) from source_class_changes`,
   `source_acl_changes` and `generations` are each > 0. Then run only
   `tests/test_hardening.py::test_audit_view_is_admin_only_paged_and_filtered`.
   It passes (it failed before with `56 == 51`).
5. Around step 4, `select count(*) from generations` gives the same number
   before and after the run. The test's 58 rows are gone, and no row from
   another test was deleted.
