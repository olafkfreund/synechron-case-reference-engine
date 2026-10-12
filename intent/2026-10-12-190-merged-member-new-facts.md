---
status: approved
issue: 190
author: olafkfreund
---

# Intent: A merged case does not pick up a re-extracted member's new facts

## Problem

A merged case (#55) is a separate row whose `data` is built once, by
`combine()` in `do_merge` (`app/review.py`), from its member cases. The
members keep `merged_into` and are hidden from the queue (`VISIBLE`).

When a member document changes:

- `ingest()` re-opens the member case and calls `reopen_merged`
  (`app/ingest.py`), so the merged case goes back to "extracted".
- The extract job upserts the member case with the new facts
  (`app/extract.py`). It keeps `merged_into`, so the member stays hidden.
- Nothing rebuilds the merged case's `data`.

The reviewer then reviews the old merged facts. On save or approve,
`check()` runs them against each member's current text:

- facts the new version no longer supports are flagged unsourced, and
  approve drops them;
- facts that are new in that version never appear.

The only way to get them is to un-merge and merge again. The review page
does not say that a member changed, or that this is what to do.

Nothing unsupported is published, because approval is re-opened and
`check()` drops stale facts. But a merged reference quietly loses facts each
time a member contract is revised. In the worst case it falls below what can
be approved: an empty title blocks approval.

## Proposed outcome

- After a member document changes, the reviewer of the merged case can see
  and approve the member's current facts without un-merging by hand.
- The reviewer can tell which member changed since the merge.
- An approved merged case never contains a fact that its members' current
  documents don't support (unchanged from today).

## Affected users and systems

- Reviewers of merged engagement cases.
- `app/review.py` (merged view, `do_merge`, `combine`), `app/ingest.py`
  (`reopen_merged`), `app/extract.py` (the case upsert),
  `templates/review_detail.html`, `docs/user-guide/reviewers.md`.

## Constraints

- Keep the lock order (source, then documents, then cases; members before the
  merged case, #144).
- The merged row is kept: generations and research point at it.
- A member that is no longer an engagement still blocks approval (#55).
- No model call on page view.

## Open questions

1. Rebuild the merged `data` automatically once the member's re-extraction
   lands? That loses the reviewer's per-field choices and edits on the
   merged case. Or should the page flag "member changed since merge" and
   offer a one-step re-merge with the same members, keeping the earlier
   choices as defaults?
2. Do the same for a withdrawn (deleted) member, which shows a badge today
   but is otherwise left alone?

**Decided by the user, 2026-10-12:** no automatic rebuild. The merged case page says a member changed (or was withdrawn) since the merge and offers a one-step re-merge; the reviewer's choices and edits stay until they use it.
