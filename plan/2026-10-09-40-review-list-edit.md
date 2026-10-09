---
status: draft
issue: 40
spec: spec/2026-10-09-40-review-list-edit.md
---

# Plan: Reviewers add and remove capabilities, tech and outcomes

## Approved decisions (self-contained)

- **One route.** Add and remove both go through `POST /review/{cid}/edit`
  (`app/review.py:130-165`), so they share `load()` (row lock, ACL, `OPEN`,
  version `v`), `check()`, `fix_summary()` and `save()`.
- **Add:** `field=<list>.new`, where the list is `capabilities`,
  `tech_stack` or `outcomes`.
  - It appends `Sourced[str](value=…)`, or `Outcome(metric=…, value=…)` for
    outcomes, at the end of the list.
  - A value is required, and for outcomes a metric too. A quote is
    required. A missing one gives 400 "invalid value".
  - A quote that isn't in the document is kept: `check()` marks the item
    unsourced, and approval drops it (user's choice). The form never sets
    `unsourced`.
- **Remove:** `action=remove` with `field=<list>.<i>` deletes that item.
  `remove` on anything else (a scalar, `period`, `summary`, `.new`) gives
  400, and so does any `action` other than `save` or `remove`. Then
  `check()`, `fix_summary()` and `save()` run as usual.
- **Stale pages:** the existing `v` check gives 409 on a second post from
  the same page. No change there.
- **Page:**
  - `rows()` adds one `new=True` row after each list, labelled "add
    capability", "add tech" and "add outcome";
  - new rows have no badge, `required` inputs and quote, and an **Add**
    button;
  - existing list rows get a **Remove** button (`name="action"
    value="remove"`, `formnovalidate`) on the same per-row form;
  - all buttons show only when the case is `reviewable`;
  - no JavaScript.
- **Unchanged:** `app/schema.py`, SQL, `approve`, extraction. Blanking a
  value still leaves the item in place.
- **Coder handoff:** 3 file-editing steps, 3 files. Steps 1–3 go to the
  `coder` agent. The session model does the manual run and the review.

## Steps

1. **Route.** `app/review.py` `edit` (lines 130-165):
   - add the parameter `action: str = Form("save")`;
   - first thing inside the `try`: `if action not in ("save", "remove"):
     raise HTTPException(400, "unknown action")`;
   - in the list branch (`elif name in LISTS and idx:`, lines 146-151):
     - if `action == "remove"`: `idx == "new"` gives 400 "unknown field";
       otherwise `del getattr(case, name)[int(idx)]` and `obj = None`;
     - elif `idx == "new"`: `if not value or not quote or (name ==
       "outcomes" and not metric): raise ValueError`. Then append
       `Outcome(metric=metric, value=value)` or `Sourced[str](value=value)`
       to the list, and set `obj` to the new item. The quote is set by the
       existing code at line 157;
     - else: today's in-place edit, unchanged;
   - every other branch (scalar, period, summary): if `action ==
     "remove"`, give 400 "unknown field". The simplest way is one check
     before the branches: `if action == "remove" and not (name in LISTS and
     idx and idx != "new")`.

   → verify by `docker compose build app && docker compose run --rm app
   pytest tests/test_review.py`: the existing tests still pass.

   Traps:
   - `HTTPException` raised inside the `try` is not caught by the `except
     (ValueError, IndexError, ValidationError)`; keep it that way;
   - `int("new")` must never run: test `idx == "new"` first;
   - a negative index such as `capabilities.-1` must give 400. Python
     accepts `del l[-1]` and `l[-1]`, so add `if int(idx) < 0: raise
     IndexError` for both edit and remove. Today's edit has the same hole,
     and this closes it;
   - no input is echoed in any error message;
   - there is no bind mount in compose: build before running tests.
2. **Page.** `app/review.py` `rows()` (lines 73-84) and
   `app/templates/review_detail.html`:
   - `rows()`:
     - after the capabilities items, append `dict(path="capabilities.new",
       label="add capability", quote="", unsourced=False, new=True,
       inputs=[("value", "")])`;
     - the same for `tech_stack.new` ("add tech") and, after outcomes, for
       `outcomes.new` ("add outcome", inputs metric and value);
     - existing list rows get `item=True`;
     - this needs the loop at line 78 split, so each list's new row follows
       its own items;
   - template:
     - badge cell (lines 10-13): `{% if r.new %}` shows no badge;
     - inputs and the quote textarea get `required` when `r.new`;
     - button cell (line 16): when reviewable, new rows show `<button
       form="{{ f }}">Add</button>`; other rows keep Save, and `r.item`
       rows also get `<button form="{{ f }}" name="action" value="remove"
       formnovalidate>Remove</button>`.

   → verify by the existing `test_detail_forms_carry_version_and_row_ids`
   and `test_detail_hides_document_text_and_escapes_script` still passing.
   Traps: autoescape stays on; keep the per-row `<form>` elements outside
   the table (the existing test checks `"<tr>\n<form" not in page`).
3. **Tests.** `tests/test_review.py`, using `make`, `ver`, `row`, `client(R)`
   and `DOC`:
   - `test_add_capability_with_document_quote_is_sourced`:
     `field=capabilities.new`, `value="Customer onboarding"`,
     `quote=Q_TITLE` → 303, the last capability has `unsourced` False, and
     `search_text` contains it;
   - `test_add_outcome_with_foreign_quote_is_dropped_on_approval`:
     `field=outcomes.new`, `metric="cost"`, `value="30 percent"`,
     `quote="cost fell by thirty percent overall"` → stored with
     `unsourced` True. Approve, and outcomes holds only the fixture's
     sourced one;
   - `test_add_needs_value_and_quote`: an empty quote, an empty value, and
     outcomes without a metric each give 400, and the data is unchanged;
   - `test_remove_list_item`: a case with `tech_stack=[Sourced[str](value=
     "Acme", source_quote=Q_TITLE)]` (`make(data=case_data(tech_stack=…))`),
     then `action=remove`, `field=tech_stack.0` → 303, `tech_stack` is
     empty and `search_text` has no "Acme" from it. Check `search_text`
     before and after, since the title quote also has Acme: assert the
     tech list is empty, and use a value only the tech item carries if
     needed (e.g. `value="onboarding"`, `source_quote=Q_TITLE`, removed;
     then assert on `data`);
   - `test_remove_only_list_items`: `action=remove` with `field=industry`,
     `capabilities.new` or `capabilities.-1`, and `action=bogus` → each
     400;
   - `test_double_remove_is_409`: two removes of `outcomes.0` with the same
     `v` → 303, then 409;
   - `test_detail_has_add_rows_and_remove_buttons`: the page contains
     `capabilities.new`, `tech_stack.new` and `outcomes.new`, and
     `value="remove"` appears once for the fixture's one outcome.

   → verify by `docker compose build app && docker compose run --rm app
   pytest`: the full suite is green, with 402 + the new tests.
   Traps: fixture data is made up (`DOC`); posts need the Origin header,
   which `client()` already sets; `make()` must not be followed by
   `db.init()` (see the comment at line 36).

## Tests

- `docker compose build app && docker compose run --rm app pytest`: green.
- Manual, in the local portal (#71), on a made-up case: add a sourced
  capability, add an unsourced outcome, remove a tech item, approve. Then
  check the approved case.

## Rollback

- Revert the PR. No schema or data migration; stored cases keep their
  shape.
