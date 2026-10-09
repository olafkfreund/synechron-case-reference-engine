---
status: draft
issue: 55
intent: intent/2026-10-09-55-merge-engagements.md
---

# Spec: Merge related contracts into one engagement reference

Approved answers from the intent:

- a reviewer merges by hand;
- the same client is the only hard rule;
- only users who can see every member see the merged reference;
- the merged reference needs a fresh approval;
- it is built from fields that are already checked, with no model call;
- un-merge is supported;
- a member that changes reopens the merged reference, and a deleted member
  hides it.

## Design

### 1. Data model: a merged case is a `cases` row, and its members point to it

Additive and idempotent, in `sql/schema.sql` after the `basis` block
(lines 72-75), following the same `drop constraint if exists` / `add
constraint` pattern:

```sql
-- a merged engagement (#55) has no document of its own; its member cases point to it
alter table cases alter column document_id drop not null;
alter table cases add column if not exists merged_into bigint references cases(id) on delete set null;
alter table cases add column if not exists member_count int;
alter table cases drop constraint if exists cases_merge_check;
alter table cases add constraint cases_merge_check check (
  (document_id is null) = (member_count is not null)        -- merged rows, and only they, count members
  and (member_count is null or member_count >= 2)
  and (merged_into is null or document_id is not null));     -- a member is a one-document case
create index if not exists cases_merged_into_idx on cases (merged_into);
```

- **Merged case:** `document_id` is null, `member_count` is N, and `basis` is
  `engagement`. `unique (document_id)` (line 50) allows many nulls, so
  `extract.py`'s `on conflict (document_id)` upsert (lines 133-138) still
  works for single-document cases.
- **Member:** an ordinary single-document case with `merged_into` set. It
  keeps its own `data` and status, so un-merge loses nothing and a member's
  re-extraction keeps working unchanged.
- **Every existing case is valid as-is:** `document_id` is set and both new
  columns are null. No backfill is needed.
- **Nesting is impossible.** A merged row has no document, so it can't be a
  member. The `merged_into` link is one level only.
- `member_count` exists so that visibility fails closed. If a member's
  document is ever hard-deleted (`documents → cases on delete cascade`, for
  example when a source is deleted in SQL), fewer members remain than were
  counted, and the merged case disappears everywhere.

### 2. The "can see every member" check

Today `ACL = "d.deleted_at is null and d.acl_groups && %s::text[]"`
(`app/review.py:23`) only works next to `join documents d on d.id =
c.document_id`. That join drops merged rows (they have no document). Its
any-group overlap also only looks at one document.

It is replaced in `app/review.py` by two predicates over `cases c` that take
one parameter, as today:

```python
# need-to-know: every document behind a case must be live and open to the user; a member is
# never shown on its own while it is merged (its content is inside the merged case)
VISIBLE = ("c.merged_into is null and coalesce(c.member_count, 1) = (select count(*) from cases m "
           "join documents md on md.id = m.document_id where (m.id = c.id or m.merged_into = c.id) "
           "and md.deleted_at is null and md.acl_groups && %s::text[])")
# review only: a withdrawn member's case stays visible to reviewers so they can un-merge it
REVIEWABLE = VISIBLE.replace("md.deleted_at is null and ", "")
```

- **Single-document case:** `m.id = c.id` counts its own document, so the
  check equals today's `ACL`.
- **Merged case:** its own row joins no document, so only the members count.
  All N members must be visible to the user.

Every query that uses `ACL` today switches to `VISIBLE`. Where the query only
joined `documents` for the ACL, that join is removed:

| Where | Today | Becomes |
| ----- | ----- | ------- |
| `app/review.py:38-47` `load()` (edit, approve, reject) | `join documents d`, `{ACL}`, `d.text` | `{VISIBLE}`; texts as in section 3 |
| `app/review.py:94-102` `review_list` (open and due soon) | `{ACL}`, `d.title` | `{REVIEWABLE}`; `left join documents d`; a merged row shows "N contracts" |
| `app/review.py:113-116` `review_detail` | `{ACL}`, `d.title, d.external_id, s.name` | `{REVIEWABLE}`; the member list (section 4) |
| `app/search.py:70-73` `search` | `join documents d`, `{ACL}` | `{VISIBLE}`, join removed |
| `app/render.py:373-377` `generate` | `join documents d`, `{ACL}` | `{VISIBLE}`, join removed |
| `app/research.py:104-109` `visible_case` (from-case and send, lines 127 and 144) | `join documents d`, `{ACL}` | `{VISIBLE}`, join removed |

- `ACL` is deleted, so a caller missed in this list fails at import instead of
  quietly using the old check.
- `REVIEWABLE` is used only to **list and show** cases. `load()` uses
  `VISIBLE`, so a withdrawn member blocks editing and approval of the merged
  case. Un-merge is still allowed (section 5).
- `app/main.py:74` (the group list) and `app/sources.py:90` (copying a
  source's groups to its documents) are unchanged. A source ACL change
  applies to merged cases straight away, because `VISIBLE` reads the current
  document groups.

### 3. Combining fields, each quote checked against its own document

- **`app/schema.py`:** `Sourced`, `Outcome` and `Period` (lines 50-68) gain
  `document_id: SkipJsonSchema[int | None] = None`. It is hidden from the LLM
  schema, like `unsourced`. Single-document cases leave it `None`, meaning
  "the case's own document", so no stored JSON changes.
- **`app/extract.py`:** `check(case, text)` (lines 78-102) accepts
  `text: str | dict[int, str]`. With a dict, `mark()` checks each item
  against `text.get(item.document_id)`. A missing id (a removed member, or
  `None` in a merged case) makes the item **unsourced**, so it fails closed.
  The string form is unchanged for `build()` and single-document review.
- **`load()` for a merged case** returns `{document_id: text[:MAX_CHARS]}`
  for its members, selected with the same `VISIBLE` row lock.
- **`merge(cases)`** builds the record in pure Python, with no model call:
  - **Text fields** (`SCALARS`, `app/review.py:16-17`, apart from
    `duration_months` and `team_size`): a copy of one member's item, with
    `document_id` set to that member's document. The reviewer picks which
    member for each field where the values differ (section 4). By default it
    is the case the merge started from.
  - **`duration_months`, `team_size`, `period`:** each is taken **whole**
    from one chosen member, never added up or spanned across members. A
    computed total is a new number with no quote behind it (the same rule as
    #52's "digits are never edited").
  - **`capabilities`, `tech_stack`:** the union of all members, de-duplicated
    on the case-folded value. The first occurrence wins, so its quote and
    `document_id` stay.
  - **`outcomes`:** always `[]` (basis `engagement`, as in #52).
  - **`summary`:** `""`. `needs_attention` gets "merged from N contracts:
    write a summary". The reviewer writes it through the existing summary
    edit, and `fix_summary()` (`app/review.py:53-58`) checks its numbers
    against all member quotes.
  - **`organisations`:** the union. It feeds the "not in registry" notes
    (`app/review.py:121-123`).
  - **`basis`:** `"engagement"`. **`basis_reason`:** `"merged: "` plus the
    members' distinct reasons.
  - Then `check(case, texts)`, so every item is re-verified against its own
    document.
- **Merge rules,** all checked server-side under `for update` of every member
  row:
  - at least 2 members;
  - every member is `VISIBLE` to the reviewer;
  - every member has basis `engagement`, `merged_into` null, a non-null
    `document_id`, and status other than `rejected`;
  - each member's `VERSION` matches the version on the form.
  - **Same client:** `anonymise.resolve(client_mention, registry)`
    (`app/anonymise.py:83-86`) gives the **same non-null** id for every
    member. `cases.client_id` is only set at approval (`app/review.py:189`),
    so it can't be used for extracted members.
  - The new row is inserted with `status 'extracted'`, `document_id null`,
    `member_count N` and `basis 'engagement'`, and the members get
    `merged_into` set.

### 4. Review UI (server-rendered forms, no JavaScript)

- **`review_detail.html`, single engagement case:** a "Merge with other
  engagements for this client" form, with a checkbox for each other
  candidate. Candidates are visible, unmerged, non-rejected engagements whose
  mention resolves to the same client. They are listed by title, document and
  period, and only shown when the case's client resolves.
  - It posts the ids and versions to `POST /review/merge/preview`. That page
    shows a table with one row per field where the members' values differ,
    and one radio button per member (the first is checked by default). It
    also lists the members' documents.
  - The preview then posts to `POST /review/merge`, which checks again
    (section 3), inserts the case, and redirects to `/review/{new id}`.
  - The candidate list only offers same-client cases. The client rule is
    still enforced in `POST /review/merge`, because a form can be forged.
- **`review_detail.html`, merged case:**
  - The single "Document:" line (line 4) becomes a members table: title,
    source, `external_id`, basis reason, status, and "withdrawn" when
    `deleted_at` is set.
  - Each field row also shows the title of its `document_id`'s document next
    to the quote, so the reviewer can see which contract backs it.
  - Approve stays. Reject is replaced by **Un-merge**, because rejecting
    would leave the members hidden.
- **`review_list.html`:** a merged row shows "N contracts" in the
  source-document column.
- **Approve on a merged case** (`app/review.py:168-192`) keeps the existing
  logic (unsourced items are emptied, the client is resolved, and review is
  due in 12 months). It also refuses with 409 while any member is `rejected`
  ("a member contract is no longer an engagement: un-merge").

### 5. Un-merge

`POST /review/{cid}/unmerge`, with `v`, on a merged case that is `REVIEWABLE`
for the reviewer. It does not require the case to be open, so a reviewer can
also un-merge an approved merged case.

- The members get `merged_into = null` and `status = 'extracted'`, with
  `approved_by`, `approved_at` and `review_due` cleared. Each one goes back to
  the queue for a fresh approval.
- The merged row gets `status = 'rejected'`. It is kept, not deleted:
  - `generations.case_ids` (the audit trail) still points at a row that
    exists;
  - `research.case_id` stays valid;
  - with no members left, its `member_count` never matches again, so it is
    invisible everywhere.
- To remove one member, un-merge and then merge the rest again.
  There is no separate "remove member" action in v1.

### 6. A member changes or is deleted; interaction with #64

- **Changed document:** `app/ingest.py:99-102` sets the member's status, then
  queues re-extraction. That is moved into one helper,
  `reopen_or_retire(conn, doc_id, basis)`, which also runs:

  ```sql
  update cases set status = 'extracted' where status = 'approved'
    and id = (select merged_into from cases where document_id = %s)
  ```

  - The merged case leaves search and returns to the queue.
  - Re-extraction updates only the member's own row. The merged copy is
    re-checked against the new text at edit and approval, so quotes that are
    no longer in the document become unsourced and are emptied at approval.
  - To pick up new fields, the reviewer un-merges and merges again.
- **Retired member** (`basis` is `None`: the contract is no longer executed,
  or is no longer a contract): the same helper marks the member `rejected`
  and reopens the merged case. Approval then refuses until the reviewer
  un-merges (section 4).
- **#64 ("contracts executed" flag changes):** #64 must retire and reopen
  through `reopen_or_retire()`, not with its own `update cases`. Turning the
  flag off then reopens any merged case with a member marked "source marked
  executed", and blocks its approval. #64 is not built yet, so this spec
  states the rule and the plan adds a note to #64.
- **Withdrawn member** (`deleted_at`, from `app/crawl.py:76-78` or
  `196-198`): `VISIBLE` hides the merged case from search, outputs, research
  and editing straight away, with no hook in the crawlers. `REVIEWABLE` keeps
  it on the review page with "withdrawn" so it can be un-merged. If the
  document comes back (`deleted_at = null`, `app/ingest.py:71-72`), the case
  is visible again unchanged.
- **Hard-deleted member:** the `member_count` mismatch hides the merged case
  everywhere (section 1).

## Alternatives rejected

- **A link table `case_documents(case_id, document_id)` for every case,** with
  `cases.document_id` dropped. It is the most general, but it needs a backfill
  and a rewrite of `extract.py`'s upsert, `ingest.py`'s status update and the
  #52 tests, for a feature most cases never use. The parent link adds two
  nullable columns and leaves every existing row valid.
- **A separate `engagements` table** next to `cases`. Search, render,
  research, review and the audit (`generations.case_ids`) would all have to
  query two tables and two id spaces. A merged case that is a `cases` row
  works in all of them for free once the ACL check is fixed.
- **Copying members' document ids into an array on the merged row.** It
  duplicates the `merged_into` link, so the two can drift apart.
- **Re-extracting from the joined member texts.** It means a new model call,
  data-class handling across sources, and new unchecked output. The approved
  answer is to combine already-checked fields.
- **Checking quotes against the concatenated member texts.** A quote could
  match the wrong contract, or span two documents, and the reviewer couldn't
  see which document backs a field.
- **Visible if the user can see any member (union):** this widens
  need-to-know. It is ruled out by the intent.
- **Deleting the merged row on un-merge:** it orphans `generations.case_ids`
  in the audit trail.
- **Summing durations or spanning periods across members:** it creates
  numbers that no quote backs.

## Risks

- **A query left on the old single-document check widens or breaks access.**
  Mitigated by deleting `ACL` (import fails), and by one access test per
  query in the section 2 table.
- **The correlated count in `VISIBLE` runs once per case.** It is fine at
  today's volumes (hundreds of cases), and `cases_merged_into_idx` covers the
  member lookup. Search is limited to 20 results.
- **Clients missing from the registry can't be merged.** The reviewer adds
  the client at `/admin/clients` first. This is deliberate, because the client
  is the only hard rule.
- **A stale merged copy after a member changes.** The member's new fields
  don't flow into the merged case until it is un-merged and merged again.
  Quotes that disappeared fail the check, so stale content can't be approved
  silently.
- **#64 implemented without the helper** would leave a merged case approvable
  after a member is retired. Mitigated by the note on #64 and a test that
  retires a member through `reopen_or_retire()`.
- **Schema:** `drop not null` on `cases.document_id` is idempotent. The new
  check constraint holds for every existing row, so the web and worker
  start-up migration (line 1-2 advisory lock) applies cleanly on the live
  dev database. Rollback has to delete merged rows before `not null` can be
  restored.

## Verification

`docker compose build app && docker compose run --rm app pytest` is green.
All test data is made up (no real client names or documents).

- **`tests/test_db.py`:**
  - the migration runs twice without error;
  - the constraint rejects a merged row with one member, a member that is
    itself merged, and a row with `document_id` and `member_count` both null.
- **`tests/test_extract.py`:** `check()` with a dict:
  - each item is checked against its own document;
  - an item whose quote exists only in another member's text is unsourced;
  - a missing `document_id` is unsourced;
  - the string form is unchanged.
- **`tests/test_schema.py`:** `llm_schema()` has no `document_id`.
- **`tests/test_review.py`:**
  - merge is refused for different or unregistered clients, a delivered
    member, a rejected member, a member already merged, a stale version, or a
    member the reviewer can't see;
  - the merged record: union of lists with quotes and document ids kept,
    chosen fields as picked, no outcomes, empty summary with a note, nothing
    summed;
  - the members table and per-field document shown; the radio preview
    renders;
  - approve refuses while a member is rejected or withdrawn;
  - un-merge restores the members as `extracted`, and the merged row is
    `rejected` and invisible.
- **Need-to-know, one test per query in the section 2 table** (review list,
  detail, load, search, generate, research from-case and send):
  - members in groups A and B: a user with only A sees neither the merged
    case nor the members;
  - a user with A and B sees only the merged case;
  - a withdrawn member hides the merged case from search, generate and
    research, but not from the review list;
  - a hard-deleted member hides it everywhere.
- **`tests/test_ingest.py`:**
  - a changed member reopens an approved merged case;
  - a retired member (no basis) is `rejected` and the merged case reopens;
  - both go through `reopen_or_retire()`.
- **`tests/test_render.py`:** a merged engagement renders with the fixed
  engagement line and no Outcomes in docx, pptx and md.
- **Manual check** in the running dev stack (no `up` or `down`): merge two
  made-up engagement cases, approve, find the result in search, generate a
  docx, then un-merge.
