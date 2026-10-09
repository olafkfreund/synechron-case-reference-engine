---
status: approved
issue: 108
author: olafkfreund
---

# Intent: an outcome with no value survives approval

## Problem

A reviewer can edit an existing outcome so that its value is empty, and the
record keeps it as a valid, sourced outcome.

How it happens, confirmed in the code on main:

1. **The edit is accepted.** Editing `outcomes.N` (`app/review.py`, the
   `edit` handler) sets `obj.metric, obj.value = metric or "", value or ""`.
   Since #99, whitespace-only input is trimmed to `""`. Nothing refuses an
   empty value here. This differs from adding an outcome (`outcomes.new`),
   which refuses a blank value or metric with 400.
2. **The outcome passes as sourced.** `check()` (`app/extract.py:104-105`)
   tests outcomes as `mark(o, f"{o.metric} {o.value}")`. With both parts
   empty, the string is `" "`, which is not `""`, so the "empty values are
   not flagged" rule does not apply.
   - `sourced(" ", quote, text)` (`app/schema.py:41-47`) is true for any
     quote of 4 or more words that is in the document, because `" "` has no
     numbers.
   - The outcome is marked sourced, so approval keeps it.
3. **A metric with an empty value behaves the same way.** For example,
   `metric="Revenue"` with `value=""` gives the string `"Revenue "`, which a
   long quote vouches for.

What the user sees after approval:

- **Search results** (`app/search.py:136-137`, `search.html:21`) show a
  check-marked outcome with no text, or one reading just "Revenue: ".
- **Generated documents** (`app/render.py:92`) drop an outcome that is
  entirely empty, because `section()` skips empty bullets. But they print
  "Revenue: " with nothing after it.
- **Search text** keeps the empty outcome (`app/search.py:45`). This is
  harmless.

The issue's own wording says the empty bullet appears in generated output.
That holds only when the metric is set. When both parts are empty, the bullet
appears in search, not in documents.

## Proposed outcome

- An outcome cannot be approved without a value. Either the reviewer is
  told on save, or the outcome is treated as empty and approval drops it.
- Search and generated documents never show an outcome bullet with no
  value, or one with a metric followed by nothing.
- Existing records already holding such an outcome are handled by the same
  rule the next time they are checked. No migration is needed.

## Affected users and systems

- **Reviewers:** the outcome edit on the review page.
- **Bid team:** search results and the generated DOCX, PPTX, PDF and MD files.
- **Code:**
  - `app/review.py` (`edit`)
  - possibly `app/extract.py` (`check()`), which extraction also uses
  - `tests/test_review.py`
- No schema, infra, or demo-data change.

## Constraints

- **Fail closed:** a doubtful outcome is dropped or refused, never shown.
- **Extraction stays the same for real outcomes.** If `check()` changes, a
  model-extracted outcome with a value must be marked exactly as today.
  Run the extraction tests and `scripts/eval_extraction.py` on the made-up
  demo documents only.
- **Keep the add path's 400.** The existing refusal on adding an outcome,
  and its test, stay as they are.
- **No confidential data:** no presale documents are used in tests or evals.

## Open questions

What should happen to an outcome whose value is empty?

1. **Refuse the edit (400), as the add path does.**
   - **Where:** `review.py` only, a small change.
   - **Gap:** it doesn't cover outcomes that are already stored.
   - **Gap:** it doesn't cover the extraction model returning an empty
     value.
   - **Also needed:** the reviewer still needs a way to clear a bad outcome.
     Remove (#40) already does that.
2. **Treat an empty value as empty in `check()`.** Mark the outcome on its
   value, so an empty value is "not flagged". It also needs a rule so that
   approval drops it, because approval today drops only unsourced fields.
   - **Coverage:** every path, including extraction and stored records.
   - **Cost:** it touches the shared `check()` and approval.
3. **Mark it unsourced in `check()`.** An outcome with an empty value gets
   `unsourced=True`, so approval removes it like any unsourced field, and
   search and documents already skip it.
   - **Where:** one condition in `check()`; it fails closed.
   - **Coverage:** every path, including extraction and stored records.
   - **Reviewer feedback:** the reviewer sees it flagged before approving.

**Recommendation: option 3, plus option 1's 400 on the edit.** Option 3 is
the root-cause fix: one rule in the function every path goes through. The
400 gives the reviewer immediate feedback, and matches the add path. If you
want the smallest change, option 3 alone is enough.

A second, smaller question: should a metric with an empty value
(`"Revenue"`, `""`) fall under the same rule? My recommendation is yes:
treat it as no value.

## Approved answers

1. Option 3 plus option 1: check() marks an outcome with an empty value unsourced, and edit refuses such a save with 400.
2. Yes: a metric with an empty value falls under the same rule.
