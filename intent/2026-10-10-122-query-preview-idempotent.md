---
status: draft
issue: 122
author: olafkfreund
---

# Intent: Research query preview can differ from what is sent

## Problem

`research.finalize` (`app/research.py:81-87`) runs in this order:

1. strip emails, URLs and long numbers (`_IDENTIFIERS`);
2. scrub client names;
3. cap the length.

It runs on preview, and again on send (`:150`), on the query the preview
shows. Its docstring and the preview page both promise that the preview is
exactly what gets sent.

The two runs can give different results. Removing a client name can leave two
numbers next to each other, and the second run then strips them as one long
number. With a made-up protected client "Zorp Bank":

- the user asks `Basel 2023 Zorp Bank 2024 reporting`;
- the preview shows `basel 2023 2024 reporting`;
- the query sent and stored is `basel reporting`.

This only ever removes text, so nothing leaks. But the user approved one query
and a different one was sent, which breaks the promise made on the page.

## Proposed outcome

`finalize(finalize(x)) == finalize(x)` holds for any input, so the query
shown on the preview is the query that is sent.

## Affected users and systems

- Bid team users of research, through `app/research.py` (`finalize`).
- Tests: `tests/test_research.py`.
- Not affected: the anonymiser, the fetcher and stored results.

## Constraints

- Fail closed. The `blocked()` refusal stays, and a fix may only strip more
  text, never less.
- The fix stays in `finalize`, which both paths call.

## Open questions

None. Running the identifier filter again after the scrub makes one pass give
the final result.
