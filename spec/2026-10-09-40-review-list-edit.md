---
status: approved
issue: 40
intent: intent/2026-10-09-40-review-list-edit.md
---

# Spec: Reviewers add and remove capabilities, tech and outcomes

## Design

Both actions reuse the existing `POST /review/{cid}/edit` route
(`app/review.py:130-165`). That way they share its `load()` (lock, ACL, open
status, version), `check()`, `fix_summary()` and `save()`, and nothing
about access or grounding is written twice.

**Add: `field=<list>.new`.**
- In `edit`, the list branch (`:146-151`) gets a case for `idx == "new"`:
  - it appends `Outcome(metric=…, value=…)` for `outcomes`, or
    `Sourced[str](value=…)` for `capabilities` and `tech_stack`;
  - then the shared code sets `source_quote` and re-validates.
- An add needs a non-empty value (plus a metric for outcomes) and a
  non-empty quote. Otherwise it returns 400 "invalid value", the same
  message as today, with no input echoed back. The form also marks these
  fields `required`, but the server is the check.
- `check()` then marks the new item sourced or unsourced against the
  document text, as for any other item (intent question 1: kept with the
  badge, and dropped on approval).

**Remove: `action=remove` on an existing list item's form.**
- `edit` gets `action: str = Form("save")`. With `remove` and `field` set
  to `<list>.<i>`, it deletes that item. Anything else with `remove`
  (scalars, `period`, `summary`, `.new`) returns 400.
- Then the same `check()`, `fix_summary()` and `save()` run. Removing an
  item can take away the only quote that backs a number in the summary,
  and `fix_summary()` already blanks the summary with a note in that case.
- Any other `action` value returns 400.

**Stale pages.** Every form carries `v` (`md5(c.data)`). After an add or a
remove the version changes, so a second click from the same page (a double
submit, or an index that has shifted) gets 409 "case changed; reload the
page". Nothing in the version check changes.

**Page (`rows()` `:73-84` and `review_detail.html`).**
- After each list's items, `rows()` adds one row: path `<list>.new`,
  label "add capability" / "add tech" / "add outcome", empty inputs, and
  `new=True`.
- In the template:
  - new rows show no badge and an **Add** button, and their inputs and quote
    are `required`;
  - existing list rows get a **Remove** button next to Save (`name="action"
    value="remove"`, `formnovalidate`), on the same per-row form;
  - the buttons only show when the case is `reviewable`, as Save does
    today.
- No JavaScript.

**Unchanged:** `app/schema.py`, the database, `approve` (it already drops
unsourced list items, `:177-179`) and extraction. Blanking an item's value
still leaves it in the list; Remove is the way to take it out.

## Alternatives rejected

- **Separate `/add` and `/remove` routes.** Each would repeat `load`,
  `check`, `fix_summary`, `save` and the error handling.
- **Refusing an unsourced add with 400** (intent question 1). The user
  chose to keep it with the badge, as edits work today.
- **An optional quote** (intent question 2). Without a quote the item can
  never be sourced, so the add would be wasted.
- **JavaScript rows that add many items in one post.** It goes against the
  no-JS constraint, and one item per post is enough.
- **Treating a blanked value as a remove.** It's implicit and would change
  today's edit behaviour; an explicit button is clearer.

## Risks

- **A remove posts the row's other inputs too** (same form). The remove
  branch ignores them.
- **A reviewer pastes a quote from memory, not the document:** it shows as
  unsourced and approval drops it. The Approve button already says so.
- **Order:** new items go to the end of the list. Lists carry no order
  meaning in the schema, search or rendering.

## Verification

Tests in `tests/test_review.py`, using its `make`/`ver`/`row` helpers and
the `DOC` fixture:
- adding a capability whose quote is in `DOC` → stored and sourced;
- adding an outcome whose quote isn't in `DOC` → stored as unsourced, and
  approval drops it (this also covers the approval drop of a list item,
  which no test covers today);
- adding with an empty quote or an empty value → 400, and the case is
  unchanged;
- removing a tech item → gone from `data`, and `search_text` no longer has
  it;
- a remove on a scalar field → 400; an unknown `action` → 400;
- two removes posted with the same `v` → the second gets 409;
- the detail page shows the three add rows and a Remove button per list
  item.

Then the full suite (`docker compose build app && docker compose run --rm
app pytest`), and a manual run in the local portal (#71) on a made-up
case: add, remove, approve.
