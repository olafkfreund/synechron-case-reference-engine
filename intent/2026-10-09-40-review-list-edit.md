---
status: draft
issue: 40
author: olafkfreund
---

# Intent: Reviewers add and remove capabilities, tech and outcomes

## Problem

On the review page (`app/templates/review_detail.html`, `app/review.py`) a
reviewer can only edit list items that extraction found. Each row posts to
`POST /review/{id}/edit` with `field=capabilities.<i>` (or `tech_stack.<i>`,
`outcomes.<i>`), and the route changes that item in place.

- **No add.** If the model missed a capability, a technology or an outcome
  that is in the document, the reviewer can't put it in.
- **No remove.** A wrong or duplicate item can only be blanked. Blanking
  leaves an empty item in the list (`value=None` or `""`) instead of
  removing it.
- Approval then drops every item that `check()` (`app/extract.py:78`) marks
  unsourced (`review.py:177-179`). So a reviewer's work on the lists is
  limited to fixing what the model happened to find.

No test edits a list item or exercises the approval drop.

## Proposed outcome

- On the review page, each of the three lists has an **add** row:
  - for capabilities and tech: a value and a source quote;
  - for outcomes: a metric, a value and a source quote.
- Each existing item has a **remove** button that takes it out of the list.
- A new item goes through the same `check()` against the document text as
  extracted items and edited items. The page shows it as sourced or
  unsourced, and approval drops it if it's unsourced, as it does today.
- The case version (`v`) check still applies, so an add or remove based on
  a stale page gets 409, not the wrong item.
- Tests cover adding (sourced and unsourced), removing, and the approval
  drop of an unsourced list item.

## Affected users and systems

- Reviewers (the reviewer role) on the review page.
- `app/review.py` (routes and `rows()`), `review_detail.html`,
  `tests/test_review.py`.
- Not the data shape (`app/schema.py`), the database, extraction or infra.

## Constraints

- No JavaScript: plain HTML forms with 303 redirects, like the rest of the
  app. The existing `guard` middleware (Origin check) covers the new posts.
- The server decides sourced or unsourced; the form never sets `unsourced`.
- Only open cases (`OPEN`), only for users who may see the case (ACL), the
  same as edit (`load()`).
- No other model call: a new item is checked by text matching only.

## Open questions

1. **An unsourced add:** keep it with the "unsourced" badge, so approval
   drops it (the same as an edit today), or refuse it with 400 so the
   reviewer fixes the quote first? I lean towards keeping it, so it behaves
   like edit and the reviewer sees the badge.
2. **Is the quote required on add?** Without a quote, the item is always
   unsourced. I lean towards a required field.
