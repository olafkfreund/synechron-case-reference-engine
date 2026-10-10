---
status: draft
issue: 124
spec: spec/2026-10-10-124-model-expiry-boundary.md
---

# Plan: Model approval refuses the 12-month expiry date the page and guide allow

These decisions are copied from the spec:

- **A: compare dates in SQL.** The chosen day `d` is accepted when
  `(d + 1)::timestamptz > now()` and `d <= (now() + interval '12 months')::date`.
  The expiry stored is still the end of the day, `(d + 1)::timestamptz`.
- **Yes: `max` on the date field.** `models_page` gets the same limit from
  Postgres and renders `max="{{ max_day }}"` on `#m-exp`, so the page and the
  check can't disagree.
- **No doc changes.** "At most 12 months" is now true.

Size: 3 steps, 3 files (`app/models_admin.py`,
`app/templates/models.html`, `tests/test_models_admin.py`). That meets the
coder handoff threshold.

## Steps

1. `app/models_admin.py`:
   - **`:35-36`.** Replace the select and where with
     ```python
     "select %s,%s,%s,(d + 1)::timestamptz,%s from (select %s::date as d) t "
     "where (d + 1)::timestamptz > now() and d <= (now() + interval '12 months')::date",
     ```
     The parameter tuple keeps the same order and count.
   - **`models_page` (`:14-18`).** In the same `with` block, add
     `max_day = conn.execute("select (now() + interval '12 months')::date").fetchone()[0]`,
     and pass `max_day=max_day.isoformat()` to `page(...)`.

   Verify with `pytest -q tests/test_models_admin.py`: the existing tests pass.

   Traps:
   - Keep the check in SQL. The comment at `:32` says why: "12 months" means
     what Postgres says, including on 29 February.
   - The insert column list is
     `model, data_class, approved_by, expires_at, note`. The fourth select
     item must stay the expiry.

2. `app/templates/models.html:26`: add `max="{{ max_day }}"` to the
   `<input type="date" id="m-exp" ...>`.

   Verify by GETting `/admin/models` in the test from step 3.

   Traps: `max_day` is an ISO date string, and autoescape is on. Nothing else
   renders this template, but `grep -rn "models.html" app/` confirms that.

3. `tests/test_models_admin.py`: add
   `test_expiry_exactly_12_months_ahead_is_accepted(env)`, using `client([ADMIN])`,
   a unique model id and `cleanup(model)` in `finally`, like the existing test.
   1. Read `limit = c.execute("select (now() + interval '12 months')::date").fetchone()[0]`.
   2. A POST with `expires=limit.isoformat()` and `follow_redirects=False`
      returns 303.
   3. A POST with `(limit + timedelta(days=1)).isoformat()` returns 400.
   4. `f'max="{limit.isoformat()}"'` appears in `a.get("/admin/models").text`.

   Verify that steps 2 and 4 of the test fail on main.

   Traps:
   - Take the limit from Postgres, not from `date.today()`. The DB clock and
     time zone decide the limit.
   - Use one model id for both POSTs, since the refused one inserts nothing.
     `cleanup` deletes by model.

## Tests

The full suite must pass. The existing test's 30, 400 and -1 day cases still
hold.

## Rollback

Revert the commit. Approvals already stored are unaffected.
