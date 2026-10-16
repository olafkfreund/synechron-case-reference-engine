---
status: approved
issue: 124
author: olafkfreund
---

# Intent: Model approval refuses the 12-month expiry date the page and guide allow

## Problem

An admin approves a model for a data class until an expiry date. The model
page help, `docs/user-guide/admins.md` and the README all say that date can
be at most 12 months away.

`approve()` (`app/models_admin.py:35-36`) keeps the approval until the end
of the chosen day, as `(day + 1)::timestamptz`. It then requires that
moment to be at or before `now() + interval '12 months'`, which falls partway
through the day 12 months away. So that date, the one the docs call the
maximum, always returns a 400, and the latest date that works is one day
earlier. The date field has no `max`, so the admin gets no hint.

## Proposed outcome

- The date exactly 12 months from today is accepted.
- The day after it is still refused.
- The page, the guide and the code agree.

## Affected users and systems

- Admins approving models: `app/models_admin.py` and
  `app/templates/models.html`.
- Tests: `tests/test_models_admin.py`.
- Not affected: how approvals are enforced (`llm.allowed`) and expiry at the
  end of the day.

## Constraints

- The bounds are still checked in SQL. The code comment says so, so that
  "12 months" means what Postgres says.
- Never accept more than 12 months by calendar date.

## Open questions

1. **Fix the code or the text?**
   - **A. Fix the code:** compare dates, so
     `day <= (now() + interval '12 months')::date`.
   - **B. Fix the text:** change the three places to "less than 12 months".

   **Recommendation: A.** It is a one-line change, and it is what admins read
   and expect.
2. **Add `max` to the date field as well?** **Recommendation: yes.** It is
   one attribute, and the browser then stops the admin before the request.

## Approved answers

1. A: fix the code, comparing dates.
2. Yes: add max to the date field.
