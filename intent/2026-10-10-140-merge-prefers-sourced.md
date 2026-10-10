---
status: approved
issue: 140
author: olafkfreund
---

# Intent: Merging keeps a member's unsourced copy of a field and drops another member's sourced one

## Problem

Merging combines several contracts' cases into one engagement. `combine`
(`app/review.py:278-288`) does two things:

- for each scalar field, it takes the picked member's copy, the first member
  by default;
- it removes duplicate list items (capabilities, tech) by keeping the first
  copy.

Neither step looks at `unsourced`. The merge preview (`:351`) only offers a
choice when the members' values differ.

So take members A and B with the same title value, where A's quote failed the
check and B's passed. The preview offers no choice, and the merged title is
A's unsourced copy. Approve then fails with "title is empty". Duplicate
capabilities and tech lose B's sourced copy the same way.

## Proposed outcome

When members hold the same value, the merge keeps a sourced copy if any member
has one. The reviewer only has to choose when the members really differ.

## Affected users and systems

- Reviewers merging engagements: `app/review.py` (`combine`, `merge_preview`).
- Tests: `tests/test_review.py`.

## Constraints

- A field where every copy is unsourced stays unsourced. Fail closed.
- An explicit pick by the reviewer still wins.
- The origin (`document_id`) is the member whose copy is kept.

## Open questions

None. When the reviewer hasn't picked, `combine` prefers the sourced copy, and
its de-duplication keeps a sourced duplicate over an unsourced one.
