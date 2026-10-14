---
status: draft
issue: 264
author: olafkfreund
---

# Intent: Architecture notes that go away once the diagram is fixed

## Problem

Extraction and Regenerate add architecture notes to `case.needs_attention`:
- "architecture has N unsupported item(s); edit it before approval"
  (`app/writeup.py:222`)
- "architecture proposal failed (…)" (`:215`)
- "architecture: no new proposal…" and "architecture: the model proposed
  nothing…" (`app/review.py:614,616`)

These notes are only ever appended. When the reviewer then saves a clean
architecture (0 flags), or removes the diagram, the old note stays.

On 2026-10-14, case 17 kept showing "architecture has 7 unsupported
item(s)" under "Needs your attention" after its diagram was saved with 0
flags. The #259 empty state also uses that note as its reason line.

The result is that reviewers see a task that is already done and stop
trusting the attention list.

## Proposed outcome

- **Clean save.** Saving an architecture by hand, or a Regenerate that
  succeeds, drops every earlier architecture note. If the new diagram has
  flags, exactly one fresh note says how many, instead of stacking a new
  note on top of the old ones.
- **Remove.** Removing the architecture drops its notes too.
- **Other notes are untouched.** Re-extraction, unsourced fields and
  similar notes stay as they are.
- **Existing stale notes clear on the next architecture save.** Case 17 is
  fixed by re-saving, with no data migration.

## Affected users and systems

- Reviewers on `/review/{cid}`.
- `app/review.py` (the architecture save, regenerate and remove routes),
  possibly a small helper shared with `app/writeup.py`.
- `tests/test_review*.py`.

## Constraints

- No change to how flags are computed or to the approval gate.
- Only architecture notes are touched, matched by their known prefixes
  ("architecture has ", "architecture proposal failed",
  "architecture: ").

## Open questions

None.
