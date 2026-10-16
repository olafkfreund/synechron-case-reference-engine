---
status: approved
issue: 143
intent: intent/2026-10-10-143-early-re-review.md
---

# Spec: Open approved cases for re-review 30 days before they expire

## Design

Approved decision B: an approved case can be reviewed from 30 days before it expires.

**1. When a case is open (`app/review.py`).** `OPEN` becomes:

```python
OPEN = "(c.status = 'extracted' or (c.status = 'approved' and c.review_due <= now() + interval '30 days'))"
```

`load()`, `review_detail` and therefore `edit`, `approve` and `reject` all
use `OPEN`, so all of them open together.

The 30-day window is the same window the "Due for re-review within 30 days"
list already uses (`review_list`, the `soon` query), so every case on that list
can now be opened.

**2. The queue does not list a case twice (`review_list`).** The main list keeps
today's rule (new or expired), as a new constant:

```python
QUEUE = "(c.status = 'extracted' or (c.status = 'approved' and c.review_due < now()))"
```

`review_list`'s `found` query uses `QUEUE` instead of `OPEN`. The due-soon list
does not change.

**3. Re-approving.** `approve` does not change.

- It runs every check against the case as it is: `check()`, the unsourced
  strip, the empty-title refusal and the merged-member guard.
- It then sets `status='approved'` and a new `review_due = now() + 12 months`.
- The case stays approved throughout, so it never leaves search.

This meets the intent's gate constraint.

**4. An edit on an approved case that has not yet expired withdraws its approval (`edit`).**
`save()` writes `data`, `summary` and `search_text` to the same row search reads
(`app/search.py:61` filters on `status='approved' and review_due > now()`).
Without a change, an edit during early review would publish unreviewed text in
search at once, including an unsourced value that `approve` would have stripped.

So after `save()`, `edit` runs:

```python
conn.execute("update cases set status='extracted', approved_by=null, approved_at=null, review_due=null "
             "where id=%s and status='approved' and review_due > now()", (cid,))
```

- The edited case leaves search and goes back to the main queue as "new" until
  it is approved again.
- An expired case (already out of search) keeps today's behaviour.
- Reject keeps its current behaviour: the reviewer is choosing to unpublish.

**5. The reviewer is told (`app/templates/review_detail.html`).** When the case is
open and `status == 'approved'`, the page shows one note above the fields:

> This case is approved and in search until {{ due }}. Approving it again renews
> it for 12 months. Saving any change takes it out of search until you approve it
> again.

`review_detail` passes `due`, the `review_due` date, by adding `c.review_due`
to its select.

**6. The guide (`docs/user-guide/reviewers.md`, Re-review).** Step 2 becomes:

> Open each case. Approve it as it is to renew it for 12 months; it stays in
> search. If you edit it, it leaves search until you approve it again.

## Alternatives rejected

- **Guide only (A):** rejected by the approver. The search gap stays.
- **Edits keep the approval and stay live:** this publishes unreviewed and
  possibly unsourced text in search, bypassing the approve gate.
- **A draft copy of the data while under review:** a new column and a merge on
  approve. That is too much for this; withdrawing on edit is one statement.
- **No edits during early review, approve and reject only:** this forces a
  reviewer who finds a mistake to reject the case, or to wait for it to expire.
  It is worse than withdrawing on edit.

## Risks

- **A reviewer edits by mistake and the case leaves search.** The note warns
  before the first save, and approving again restores the case.
- **Merge candidates.** `candidates()` and `lock_members` already accept
  approved engagements. Opening the detail page earlier exposes the merge
  form on due-soon approved engagements as well (`cands` is gated on `r[5]`, the
  `OPEN` flag). Merging creates a new extracted case, and the members leave search
  (`VISIBLE` needs `merged_into is null`). That is today's behaviour for expired
  ones, now also 30 days earlier. Accepted: the reviewer chose to merge.
- **Existing tests that assume a 30-day approved case is closed**
  (`tests/test_review.py::test_approving_decided_case_is_409` and
  `::test_no_add_or_remove_buttons_when_not_reviewable`, both using
  `make("approved", "30 days")`) must move to `"60 days"`. Their intent, an
  in-date approval outside the window being closed, is unchanged.

## Verification

New tests in `tests/test_review.py`, all of which fail on main:

- `test_due_soon_case_can_be_reapproved_and_stays_searchable`:
  - `make("approved", "10 days")`;
  - the detail page shows the approve form and the note;
  - approve returns 303;
  - `row()` shows `approved` with `review_due` more than 364 days away;
  - the case is still returned by `/search` throughout.
- `test_edit_on_due_soon_case_withdraws_approval`:
  - an edit returns 303;
  - status is `extracted` and `review_due` is null;
  - the case is listed in the main queue as new and is not in `/search`.
- `test_due_soon_case_not_listed_twice`:
  - a 10-day case appears only after "Due for re-review within 30 days" on `/review`.

Existing tests:

- `test_list_shows_extracted_and_expired_only` and
  `tests/test_hardening.py::test_review_page_lists_approvals_expiring_within_30_days`
  pass unchanged.
- The two tests above move to `"60 days"`.

Run the full suite with
`docker compose run --rm app timeout 900 pytest -q -p no:cacheprovider`. It must be green.
