---
status: draft
issue: 222
intent: intent/2026-10-13-222-complete-cases.md
---

# Spec: Approved cases are complete enough to present

## Design

Four small pieces, one PR. No new job kind, and no migration for the case
data itself (`cases.data` is JSON).

### 1. One pure checklist (`app/completeness.py`, new)

```python
CHECKS = {"challenge_words": 80, "solution_words": 80, "capabilities": 3, "technologies": 3}  # tune here only
MINIMUM_OTHERS = 2  # beyond challenge and solution

def checklist(case: ReferenceCase) -> list[dict]   # [{key, label, ok, detail}]
def meets_minimum(items) -> bool
```

`checklist()` reads only the case: no database and no model. It returns nine
items, in the intent's order:

| key | ok when |
|---|---|
| challenge | section text has ≥ `challenge_words` words |
| solution | section text has ≥ `solution_words` words |
| implementation | section text is not empty |
| configuration | section text is not empty |
| capabilities | ≥ 3 sourced capabilities |
| technologies | ≥ 3 sourced `tech_stack` items |
| results | delivered: ≥ 1 sourced outcome; engagement: scope (solution present) and `duration_months` |
| period | `period.start`, or `duration_months` |
| diagram | ≥ 1 diagram with status `ready` |

**Section text** is `case.prose[f].text` when present (#212 B). Otherwise it
is the headline value plus the sourced point values (`case.points[f]`, from
#212 A), joined. Unsourced items never count, so the checklist can't be met
with text the documents don't back.

**Graceful before #212 B and C:**
- Before #212 B, `prose` doesn't exist yet. `getattr(case, "prose", {})`
  falls back to headline plus points, which #212 A already provides on main.
- Diagrams use the #200 `case.diagrams` list, which already exists. #212 C
  only adds more ways to get one.
- No item depends on code that isn't merged.

`detail` is a short plain string, for example "42 of 80 words" or "1 of 3".
`meets_minimum()` holds when challenge and solution are ok and at least
`MINIMUM_OTHERS` of the other seven are ok.

### 2. Review page and approval (`app/review.py`, `review_detail.html`, `sql/schema.sql`)

- **`review_detail`** passes `checklist(case)` and `meets_minimum`. The
  template adds a "Completeness: 7 of 9" card at the top: one row per item,
  with ✓ or ✗ and its detail. Each ✗ row links to that field's anchor on the
  page. It's text, not colour alone, for accessibility.
- **`approve()`** gets a new form field, `reason: str = Form("")`. It
  computes the checklist **after** the existing unsourced drops (what gets
  approved is what gets scored).
  - If `not meets_minimum(...)` and `reason.strip()` is empty: return 400
    "below the minimum: complete the case or give a reason".
  - If a reason is given below the minimum: insert into a new table,
    `case_approval_overrides(id, case_id, reason, score, approved_by,
    approved_at)`. This follows the `source_class_changes` pattern, with
    the reason capped at 500 characters.
- **Audit page** (`app/audit.py`, `audit.html`) lists the latest 100
  overrides, like the class and ACL change tables. The reason text is
  autoescaped.
- The approve form shows the reason box only when the minimum isn't met.

### 3. Complete or partial marker (`app/search.py`, `search.html`)

`view()` adds `score = (sum ok, total)` from `checklist(c["case"])`. The case
is already loaded, so this adds no query. Browse rows and bid-text results
show a small badge: "Complete" when all 9 are ok, otherwise "Partial · 7/9".
Only approved cases appear there, so there's no reviewer data to leak.

### 4. Extraction version and bulk re-extraction

- **Version.** `app/extract.py` gets `EXTRACTION_VERSION = 2`, where 1 means
  everything before #212 A. `ReferenceCase` gets
  `extraction_version: SkipJsonSchema[int] = 1`, so old rows load as 1.
  `build()` stamps the current version. **Every PR that changes extraction
  output bumps it.** #212 B will set 3, and that is written into #212's plan
  as a trap.
- **The note.** In `extract()`, when the old row's version is below the
  current one, it appends "Re-extracted with richer extraction: check and
  approve again" to `needs_attention`. Reviewer text is kept as today
  (#198 and #212 `by_reviewer`). The existing upsert already returns the case
  to `extracted` and clears the approval.
- **The admin action.** `POST /admin/reextract` (admin only) is a button on
  the Sources page: "Re-extract older cases (N)". N is counted in the same
  query.
  - It inserts at most `REEXTRACT_BATCH = 25` extract jobs per click, using
    the `QUEUE_CONTRACTS` insert pattern in `app/sources.py`:
    `basis`, `basis_reason` and the document's current `checksum` in the
    payload.
  - It only queues cases where
    `coalesce((data->>'extraction_version')::int, 1) < EXTRACTION_VERSION`,
    `status <> 'rejected'` and `document_id is not null`, and whose document
    is live and has no extract job queued or running.
  - A merged parent has no document of its own, so it's skipped. Its members
    are re-extracted, and `reopen_merged` already sends the parent back to
    review.
  - **Rate limit:** the single worker runs jobs one at a time, and the
    25-per-click cap keeps the queue bounded on the local model. Clicking
    again queues the next batch. Cases already at the current version are
    never queued.
  - The redirect shows the notice "Queued N of M older cases".

## Alternatives rejected

- **A new `reextract` job kind.** The existing `extract` job already does
  exactly this, including the checksum and source-flag guards (#132, #159).
- **A `completeness` column kept up to date.** It's derived data that would
  go stale on every edit. Computing it from the loaded case is cheap.
- **Scoring with a model.** That's slow and not explainable. The checklist
  must say plainly what's missing.
- **A hard block with no override.** A real reference can legitimately lack
  a diagram or have few technologies. The reason is recorded instead.
- **Re-extracting everything automatically on deploy.** On the local model
  that's hours of model time and a review queue flooded without warning. The
  admin chooses when.

## Risks

- **Re-extraction un-approves cases.** A re-extracted case leaves search until
  it's approved again. This is intended, and the button's label says so:
  "cases go back to review".
- **Thresholds are guesses.** 80 words and 3 items are starting points, kept
  in one constant to tune after the bid team uses them.
- **A forgotten version bump.** A PR that changes extraction but doesn't bump
  `EXTRACTION_VERSION` leaves older cases unflagged. A test pins the current
  value, so changing extraction code makes the reviewer look at it. That's
  a weak guard; it's mainly a plan-level trap.
- **Word counts on headline plus points** overstate fluency: a list of points
  can reach 80 words. That's acceptable, because the points are sourced.

## Verification

- Unit tests for `checklist()` and `meets_minimum()`:
  - each item ok and not ok
  - engagement against delivered results
  - prose preferred over points
  - unsourced items not counted
  - a case with no `prose` attribute (before #212 B)
- Approve:
  - below the minimum with no reason gives 400
  - with a reason it approves, writes an override row, and the row is on
    the audit page, escaped
  - above the minimum, approval needs no reason
- Search: the badge shows "Complete" or "Partial · n/9" on browse and bid
  results.
- Re-extract:
  - only older, non-rejected, live, not-already-queued cases are queued
  - capped at 25
  - plain users get 403
  - the re-extracted case gets the note and keeps reviewer text
  - an up-to-date case is not queued
- The full suite:
  `docker compose build app && docker compose run --rm app pytest`.
- By hand in the demo: the two trial cases show their score, and
  "Re-extract older cases" queues only the demo cases with an older version.
