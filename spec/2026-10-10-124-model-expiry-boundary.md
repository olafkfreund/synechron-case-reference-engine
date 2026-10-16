---
status: approved
issue: 124
intent: intent/2026-10-10-124-model-expiry-boundary.md
---

# Spec: Model approval refuses the 12-month expiry date the page and guide allow

## Design

The approved answers are:

- **A:** fix the code so that it compares dates;
- **yes:** add `max` to the date field.

1. **The bound, in `approve()` (`app/models_admin.py:32-37`).** The check
   stays in SQL, as the comment there says, but it compares the chosen day,
   not end-of-day times. The end-of-day expiry is still what gets stored. One
   parameter is bound, as today:
   ```python
   "select %s,%s,%s,(d + 1)::timestamptz,%s from (select %s::date as d) t "
   "where (d + 1)::timestamptz > now() and d <= (now() + interval '12 months')::date",
   ```
   - **Today:** still accepted, because it lasts until midnight.
   - **Yesterday:** still refused.
   - **The date exactly 12 months away:** now accepted.
   - **The day after that:** refused.
2. **The date field's `max`.** `models_page` (`:14-18`) asks Postgres for the
   same limit in the query it already runs, so the page and the check can't
   disagree:
   ```python
   max_day = conn.execute("select (now() + interval '12 months')::date").fetchone()[0]
   ```
   It passes `max_day=max_day.isoformat()`. `app/templates/models.html:26`
   gets `max="{{ max_day }}"` on `#m-exp`.
3. **The docs.** The guide, the README and the field help already say "at most
   12 months", which is now true, so they don't change.

## Alternatives rejected

- **B: change the three texts to "less than 12 months".** Answer 1 chose to
  fix the code.
- **Compute the bound in Python (`date.today()` plus 12 months).** Python has
  no month arithmetic, and it could disagree with Postgres on 29 February or
  on timezone. The code comment asks for Postgres.
- **Allow end-of-day up to 12 months and a day.** That stores a later expiry
  than the date the admin chose.

## Risks

- **Timezone.** `::date` uses the session timezone, the same one that `(d +
  1)::timestamptz` already uses, so the two stay consistent. The test uses
  Postgres `current_date` so that it does not depend on the container's clock.
- **The `max` attribute is a convenience only.** The browser enforces it, and
  the server check is the real one.
- **No schema change, no host impact.**

## Verification

`tests/test_models_admin.py` gets
`test_expiry_exactly_12_months_ahead_is_accepted`, which takes the limit
from Postgres with `select (now() + interval '12 months')::date`:

- A POST with that date returns 303. It returns 400 on main.
- The same date plus one day returns 400.
- `GET /admin/models` contains `max="<that date>"`. It doesn't on main.

The existing test's 30, 400 and -1 day cases still hold, and the full suite
passes.
