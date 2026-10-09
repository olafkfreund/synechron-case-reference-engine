---
status: approved
issue: 100
spec: spec/2026-10-09-100-audit-test-order.md
---

# Plan: Audit page test depends on test order

The test `tests/test_hardening.py::test_audit_view_is_admin_only_paged_and_filtered`
counts every `<tr>` on `/admin/audit`, so rows that other tests leave in
`generations`, `source_class_changes` and `source_acl_changes` change the
count. The approved fix changes only this test. No app, template or fixture
change.

Approved decisions:

- **Count only the generated-outputs section.** The helper is
  `gen(t) = t.split("<h2>Generated outputs</h2>")[1].split("<h2>Model approvals</h2>")[0]`.
  The headings are fixed text in `app/templates/audit.html:14,27`.
- **Use the test's own users.** Set `u = uuid.uuid4().hex[:8]`, then
  `a, b = f"u{u}-0", f"u{u}-1"`. Insert 55 rows for `a` and 3 for `b`, all
  with `case_ids=[cid]`. Check paging through `?user=a`.
- **Delete only the test's own rows.** Remove the blanket
  `delete from generations`. Put everything after the inserts in `try:`.
  The `finally:` runs `delete from generations where user_id = any(%s)`
  with `[a, b]` on `db.connect()`.
- **The assertions:**

| Request | Assertion |
| --- | --- |
| `client(R)` `/admin/audit` | 403 |
| admin `/admin/audit` | 200, and `f'href="/review/{cid}"' in t` |
| admin `?user={a}` | `gen(t).count("<tr>") == 51`, `"older &raquo;" in t`, `"&laquo; newer" not in t`, `f"p=2&amp;user={a}" in t` |
| admin `?p=2&user={a}` | `gen(t).count("<tr>") == 6`, `"&laquo; newer" in t`, `"older &raquo;" not in t` |
| admin `?user={b}` | `gen(t).count("<tr>") == 4`, `b in t`, `a not in t` |

- **What stays out:**
  - no row count or pager check on the unfiltered page;
  - no clean-up of the `source_*_changes` tables.

## Steps

1. `tests/test_hardening.py:117-131`: rewrite the test body as above. It
   still uses the `approved` fixture and `client`, `ADMIN`, `R`; `uuid` is
   already imported (line 6). Verify with
   `docker compose build app && docker compose run --rm app pytest -p no:cacheprovider tests/test_hardening.py -k audit_view`.

   Traps:
   - The worktree has no bind mount, so build before you run anything.
   - Use only the `refs-engine-100` compose project. Never `up` or `down`, and never touch `refsdemo` or `refsdev`.
   - Jinja escapes `&` in the pager href, which is why the test checks `p=2&amp;user=`.

## Tests

Run all of these in the `refs-engine-100` compose project:

1. The full suite in default order: all pass.
2. The full suite in reversed node-id order: all pass.
3. Shuffled with seeds 1–5, seeded as in the intent: all pass.
4. On a dirty DB:
   1. Check that `source_class_changes`, `source_acl_changes` and `generations` each have more than 0 rows.
   2. Run only this test: it passes.
   3. Check that `select count(*) from generations` gives the same number before and after.

## Rollback

Revert the commit. It touches only the test.
