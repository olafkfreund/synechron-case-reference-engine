---
status: draft
issue: 128
spec: spec/2026-10-10-128-merged-edit-source.md
---

# Plan: Merged case: an edited quote can only be sourced from the member it was first taken from

On a merged case, an edited field keeps the `document_id` of the member it
was first picked from. `check()` then looks for the new quote in that member
only.

These decisions are copied from the approved spec:

- **Clear the origin on a new quote.** In `edit`, when a quote is posted and
  it differs from the stored `source_quote`, set the field's `document_id` to
  `None`. `check()` then resolves it to the first member whose text sources
  the value and quote, the same way it does for reviewer-added items (#40). A
  quote that no member sources stays unsourced.
- **An unchanged quote keeps its origin.** The form always posts the quote
  textarea, so an origin cleared on every save could move to another member.
- **Single-document cases.** Nothing changes for them.
- **Scope.** Only `app/review.py` changes.

**Size:** 2 file-editing steps in 2 files. That is below the coder threshold.

## Steps

1. `app/review.py:206-207`. Replace the two lines with:
   ```python
   if obj is not None and quote is not None:
       if quote != obj.source_quote:
           obj.document_id = None  # a new quote has no origin yet: check() finds the member that sources it (#128)
       obj.source_quote = quote
   ```
   Verify: `pytest -q tests/test_review.py` passes, all existing tests
   included.

   Traps:
   - The field objects (`Sourced`, `Outcome`, the `period`) all carry
     `document_id`. Confirm this with `grep -n "document_id" app/schema.py`
     before relying on it. If one type lacks it, guard with
     `hasattr(obj, "document_id")` and record that as a deviation.
   - `ReferenceCase.model_validate(case.model_dump())` on the next line
     re-creates the objects. `document_id=None` survives, because it is a
     normal field.

2. `tests/test_review.py`: after the merge tests, using `acme`, `eng`,
   `merge`, `ver`, `TEXT_A` and `TEXT_B` (`:336-375`), add three tests.
   - `test_merged_edit_can_quote_another_member`:
     1. Merge `a=eng(make, TEXT_A, ["x"], [])` and `b=eng(make, TEXT_B, ["y"], [])`.
        Take the new id from the redirect.
     2. POST `/review/{new}/edit` with `field=industry`, `value=Payments`,
        `quote="Acme moved payments to the cloud"` and `v=ver(new)`.
     3. Assert that the stored `data["industry"]` has `unsourced` false and
        `document_id` equal to b's `document_id`.
   - `test_merged_edit_quote_in_no_member_is_unsourced`: the same steps,
     with a quote in neither text. Assert `unsourced` true and
     `document_id` null.
   - `test_merged_edit_same_quote_keeps_origin`:
     1. Read `data["title"]`'s `document_id` and `source_quote`.
     2. POST an edit of `title` with a new value that keeps the same quote.
     3. `document_id` is unchanged.

   Verify:
   - Each test uses a reviewer client, `client(R)` as in the other merge
     tests.
   - The first test fails on main, with `unsourced` true. Stash
     `app/review.py`, rebuild and run it, then pop and rebuild.

   Traps:
   - The industry quote must appear verbatim in TEXT_B, and "Payments" must
     be sourced by it. Check against `sourced()` in `app/schema.py`. If the
     value is not in the quote, pick a value that is, such as "payments", and
     note that in the test.
   - For the third test, the title's value must still be sourced by the
     unchanged quote. `eng()` uses `q[:12]` in the value, so reuse a
     substring of q.

## Tests

The full suite passes, and the first new test fails on main.

## Rollback

Revert the commit. No stored data changes shape.
