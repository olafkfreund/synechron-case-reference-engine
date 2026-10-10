---
status: draft
issue: 131
author: olafkfreund
---

# Intent: A model reply with a very long number crashes extraction instead of being skipped

## Problem

`schema.assemble` (`app/schema.py:186-190`) turns the model's reply items
into a case. For an integer field, it takes the first number in the value and
calls `int()` on it. Its docstring promises that a malformed item is skipped
and counted.

A value of more than 4300 digits makes Python's `int()` raise `ValueError`.
A small model stuck in a repetition loop can produce one. The error fails the
whole build. The job is retried and gets the same reply at temperature 0, so it
ends `failed`, and the document gets no case. For a changed document, the old
case has already been reopened, so a stale case stays in review.

## Proposed outcome

- An integer value that can't be parsed is skipped and counted as malformed,
  the same as any other malformed item.
- The rest of the reply still becomes a case.

## Affected users and systems

- `app/schema.py` (`assemble`), which extraction uses.
- Tests: `tests/test_schema.py`.

## Constraints

- No invented values: a number too long to be real is dropped, not truncated.

## Open questions

None. The fix caps the regex at a plausible length (12 digits with
separators), so longer runs don't match and the item counts as malformed.
