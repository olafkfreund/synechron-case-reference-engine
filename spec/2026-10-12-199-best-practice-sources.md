---
status: approved
issue: 199
intent: intent/2026-10-12-199-best-practice-sources.md
---

# Spec: Case studies: cite the public best-practice sources used

## Decisions

The user decided (2026-10-12):

- A reviewer attaches research to the case and picks the claims they accept.
- Every output of that case then lists those sources: the verbatim quote,
  publisher, URL, retrieval date and fixed phrase, under a heading like
  "Best practice and public sources", with the existing third-party
  disclaimer.
- No new search per download.
- **Heading** (decided 2026-10-12, "approve all with your recommendations"):
  "Best practice and public sources" only when the output includes sources
  attached to a case. An output built only from per-download research keeps
  "Industry context (public sources)". Both are constants, so #23 can change
  them.
- **Approval** (decided 2026-10-12): a reviewer attaching or removing
  sources does not reopen an approved case.

## Design

### 1. Storage: a copy on the case, outside `data`

`sql/schema.sql`, after the #190 lines (`:92-93`):

```sql
-- public claims a reviewer accepted for this case (#199): copied, so they outlive the research row;
-- never in data or search_text (research text must not reach search)
alter table cases add column if not exists public_sources jsonb not null default '[]';
```

- **What each item holds:** `{quote, publisher, url, retrieved_at, type,
  research_id, attached_by, attached_at}`, copied from one
  `research.results.claims` item.
- **What is not copied:** the AI-written `statement` is dropped. Outputs
  never show it (`industry_section` uses the quote and the fixed phrase).
- **Why a copy, not a link:**
  - research rows are private to their creator (`created_by`), and
    `case_id` is `on delete set null`;
  - a copy survives the research being deleted, and needs no ownership check
    at render time;
  - `research_id` is kept for the audit trail only.
- **Why outside `data`:**
  - re-extraction overwrites `data` (the upsert in `app/extract.py:163-168`),
    which would drop the reviewer's choice;
  - `test_research_text_never_reaches_cases` (`tests/test_industry.py:211`)
    stays true: `data` and `search_text` never hold public text, so search
    never matches it.
- **Cap:** at most 20 items per case. One attach that would go over the cap
  is refused (400).

### 2. Attach from the research page

The research page (`templates/research_view.html`) is where the claims
already are. When the research was started from a case (`case_id`) and the
viewer is a reviewer who can see that case (`REVIEWABLE`), the page shows:

- a checkbox per claim;
- the button **Attach to the case as public sources**, which posts to
  `POST /review/{cid}/sources` with `research_id`, the chosen claim indexes
  (`n`) and the case version token (`s`, see below).
- **No JavaScript:** a plain form.

`POST /review/{cid}/sources` in `app/review.py` (role `reviewer`):

1. **Lock the case.** `select ... where c.id = %s and {REVIEWABLE} for update
   of c`, or 404. It takes no source or document lock, so the lock order is
   unchanged.
2. **Check the research.** It must exist, belong to the user
   (`created_by = user.sub`), be `status = 'done'`, and have
   `case_id = cid`. Otherwise 404. Research that wasn't started from this
   case can't be attached, which keeps the link visible on both pages.
3. **Copy the chosen claims from the stored row**, by index, never from the
   form. Each claim passes `visible_claims` first (`app/research.py:168`), so
   one that names a protected client is not attached.
4. **Deduplicate** by `(url, quote)`, append, and apply the cap.
5. **Stale form:** if `md5(public_sources::text) != s`, return 409 "case
   changed; reload the page".
6. **Redirect** with a 303 to `/review/{cid}`.

### 3. Show and remove them on the review page

- **List:** `review_detail` reads `public_sources` and shows a card
  "Public sources (not delivery evidence)" in
  `templates/review_detail.html`. Each item shows the quote, the publisher
  linked to its URL, the retrieval date and the type badge, as
  `research_view.html` does. The list is filtered through
  `visible_claims` too, so a client protected later is hidden here as well.
- **Remove:** `POST /review/{cid}/sources/remove` with the item index and
  `s`. It takes the same lock and the same 409 rule.
- **Status and approval are not touched.** Attaching or removing doesn't
  reopen an approved case, and approval doesn't gate the sources:
  - they are public third-party text, never delivery evidence;
  - they reach an output only of an approved case, because `/generate`
    already requires `status = 'approved'`;
  - who attached each item, and when, is stored on the item.
- **When it's allowed:** while the case is open for review, and also on an
  approved case. A reviewer who sees the case can curate its sources at any
  time.

### 4. Output: one section, as today

In `generate` (`app/render.py:352`), the cases' attached sources and the
per-download research claims (`research_id`) are combined:

- the attached sources of the chosen cases come first, then the research
  claims;
- duplicates by `(url, quote)` are dropped;
- the result goes through the existing `industry_section(claims, clients)`
  (`:48`).

So the Word, PowerPoint, PDF and Markdown templates are unchanged. They
already render one `industry` section with the quote, phrase, publisher,
URL, date, disclaimer and omitted-claims note.

- **Re-checked at render time:** `industry_section` already runs `apply()`
  and then `blocked()` per claim. A client protected after the attach drops
  that claim and counts it in the note.
- **Heading:** `INDUSTRY_HEADING` (`:40`) stays
  `"Industry context (public sources)"`. A new constant
  `SOURCES_HEADING = "Best practice and public sources"` is used when at
  least one attached source is in the combined list, even if some of them
  are later dropped by `blocked()`. `industry_section` gains a keyword
  `heading=INDUSTRY_HEADING`, and `generate` passes `SOURCES_HEADING` when
  attached sources are present. #23 can change either constant, along with
  `PHRASES`. The disclaimer constant stays as it is.
- **The acknowledgement:** unchanged. It is needed only when an output has
  no case (`:357`). An output with a case and attached sources needs none,
  just as a case with `research_id` needs none today.
- **Audit:** `generations` gains no column. `case_ids` already says which
  cases went out. The exact sources at that time are not snapshotted (see
  Risks).
- **Search card:** unchanged. A "Download Word" from the card now carries
  the attached sources. The preview (#197) shows them, because it uses the
  same sections.

### 5. Merged cases

- **`do_merge`** (`app/review.py:389`) sets the new merged row's
  `public_sources` to the union of its members' items, deduplicated by
  `(url, quote)` and capped at 20.
- **`remerge`** keeps the merged row's own list. It rebuilds `data` only.
- **`unmerge`** leaves each member's own list as it was.

### 6. Docs

- `docs/user-guide/reviewers.md`: a new section "Attach public sources":
  - run "Research this (public sources)" from the case;
  - tick the claims you accept and press **Attach to the case as public
    sources**;
  - remove them on the review page;
  - they appear in every download of the case under "Best practice and
    public sources", marked as third-party statements.
- `docs/user-guide/bid-team.md` ("Download with industry context", `:103`):
  the note that a case can carry attached sources, which change the heading
  to "Best practice and public sources"; research-only downloads keep
  "Industry context (public sources)".
- `test_user_guide` checks the button labels.

## Alternatives rejected

- **Link to the research row instead of copying.** Research is private to
  its creator and can be deleted. Every render would need an ownership
  exception and a missing-row path.
- **Put the sources in `cases.data`** (a `ReferenceCase` field).
  Re-extraction overwrites `data`, and public text would leak into
  `search_text`, against `test_research_text_never_reaches_cases`.
- **A separate section per case in the output.** That needs changes to the
  Word template, the PowerPoint master and Markdown. One combined section
  reuses `industry_section` and every template as they are. With up to three
  cases, which case a source came from is lost, which is acceptable for
  public background.
- **Gate the sources by case approval** (reopen on attach). That sends an
  approved case back to the queue for public text that is never delivery
  evidence. The copy records who attached each item and when.
- **Let the form send the quote and URL.** A crafted form could attach text
  no search ever returned. The server copies from the stored research row
  by index.

## Risks

- **No audit snapshot.** `generations` doesn't record which attached sources
  were in a given download. If a reviewer later removes one, an audit can't
  tell from the row alone that it went out. *Mitigation:* the item carries
  `research_id`, `attached_by` and `attached_at`. A follow-up could store
  the source count or the md5 of the list on `generations` if the audit
  needs it.
- **Slides cut at 5** (`SLIDE_ITEMS`). With up to 20 attached sources, the
  PowerPoint slide shows only the first 5. Attached sources come first, so
  those win over per-download research.
- **Live behaviour is untested until #19.** Without a Brave key, no real
  research exists locally or in staging. The tests use stored claims rows.
- **Hosts:** none, beyond the schema change. It is additive and idempotent,
  and runs in the migrate task on the next release.

## Verification

New tests in `tests/test_industry.py`, using the existing `rows` and
`approved` fixtures with stored claims and no Brave call:

- `test_reviewer_attaches_chosen_claims_to_the_case`: only the chosen
  indexes are copied, with no `statement`, and `research_id` and
  `attached_by` are set.
- `test_attach_refuses_research_not_yours_not_done_or_from_another_case`:
  each case returns 404, and a plain user (not a reviewer) gets 403.
- `test_attach_copies_from_the_row_not_the_form`: an extra form field with
  a quote is ignored.
- `test_attach_skips_a_claim_naming_a_protected_client`.
- `test_attach_dedupes_and_caps_at_20`.
- `test_attach_and_remove_need_the_current_list`: a stale `s` returns 409.
- `test_attached_sources_appear_in_every_format`: docx, pptx, md and the
  section dict carry the quote, the publisher, the URL and the new heading,
  with no `research_id` on the request.
- `test_attached_and_download_research_are_merged_without_duplicates`.
- `test_research_only_output_keeps_the_industry_heading`: an output with
  `research_id` and no attached sources is headed "Industry context (public
  sources)".
- `test_client_protected_after_attach_is_dropped_at_render`: the omitted
  note shows.
- `test_attached_sources_survive_reextraction_and_research_deletion`.
- `test_attach_does_not_reopen_an_approved_case`.
- `test_merge_unions_member_sources`, plus `remerge` and `unmerge` keeping
  the lists.
- The existing `test_research_text_never_reaches_cases` is extended: after
  an attach, `data` and `search_text` still don't contain the quote, and a
  search for the quote finds nothing.

Also:

- `tests/test_user_guide.py` passes with the new labels.
- The full suite passes.
- Manually, once #19 lands: run research from a demo case, attach two
  claims, download Word and check the section.

## Open questions

None. The heading question was decided on 2026-10-12 (see Decisions).
