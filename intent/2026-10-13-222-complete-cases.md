---
status: approved
issue: 222
author: olafkfreund
---

# Intent: Approved cases are complete enough to present

## Problem

#212 makes extraction gather much more: points per section, a written-up
paragraph per section, and more capabilities and technologies. But nothing
makes an *approved* case complete:

- **A thin case can still be approved.** Approval checks quotes and flags,
  not substance. A case with a one-sentence challenge, no technologies and
  no diagram can be approved and shown to a customer.
- **Older cases stay thin.** Cases extracted before #212 keep their old,
  thin content. The only way to refresh them is to re-queue extraction by
  hand, as was done for the two trial cases on 2026-10-13. Re-extraction
  also moves an approved case back to review, and nobody is told why.
- **Reviewers can't see what's missing.** The review page lists fields, but
  doesn't say "this case has no configuration text and no diagram; a
  customer-ready case usually has them".

## Proposed outcome

- **A completeness checklist on the review page,** with a score, for
  example "7 of 9". Each item says what's present and what's missing, with a
  link to where to add it:
  - Challenge text of at least ~80 words
  - Solution text of at least ~80 words
  - Implementation approach
  - Configuration and integration
  - At least 3 capabilities
  - At least 3 technologies
  - Outcomes for delivered work, or scope and duration for engagements
  - Period or duration
  - At least one diagram

  The checklist is advice. It's never a reason to invent content: missing
  items are filled from the documents, by the reviewer, or left empty.
- **Approval needs a minimum.** A case below the minimum (proposed:
  Challenge and Solution text present, plus at least 2 of the remaining
  items) can only be approved with a short reason from the reviewer, which is
  recorded in the audit log.
- **Search shows completeness.** Bid users see a small "complete" or
  "partial" marker on each case, so they pick full cases for proposals.
- **Bulk re-extraction for admins.** "Re-extract cases made with older
  extraction" queues the documents whose cases predate the current extraction
  version. Reviewer-written text is kept (#198 and #212 rules). Each case
  goes back to review with a note: "re-extracted with richer extraction;
  check and approve again". Cases that are already up to date are untouched.
- **An extraction version** stored on each case, so "older" is known and not
  guessed.

## Affected users and systems

- Reviewers (the checklist, the approval minimum), bid users (the marker in
  search) and admins (bulk re-extraction).
- `app/review.py` (approve, review detail), `review_detail.html`,
  `app/search.py` and `search.html` (the marker), `app/extract.py` (the
  version stamp), an admin action on the Sources page, and the audit log.

## Constraints

- The checklist never makes up content and never lowers the sourcing rules.
- Bulk re-extraction runs on the worker in the background, rate-limited for
  the local model. It never deletes a case and never discards reviewer text.
- The minimum and thresholds live in one place in the code, so they're easy
  to tune once the bid team has used it.

## Decisions (2026-10-13, approved with the intent)

1. The approval minimum is a hard block. The reviewer can override it with a
   short reason, recorded in the audit log.
2. Thresholds to start with: about 80 words each for Challenge and Solution,
   and at least 3 capabilities and 3 technologies, kept in one place so they
   are easy to tune.
