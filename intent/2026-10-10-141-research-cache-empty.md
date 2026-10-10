---
status: draft
issue: 141
author: olafkfreund
---

# Intent: A research run where every page failed is cached and reused for 30 days

## Problem

The research worker reuses an earlier `done` row for the same query
(`app/research.py:437`), so it doesn't call Brave and refetch pages.

Brave can return hits while every fetch or conversion fails for a short-lived
reason: a timeout, a busy converter. That run is still stored as `done`, with
`pages: []`. Every later run of the same query is then answered from that
empty row and shows "No usable sources found" until the cache expires.

"Research from case" builds the same query for a case each time, so the user
can't get past it.

## Proposed outcome

A run with no usable pages is never reused. The next run of that query
searches and fetches again.

## Affected users and systems

- Bid team research users: `app/research.py` (the cache lookup).
- Tests: `tests/test_research.py`.

## Constraints

- A cached run with pages is reused exactly as today, with no extra Brave
  calls. The key is paid for, so call it only when needed.

## Open questions

None. The cache lookup only matches rows with at least one page.
