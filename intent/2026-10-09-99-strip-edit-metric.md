---
status: approved
issue: 99
author: olafkfreund
---

# Intent: Reviewer edit keeps a whitespace-only outcome metric

## Problem

`edit` in `app/review.py` stores what the reviewer typed without trimming it.

- Editing an outcome stores both parts as typed: `obj.metric, obj.value = metric or "", value or ""`
  (`app/review.py:195`). A metric of `"  "` is truthy, so the outputs print `"  : value"`:
  `app/render.py:92` and `app/search.py:133` both use `f"{o.metric}: {o.value}" if o.metric else o.value`.
  The search index text (`app/search.py:45`) also gets the padding.
- Adding an outcome refuses blank parts (`app/review.py:181` checks `.strip()`), but then stores
  `metric` and `value` unstripped (`app/review.py:183`), so `" Revenue "` keeps its spaces.
- The check in `vague_metric` (`app/schema.py:75-78`) strips before comparing, but only
  `extract.py:127` calls it, at extraction. Nothing flags a blank metric after a reviewer edits it.
- Other edited text fields have the same problem:
  - Text scalars store `value or None` (`app/review.py:179`). A value of `"  "` is not `None`.
  - Edited list items (capabilities, tech stack) store `value or None` (`app/review.py:197`).
  - Quotes are stored as typed (`app/review.py:204`). Summary is stored as `value or ""` (`app/review.py:174`).
  - Integer scalars already strip (`app/review.py:179`).
  - Period start and end are stored as `start or None, end or None` (`app/review.py:200`).
- A whitespace value can also count as sourced. `check` (`app/extract.py:94-97`) flags only
  `None` or `""`, and `_norm("  ")` is `""` (`app/schema.py:19-20`). `""` is a substring of any quote,
  so `sourced` (`app/schema.py:47`) passes when the quote is in the document. The blank field
  is then kept on approve (`app/review.py:222-225`).

## Proposed outcome

- A reviewer's edit or add stores the outcome metric and value with outer spaces removed.
- A metric of only spaces is stored as `""`, so outputs show the value alone and never `" : value"`.
- A test covers this.
- Depending on the open question, other edited text fields are trimmed the same way.
  A field left blank then becomes `None` (or `""`), so it is not flagged and not shown.

## Affected users and systems

- Reviewers using the review page (`/review/{cid}/edit`).
- Readers of rendered outputs (`app/render.py`) and search results (`app/search.py`).
- Code: `app/review.py` `edit` only. No change to the schema, extraction or templates.

## Constraints

- The server still decides `unsourced`. `check()` recomputes it after the edit, as now (`app/review.py:164`, `:208`),
  and it is never read from the form.
- The only behaviour change is trimming outer whitespace. Validation, error messages,
  the add-row refusals at `:181` and the summary re-validation at `:205` stay the same.
- Stored cases are not migrated. Existing padded values stay until someone edits them.

## Open questions

Which fields should be trimmed?

1. **Only the outcome metric and value** (`:183`, `:195`). This is exactly what the issue asks for.
2. **Every edited text field**: metric, value, scalar and list values, quote, summary and
   period. Add one trim at the top of `edit`. A blank input then goes through the existing
   `or None` / `or ""` paths. This also closes the whitespace-counts-as-sourced gap above.

Lean: **option 2**. The code change is about as small (one place instead of two), it fixes
the same bug in the fields beside it, and it trims surrounding spaces only.
Inner text and quote matching are unchanged, because `_norm` already collapses whitespace.
