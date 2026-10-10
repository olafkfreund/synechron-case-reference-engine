---
status: approved
issue: 140
intent: intent/2026-10-10-140-merge-prefers-sourced.md
---

# Spec: Merging prefers a member's sourced copy over an unsourced one

## Design

Both changes go in one function, `combine` (`app/review.py`). `do_merge` and
the tests are its only callers. No other file changes.

1. **Scalars and period, with no pick.** Today the default is
   `members[0][0]`. The new default is the first member whose copy of the
   field is not `unsourced`. If every copy is unsourced, it falls back to the
   first member as today, so the field stays unsourced (fail closed).
   - An explicit pick in `pick` still wins unchanged.
   - `stamped` already sets `document_id` from the member whose copy is kept.
2. **Capabilities and tech de-duplication.** The `seen` set becomes a dict from
   the casefolded key to the item's index in `items`. When a duplicate comes
   in sourced and the kept copy is unsourced, the sourced copy (stamped with
   its own `document_id`) replaces the kept one in place. That keeps the
   first-seen order, which the existing test asserts. Otherwise the first
   copy is kept, as today.
3. **Preview.** `merge_preview` is left as it is. It still offers a choice only
   when the values differ. When they are equal, the default from step 1 now
   picks the sourced copy, so no choice is needed.

`do_merge` calls `check()` on the combined case against each member's
document. That re-verifies whichever copy was kept, so a stale `unsourced`
flag can't vouch for a quote.

## Alternatives rejected

- **Offer a choice in the preview whenever any member's copy is unsourced.**
  This makes the reviewer do work the code can do. The intent asks for a
  choice only when the members really differ.
- **Merge the quotes from several members.** A field has one quote from one
  document. Mixing them breaks the `document_id` origin.

## Risks

- **Different default origin.** The merged title (or another field) may now
  come from member B instead of A when A's copy is unsourced. That is the
  point, and it changes nothing when A's copy is sourced.
- **Default when values differ.** When the values differ and the reviewer
  doesn't pick, the default is now the first *sourced* member, not the
  first member. Unsourced values are dropped at approve anyway, so this is
  the safer default.

## Verification

New tests in `tests/test_review.py`. Both must fail on main:

- `tests/test_review.py::test_combine_prefers_sourced_scalar`
  - A pure call to `combine`. Members A and B have the same title value; A's
    title is `unsourced=True` and B's is sourced.
  - With no pick, the result's title is not unsourced and its `document_id`
    is B's.
  - With `pick={"title": A}`, the result is A's unsourced copy.
  - When both copies are unsourced, the result stays unsourced.
- `tests/test_review.py::test_combine_dedupe_keeps_sourced_copy`
  - A has the capability "Payments" unsourced; B has "payments" sourced.
  - The result has one "Payments" item, not unsourced, with B's
    `document_id`, still in A's position.

Then the full suite stays green, including
`test_merge_combines_checked_fields`, whose order and origins are unchanged.
