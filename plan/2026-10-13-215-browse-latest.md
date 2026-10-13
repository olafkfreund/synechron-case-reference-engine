---
status: draft
issue: 215
spec: spec/2026-10-13-215-browse-latest.md
---

# Plan: Open on what we have: latest cases, documents and their state

Branch `feat/215-browse-latest`, worktree `../refs-engine-215`. One PR.

## Approved decisions

- No new table, job, or triage or crawl change. Everything is read from
  existing rows, plus one new column, `documents.updated_at`.
- **Search, two entry points.**
  - `GET /search?industry=&region=&tech=&kind=&sort=&page=` browses.
  - `POST /search` with bid text ranks and runs the AI pick as today, with
    no paging.
  - A POST with an empty bid text renders the same page as the GET and never
    calls a model.
  - The bid text stays in POST bodies only. Filters may be in the URL.
- **Browse.** Same visibility as search (`status='approved'`,
  `review_due > now()`, `VISIBLE`). Sorts:
  - `newest` (default): `approved_at desc nulls last, id desc`
  - `title`
  - `industry` (`nulls last`)

  `kind` is `delivered` or `engagement` and adds `c.basis = %s`. Offset paging
  with `TOP` = 20 rows per page.
- **Rows** use `view()` and `clean()` exactly as `others` does. Each row adds
  industry, region, up to 5 sourced technology values (each through
  `clean()`), `approved_at` and `basis`. It has the existing Preview and
  Download form. There is no "Why it fits" without bid text.
- **Facets.** Industry, region and tech values with counts over every visible,
  approved, in-date case. They are not cross-filtered. They are ordered by
  count desc and capped at 50. A value that `clean()` withholds is dropped.
  The fields become `<select>`s with "Any", plus Kind and Sort selects.
  Filtering stays substring (`position(lower(...))`).
- **Indexes.**
  - `cases_approved_idx` on `cases (approved_at desc, id desc) where
    status='approved' and merged_into is null`
  - `documents_updated_idx` on `documents (updated_at desc, id desc)`
- **Shortcut comments in the code:**
  - "offset paging; switch to keyset past ~50k approved cases"
  - "facet group-by scans approved cases; cache per minute if search gets
    slow"
- **Latest documents.** `GET /documents?page=`, `require("reviewer")`, with a
  "Latest documents" tab after "Review queue".
  - Rows: `d.deleted_at is null and d.acl_groups && %s::text[]`, ordered by
    `d.updated_at desc, d.id desc`, 50 per page, offset paging.
  - Titles are shown raw, as the review list does.
  - "Updated <date>", plus "first seen <date>" when it differs from
    `created_at`.
  - One status per row, from the spec's table: waiting, extraction failed,
    waiting for review, approved, skipped (contract not marked executed,
    proposal or deck, not a case document), rejected in review.
  - Admins get a `/admin/sources` link on skipped contracts.
  - A merged member links to its merged case.
  - Files refused for type or size are not listed. A note points to the
    Sources counts.
- **"Latest" = last updated.**
  - `documents.updated_at timestamptz not null default now()`, added with
    `add column if not exists`.
  - The ingest upsert sets `updated_at = now()` in `do update set`.
  - `skip_unchanged` does not touch it.
- **Home page panels,** none of which calls a model:
  - Latest 5 approved cases (`browse()` page 1, `newest`), with a link to
    `/search`.
  - Your 5 most recent research runs (`created_by = user.sub`), with the
    stored scrubbed query.
  - For reviewers: the review queue count (`REVIEWABLE and QUEUE`) and the
    count of extract jobs that are queued or running.

  The link cards stay below the panels.

## Steps

1. **`sql/schema.sql`: the column and the indexes.** Append after `:200`:
   ```sql
   alter table documents add column if not exists updated_at timestamptz not null default now();
   create index if not exists documents_updated_idx on documents (updated_at desc, id desc);
   create index if not exists cases_approved_idx on cases (approved_at desc, id desc)
     where status = 'approved' and merged_into is null;
   ```
   In `app/ingest.py:117-119` (the `on conflict ... do update set`), add
   `updated_at=now(), ` before `deleted_at=null`.
   → verify: `grep -n "updated_at" sql/schema.sql app/ingest.py` shows three
   lines.
   Traps:
   - The schema file is re-run on every start (`DB_INIT_ON_START`,
     `app.migrate`), so every statement must be idempotent. These are.
   - Do not touch `skip_unchanged()` (`app/ingest.py:136+`): an unchanged
     re-crawl must not move a document up.

2. **`app/search.py`: browse, facets and routes.**
   - `search()` (`:49-82`): add the keyword args `kind=""`, `sort="newest"`,
     `page=1`.
     - When `kind in ("delivered", "engagement")`, append `c.basis = %s`.
     - Without bid text, choose `order by` from a fixed dict keyed by `sort`.
       An unknown key falls back to `newest`. Never interpolate the user's
       value.
     - Add `offset {(max(page, 1) - 1) * TOP}` with `page` clamped to
       1..1000.
     - With bid text, keep today's `order by rank desc, ...` and no offset.
     - Add the shortcut comment on the offset.
   - New `facets(user) -> dict[str, list[tuple[str, int]]]`: three queries
     over the same `where` as browse (approved, in-date, `VISIBLE`).
     - Industry and region group by `c.data->key->>'value'`.
     - Tech uses `jsonb_array_elements(c.data->'tech_stack') t where not
       coalesce((t->>'unsourced')::bool, false)`.
     - Each is `having v <> ''`, `order by count desc, v limit 50`.
     - Values go through `clean(v, clients, None)` and withheld ones are
       dropped. Read `clean()` at `:85` for what it returns when it withholds.
     - Add the facet shortcut comment.
   - New `browse(user, filters, sort, page) -> list[dict]`: `search(user, "",
     filters, kind, sort, page)` mapped through the same `view()` as
     `results()`. Lift `view` out of `results()` to module level with
     `clients` as a parameter; both callers then share it.
     - Each row adds `industry`, `region`, `tech` (the first 5 sourced, each
       cleaned), `approved_at`, `basis`, and `summary` (cleaned, as `others`
       has it).
   - Routes (`:148-160`):
     - `GET /search` reads `industry, region, tech, kind, sort, page` from
       query params (each cut to `MAX_FILTER_CHARS`) and renders `browse()`,
       `facets()` and `page`.
     - `POST /search` with empty `bid_text.strip()` does the same with the form
       filters. With bid text, it calls `results()` as today.

   → verify: `docker compose build app && docker compose run --rm app pytest tests/test_search.py`
   passes, so the existing tests are unchanged.
   Traps:
   - `search()` also feeds the AI pick (`results()`), so its default
     behaviour with bid text must not change. `test_filters` calls `search()`
     positionally with filters only, so keep the signature compatible.
   - `ids(text="", industry=...)` in `test_filters` expects the rank-0 order
     `c.id` today. With the `newest` default, the order changes. That test
     compares single-element lists and sets, so check it still passes and
     don't change its assertions.

3. **`app/templates/search.html`: two forms and the list.**
   - A `method="get"` filter form: labelled `<select>`s for Industry, Region,
     Technology (each "Any" plus `value (count)`), Kind (Any, Delivered case,
     Engagement) and Sort (Newest, Title, Industry), and a "Filter" button.
     The current choice is selected.
   - The existing `method="post"` bid form keeps the textarea and adds hidden
     inputs that carry the current filters, so a bid search applies them.
   - With no bid text, list `rows`. Each row is compact: title, label, basis
     badge, industry and region, technology chips, "Approved <date>", and the
     existing generate form block (Preview and Download Word, Other formats).
   - A pager with Previous and Next links that keep the query string, and a
     Next link only when a full page came back.
   - The empty state reads "No approved cases match these filters".
   - With bid text, the page is unchanged: `top` and `others`.

   → verify: `grep -n 'method="get"\|method="post"' app/templates/search.html`
   shows both forms.
   Traps:
   - `test_search_form_is_post_so_bid_text_stays_out_of_urls` asserts
     `method="post"` is present. Keep it on the bid form, and never put the
     textarea in the GET form.
   - Every `<select>` needs a `<label for>`, which `tests/test_ui.py` checks.

4. **`tests/test_search.py`: browse and facet tests.** Add:
   - `test_get_lists_visible_cases_newest_first_without_a_model`: patch
     `sr.complete_json` to raise. Approve 3 cases with set `approved_at`
     values. `GET /search` lists them newest first, and a hidden case
     (`test_hidden_cases` setup) is absent.
   - `test_paging`: approve 21 cases. Page 1 has 20, and page 2 has the 21st.
   - `test_kind_and_sort`: `kind=engagement` lists only engagement cases, and
     `sort=title` is alphabetical.
   - `test_facets_count_and_hide_protected`: industry counts are right, and an
     industry value containing a protected client name (the `reg` fixture) is
     not offered.
   - `test_post_with_only_filters_calls_no_model`: patch `complete_json` to
     raise. `POST /search` with `industry=` and empty `bid_text` returns 200
     and lists the case.

   → verify: `docker compose build app && docker compose run --rm app pytest tests/test_search.py`.
   Traps: approved cases need `review_due > now()`. Use the existing
   `approved` fixture and set `approved_at` with an `update` afterwards.

5. **`app/review.py` and `app/templates/documents.html`: latest documents.**
   - New route `GET /documents` (`require("reviewer")`), after `review_list`
     (`:121-135`), with `page: int = 1`, clamped to 1..1000. One query:
     ```sql
     select d.id, d.title, d.kind, d.created_at, d.updated_at, s.name,
            c.id, c.status, c.retired_by, c.merged_into, j.status
     from documents d join sources s on s.id = d.source_id
     left join cases c on c.document_id = d.id
     left join lateral (select status from jobs where kind = 'extract'
                        and payload->>'document_id' = d.id::text order by id desc limit 1) j on true
     where d.deleted_at is null and d.acl_groups && %s::text[]
     order by d.updated_at desc, d.id desc limit 50 offset %s
     ```
   - `doc_status(kind, case_status, retired_by, merged_into, job) -> (label,
     link)` is a small pure function that follows the spec table in order:
     the job state first, then the case, then "no case" by kind. A merged
     member links to `/review/{merged_into}`.
   - The template lists title, source, "Updated <date>" (plus "first seen"
     when the date differs from `created_at`), and the status, with its link
     if any. Admins get a "Sources" link on "Skipped: contract not marked
     executed". The note on refused files goes under the list, with a pager.
   - `base.html:10`: add `("/documents", "Latest documents")` to the reviewer
     tabs, after `("/review", "Review queue")`.

   → verify:
   `docker compose build app && docker compose run --rm app pytest tests/test_review.py tests/test_ui.py`
   still passes.
   Traps:
   - The job payload's `document_id` is stored as a number in JSON, so compare
     it as text (`->>`), as `app/sources.py:36` does.
   - Lock-free reads only: this is a GET.
   - The tab's `aria-current` rule matches `path.startswith(href ~ "/")`.
     `/documents` doesn't collide with `/review`.

6. **`tests/test_documents.py` (new): the status table and access.**
   - Unit-test `doc_status()` with one case per spec row:
     - queued job
     - failed job, no case
     - extracted
     - approved
     - rejected and untick
     - rejected in review
     - contract with no case
     - proposal with no case
     - other with no case
     - a merged member
   - Route tests:
     - A document outside the reviewer's groups isn't listed.
     - A plain user gets a 403.
     - An admin sees the Sources link.
     - Re-ingesting changed content moves a document to the top.
     - `skip_unchanged` does not move it.

   → verify: `docker compose build app && docker compose run --rm app pytest tests/test_documents.py`.
   Traps: build the fixtures with `tests/test_review.py`'s `make` and `DOCS`
   helpers, which `test_search.py` already imports, rather than new ones.

7. **`app/review.py` `landing()` (`:116-118`) and `landing.html`: home panels.**
   - `landing()` passes:
     - `latest = browse(user, {}, "newest", 1)[:5]`. Import inside the
       function, because `app.search` imports `app.review`, so a top-level
       import would be circular.
     - `research`: `select id, query, status, retrieved_at from research where
       created_by=%s order by retrieved_at desc limit 5`, with `user.sub`.
     - For reviewers, `queue`: `select count(*) from cases c where
       {REVIEWABLE} and {QUEUE}`.
     - For reviewers, `waiting`: `select count(*) from jobs where
       kind='extract' and status in ('queued','running')`.
   - `landing.html` shows three panels above the existing cards: "Latest
     approved cases" (title, label, date, linked to `/search`), "Your recent
     research" (query, status, date, linked to `/research/{id}`) and, for
     reviewers, "Waiting for review: N cases, M documents being extracted".
   - Add to `tests/test_documents.py`, or to `tests/test_ui.py` if its
     helpers fit: a user sees their own research and not another user's, and
     a reviewer sees the counts.

   → verify:
   `docker compose build app && docker compose run --rm app pytest tests/test_ui.py tests/test_documents.py`.
   Traps:
   - The waiting count is not limited by group, so it shows only a number,
     never titles. That matches "count only".
   - `research.created_by` is `user.sub`. Check `research_view` for the exact
     field it compares.

8. **Measure and check by hand on the demo stack.**
   - Rebuild with `scripts/demo.sh up`, which keeps the data. Then run
     `explain analyze` in `refsdemo-db-1` on the browse query (default sort,
     page 1, with `VISIBLE` for groups `{sales,delivery}`), on the industry
     facet query, and on the documents query.
   - Record the plan shapes and timings in the PR: each should be well under
     50 ms on the demo data. Expect a seq scan at this size; the indexes
     matter later.
   - Then check in the browser:
     - `/search` opens on the approved cases.
     - The selects show counts.
     - As `reviewer`, `/documents` shows the 11 trial documents: 2 "Case
       waiting for review", 6 "Skipped: contract not marked executed" and 3
       "Skipped: proposal or deck…".
     - The home page shows the panels.

   → verify: `docker compose build app && docker compose run --rm app pytest` passes in
   full.
   Traps: `demo.sh up` restarts the worker. Do it with no extract job
   running. The demo's `s3` container keeps the trial originals, so don't
   recreate it.

## Tests

```bash
docker compose build app && docker compose run --rm app pytest tests/test_search.py tests/test_documents.py tests/test_ui.py tests/test_review.py
docker compose build app && docker compose run --rm app pytest
```

Both should pass. No model is called by any browse, facet, documents or
landing test.

## Rollback

Revert the PR. The new column and indexes are harmless to the old code (it
never reads them), so they can stay. To remove them anyway:

```sql
drop index if exists cases_approved_idx;
drop index if exists documents_updated_idx;
alter table documents drop column if exists updated_at;
```
