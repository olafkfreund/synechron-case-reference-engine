---
status: draft
issue: 215
intent: intent/2026-10-13-215-browse-latest.md
---

# Spec: Open on what we have: latest cases, documents and their state

## Design

Three pages change: the search page, a new "Latest documents" page and the
home page. There is no new table, no new job, and no change to triage or
crawling. Everything is read from rows that already exist.

### 1. Search opens on a browsable list (`app/search.py`, `search.html`)

**Two entry points, one function.**
- `GET /search?industry=&region=&tech=&kind=&sort=&page=` browses.
- `POST /search` with bid text ranks, as today. The bid text stays in the POST
  body. The filters may sit in the query string, because they are values
  picked from the visible cases, not confidential bid text.
- A POST with an empty bid text renders the same page as the GET. It never
  calls a model. Today `search_page` runs `results()`, and so `pick()`, for a
  filter-only search.

**Browse query.**
- `search()` gains `kind`, `sort` and `page` parameters.
- With no bid text, it orders by the chosen sort:
  - `newest` (the default): `c.approved_at desc, c.id desc`
  - `title`: `lower(c.data->'title'->>'value'), c.id`
  - `industry`: `lower(c.data->'industry'->>'value') nulls last, c.id`
- It returns `TOP` (20) rows from `offset (page-1)*20`.
- With bid text, ordering stays rank-first, exactly as today, with no paging,
  because the AI pick sees the top 20 only.
- Everything else is unchanged: `status='approved'`, `review_due > now()`,
  `VISIBLE`, and the existing substring filters.
- `kind` adds `c.basis = %s`, restricted to `delivered` or `engagement`.

**Offset paging, not keyset.** Three sort orders would need three keyset
cursors, and the library holds hundreds to a few thousand approved cases.
At that size `offset` costs microseconds, and a page number can be shown and
linked. This is noted as a shortcut in the code: "offset paging; switch to
keyset past ~50k approved cases".

**Rows.** A new `browse()` beside `results()` builds a view for every
candidate, using `view()` and `clean()` the same way `others` does today, so
anonymisation is identical. Each row adds:
- industry, region and the first 5 unsourced-filtered technology values, each
  passed through `clean()`
- `approved_at` as a date
- `basis`

The template reuses the existing result card's Preview and Download form (the
`generate` form block), as a compact row. No AI "Why it fits" appears unless
there is bid text.

**Filters you pick (facets).**
- `facets(user)` runs one query per facet (industry, region, tech) over the
  same visible, approved, in-date set. It returns `(value, count)` ordered by
  count desc, capped at 50 values.
  - Industry and region:
    `select c.data->'industry'->>'value' v, count(*) ... group by v`
  - Tech: `jsonb_array_elements(c.data->'tech_stack')` where
    `not (t->>'unsourced')::bool`.
- Values go through `clean()`, and a value that `clean()` withholds is
  dropped, so a protected name never appears as a filter option.
- The form fields become `<select>`s with an "Any" option, plus a
  Kind select (Delivered or Engagement) and a Sort select.
- Filtering stays substring matching (`position(lower(...))`), so the existing
  `test_filters` behaviour holds, and a picked value always matches itself.
- Facet counts cover every visible case. They are not narrowed by the other
  active filters (no cross-filtering). That's simpler, and enough to stop the
  guessing.

**Index.** One new partial index for the default sort:
`create index if not exists cases_approved_idx on cases (approved_at desc, id desc) where status = 'approved' and merged_into is null;`.

Facets scan the approved set once per facet. That is fine at a few thousand
cases. It is noted as a shortcut in the code: "facet group-by scans approved
cases; cache per minute if search gets slow".

### 2. "Latest documents" for reviewers and admins (`app/review.py`, `documents.html`)

**Route and visibility.** `GET /documents?page=`, behind `require("reviewer")`
(admins hold the reviewer role). It gets a "Latest documents" tab in
`base.html` next to "Review queue". It uses the same group rule as the review
list: `d.deleted_at is null and d.acl_groups && %s::text[]`. So it shows
exactly the documents whose cases this reviewer could review. Titles are
shown raw, as the review list already shows `d.title`.

**Query.**
- One query: documents left-joined to their case, with a lateral pick of the
  newest `extract` job for the document, joined to `sources` for the name.
- Ordered by `d.created_at desc, d.id desc`, 50 per page, offset paging (as
  above).
- `documents.created_at` is when a document first arrived. A new version
  updates the row in place, so a re-crawled document doesn't jump to the
  top. That's acceptable for "what arrived". It is noted in the template's
  help text.

**One status per row**, decided in Python from what the query already returns
(`kind`, the case's `status` and `retired_by`, the merge link, and the newest
extract job's `status`):

| Condition | Shown as |
|---|---|
| Newest extract job queued or running | Waiting for extraction |
| Newest extract job failed, no case | Extraction failed |
| Case `extracted` (or a merged parent awaiting review) | Case waiting for review, linked to `/review/{id}` |
| Case `approved` | Approved, linked |
| Case `rejected` with `retired_by = 'untick'` | Skipped: contract not marked executed |
| Case `rejected` otherwise | Rejected in review |
| No case and no job, `kind = 'contract'` | Skipped: contract not marked executed |
| No case, `kind in ('proposal','deck')` | Skipped: proposal or deck, not delivered work |
| No case, any other kind | Skipped: not a case document |

For admins, skipped contracts also link to `/admin/sources`, where the "All
contracts here are executed" box is.

**Where the skip reason comes from.** No new column is needed. `basis_for()`
in `app/ingest.py` returns `None` only for a contract that is not executed
and not flagged, or for a proposal or deck that doesn't describe delivered
work, or for "other". `documents.kind` plus "no case" is therefore exactly
the reason. A source flag or a re-triage changes the case or the job, and the
status follows.

**Files that never got a row** (wrong type, too large) are not listed. They
have no document, title or ACL to show. A note under the list says: "Files
of an unsupported type or over the size cap are counted per source on
Sources", with a link for admins. Adding rows for them would mean storing
names of files we refused to read, so it is rejected.

### 3. Home page panels (`landing.html`, `review.landing`)

`landing()` gains three panels. They are cheap queries, and none of them
calls a model:
- **Latest approved cases:** `browse()` page 1 with `newest`, limited to 5
  rows, with a link to `/search`. Every user sees it.
- **Your recent research:** `select id, query, status, retrieved_at from
  research where created_by=%s order by retrieved_at desc limit 5`. This is
  the same ownership rule as `research_view`, and the query is shown as
  stored (already scrubbed).
- **Waiting for review** (reviewers only): `count(*)` with `REVIEWABLE and
  QUEUE` (the review list's own filter), with a link to `/review`, plus the
  count of documents waiting for extraction.

The existing link cards stay below the panels.

## Alternatives rejected

- **Keyset paging.** It needs a cursor per sort order for no measurable gain
  at this size. Revisit past ~50k cases.
- **Exact-match facets.** They would change today's substring filter and its
  tests (`test_filters`). Substring matching on a picked value is
  equivalent for the user.
- **A `skip_reason` column, or document rows for refused files.** The reason
  is already determined by `kind` plus the case or job state. Rows for
  refused files would store names of files we never read.
- **Cross-filtered facet counts.** That's one more query per active filter
  for a nicety. It can be added later if asked.
- **A JS-driven instant filter.** The portal is server-rendered.
  `<select onchange=form.submit()>`-free GET forms work without JavaScript
  and keep accessibility simple. The form has a "Filter" button.

## Risks

- **Approval dates.** An approved case's `approved_at` can be null if it was
  approved before that column was set. It sorts last (`nulls last`), with
  `id desc` as the tiebreak.
- **Facets and anonymisation.** A facet value can contain a protected name, for
  example an industry string that quotes a client. `clean()` withholds it, so
  that value disappears from the options.
- **Visibility.** `VISIBLE` is a correlated subquery per case. With browse
  paging, it runs for the approved, in-date set on each page view. That is
  acceptable at the stated scale, and the plan measures it with
  `explain analyze` on the demo data.
- **Status edge cases.** A merged member's own case is `approved` or
  `extracted` but hidden (`merged_into`). The row links to the merged case
  instead. A document whose case was deleted and never re-extracted shows
  "Skipped: …" by kind, which is correct, because it has no case.

## Verification

- **`tests/test_search.py`**:
  - GET with no parameters lists approved, visible, in-date cases, newest
    first, without calling the model (patch `complete_json` to raise).
  - Page 2 holds the next 20.
  - `kind`, `sort` and the facet values filter as expected.
  - Facet counts are correct, and a protected value is not offered.
  - A POST with only filters calls no model.
  - A POST with bid text behaves exactly as before; the existing tests pass
    unchanged.
- **Latest documents, `tests/test_review.py` or a new
  `tests/test_documents.py`**:
  - Each row of the status table from a fixture: queued job, failed job,
    extracted, approved, untick-rejected, reviewer-rejected, unexecuted
    contract with no case, proposal with no case.
  - A document outside the reviewer's groups is not listed.
  - A user without the reviewer role gets a 403.
  - Admins get the Sources link.
- **Home page:** panels render for a user (latest cases and research only)
  and for a reviewer (with counts). Another user's research never appears.
- **`tests/test_ui.py`:** the new tab, and the select fields have labels.
- **By hand in the demo stack:**
  - `/search` opens on the 9 approved demo cases.
  - The industry options show counts.
  - `/documents` shows the 11 trial documents: 2 "Case waiting for review",
    6 "Skipped: contract not marked executed" and 3 "Skipped: proposal or
    deck…".
- **Full suite:** `docker compose build app && docker compose run --rm app pytest`.
