---
status: draft
issue: 128
author: olafkfreund
---

# Intent: Merged case: an edited quote can only be sourced from the member it was first taken from

## Problem

A merged engagement combines several contracts. `combine`
(`app/review.py:271-280`) gives each field the `document_id` of the member it
was picked from. That is the first member by default, even when the field is
empty in every member.

When a reviewer edits a field, `edit` (`:206-207`) replaces the quote but
keeps that `document_id`. `extract.check` (`app/extract.py:91-93`) then
checks the quote only against that one document.

So a reviewer who quotes member B for a field that came from member A gets
"unsourced", and Approve empties the field. With made-up contracts:

- A: "Acme cut onboarding…"
- B: "Acme moved payments to the cloud…"

Setting industry to "Payments", quoting B, fails. The only way out is to
un-merge. Items a reviewer adds have `document_id = None`, so they already
resolve to whichever member contains the quote (#40).

## Proposed outcome

On a merged case, an edited field is sourced from whichever member's text
contains the new quote, the same way an added item is. Single-document cases
are unchanged.

## Affected users and systems

- Reviewers of merged engagements: `app/review.py` (`edit`).
- `app/extract.py` (`check`) is reused, not changed.
- Tests: `tests/test_review.py`.

## Constraints

- A quote found in no member stays unsourced (fail closed).
- Single-document cases keep their current behaviour, because `check` uses
  the one text there.

## Open questions

None. When a quote is supplied, the edit clears the field's `document_id`,
and `check` resolves it.
