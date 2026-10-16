---
status: approved
issue: 24
author: olafkfreund
---

# Intent: The recall gate measures what the AI can pick from

## Problem

Plan step 22 says: "verify recall ≥ 90% before org rollout. Below that, add
`pgvector` on `summary` and re-measure." `scripts/recall.py` scores that as the
**top-3** hit rate. `--min` (default 0.90) checks top-3, and the exit code
follows it. Top-20 is printed but never gates anything.

Top-3 and top-20 answer different questions:

- **Top-20** is the candidate set the AI picks from. An expected case outside
  it can never reach an output. This is a retrieval failure, and it is what
  `pgvector` on `summary` would fix.
- **Top-3** is what a user sees first. A miss there with a hit in the top 20 is
  a ranking problem, which `pgvector` does not obviously fix.

Gating on top-3 can therefore send us to `pgvector` for a problem it does not
solve, or hold up rollout over ordering the AI step already handles. The user
has chosen to gate on top-20 (#24).

## Proposed outcome

- `scripts/recall.py --min` checks the **top-20** hit rate, and the exit code
  follows it (0 at or above, 1 below, 2 when there is nothing to score).
- Top-3 is still printed, with the misses, for information only.
- The summary line shows which number is the gate.
- Plan step 22 records the decision: top-20 ≥ 90% before org rollout, and below
  that, add `pgvector` and re-measure.

## Affected users and systems

- Engineering, running the recall gate (#35) once the bid team's 50 queries
  exist (#20).
- `scripts/recall.py`, `tests/test_hardening.py` (the recall test), and plan
  step 22.

## Constraints

- No AWS, Bedrock or real queries needed: the existing test builds its own cases.
- Exit codes and flags keep their meaning, apart from which rate `--min`
  checks.

## Open questions

None.
