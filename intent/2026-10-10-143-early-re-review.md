---
status: approved
issue: 143
author: olafkfreund
---

# Intent: Reviewers' guide says to review "due soon" cases, but they cannot be edited or approved until they expire

## Problem

An approved case is due for re-review at `review_due`. The review queue
lists **Due for re-review within 30 days**: approved cases whose
`review_due` is still in the future. The reviewers' guide
(`docs/user-guide/reviewers.md:136-137`) says to open each one and review
it.

The code doesn't allow that. A case is open for review only once
`review_due < now()` (`OPEN`, `app/review.py:29`):

- the page shows "This case is not open for review";
- edits and approve return 409.

So a reviewer can't renew an approval early. The case expires, drops out of
search, and only then comes back under **Awaiting review**. Bid users lose the
case from search for as long as the re-review takes.

## Proposed outcome

The guide and the app agree on when a reviewer can re-review an approved case,
and the documented workflow works.

## Affected users and systems

- Reviewers: `app/review.py` (`OPEN`) or `docs/user-guide/reviewers.md`.
- Bid users, who lose expired cases from search while they wait.
- Tests: `tests/test_review.py`.

## Constraints

- **Gate:** a case approved early must still pass every check `approve` runs.
- **Search during re-review:** the case stays searchable until it is
  re-approved or expires. An edit during early review must not unpublish it by
  accident. This needs to be confirmed in the spec.

## Open questions

1. **Fix the code or the guide?**
   - **A. Guide only.** It says due-soon cases open for review once they
     expire, and then appear under Awaiting review. No code change, but the
     search gap stays.
   - **B. Allow early re-review.** `OPEN` also includes approved cases due
     within 30 days. Re-approving sets a new `review_due`, and the case
     stays in search throughout.

   **Recommendation: B.** It matches the guide and what a reviewer expects,
   and it closes the search gap. The spec must check how an edit on an
   approved case behaves, whether it resets the status, and keep the case
   searchable while it is under review.

**Decision (approved by olafkfreund, 2026-10-10): B.**
