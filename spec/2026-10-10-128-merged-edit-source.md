---
status: approved
issue: 128
intent: intent/2026-10-10-128-merged-edit-source.md
---

# Spec: Merged case: an edited quote can only be sourced from the member it was first taken from

## Design

The approved intent has no open questions. The fix is in `edit`
(`app/review.py:206-207`), using the resolution `check()` already does for
items with no origin (`app/extract.py:91-92`, #40):

```python
if obj is not None and quote is not None:
    if quote != obj.source_quote:
        obj.document_id = None  # a new quote has no origin yet: check() finds the member that sources it (#128)
    obj.source_quote = quote
```

- **Only when the quote changes.** The detail page always posts the quote
  textarea (`review_detail.html:11`). So a save that changes only the value
  keeps its origin, and the row keeps showing the same member's title
  (`review.py:94`).
- **A changed quote** gets `document_id = None`. `check(case, text)` (`:211`)
  then sets it to the first member whose text sources the value and the new
  quote. If no member sources it, `document_id` stays `None`, `text.get(None,
  "")` returns `""`, and the field is unsourced, which fails closed.
- **Single-document cases.** `text` is a `str` there, so `text_for` never
  reads `document_id`. No behaviour changes.
- **A field with an empty value.** `mark` doesn't resolve an origin for it
  (`value in (None, "")`), so `document_id` stays `None` until a value is
  saved. Nothing reads it in the meantime.

Only `app/review.py` changes.

## Alternatives rejected

- **Clear `document_id` on every save that posts a quote.** That is every
  save, because the textarea is always posted. An unchanged quote could then
  move to another member that also contains it, and the member title shown
  would change for no reason.
- **Let the reviewer pick the member in the form.** That adds new UI, and the
  quote already identifies the member.
- **Have `check()` try every member for every field.** It would hide a real
  origin mismatch on fields the reviewer never touched, and it changes
  `check()` for all callers.

## Risks

- **Two members contain the new quote.** The first member in `text` order
  wins. Both vouch for it, so either is correct.
- **No schema change, no host impact.**

## Verification

New tests in `tests/test_review.py`, using `eng`, `merge`, `ver`, `acme`,
`TEXT_A` and `TEXT_B` (`:341-375`):

- `test_merged_edit_can_quote_another_member`: merge A and B with every
  field picked from A. Edit `industry` to "Payments" with quote "Acme moved
  payments to the cloud", from B. The field is not unsourced, and its
  `document_id` is B's document. On main it is unsourced, with A's id.
- `test_merged_edit_quote_in_no_member_is_unsourced`: a quote found in
  neither member gives `unsourced` true and `document_id` null.
- `test_merged_edit_same_quote_keeps_origin`: re-saving with an unchanged
  quote keeps `document_id`.

The full suite passes.
