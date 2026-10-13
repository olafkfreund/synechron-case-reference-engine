---
status: approved
issue: 235
spec: spec/2026-10-13-235-library-in-bids.md
---

# Plan: The Library works for you during a bid

Branch `feat/235-library-in-bids`, worktree `../refs-engine-235`. Two PRs.

## Approved decisions

- **Two PRs.** PR 1 covers suggestions, research from the Library and browse
  by topic, with no output changes. PR 2 adds library items to bid downloads.
- **Suggestions** come from `library.suggest()`, with no model call.
  - **Keywords:** words of 4 or more letters from the bid text, minus
    `SUGGEST_STOP`, plus the filter values, at most 30.
  - **Candidates:** the newest 300 items, matched as whole words against
    `_visible_text()` only.
  - **Score:** at least 2 keywords, or 1 when there are 2 or fewer keywords.
    The top `SUGGEST_MAX` = 5 are shown.
  - **Where:** POST bid search only. The bid text is never logged or stored.
- **Research from the Library** is a form posting to the existing `/research`.
  The research view pre-fills "Library topic" with the scrubbed query. There
  is no return flag.
- **Browse:**
  - topic chips (case-insensitive, `[withheld]` left out, top 20)
  - GET filters `topic`, `kind` and `due=1`
  - `library_items.created_by_name` holds the display name; older rows show
    "a colleague"
- **Downloads:**
  - `library_ids` on `/preview` and `/generate`, at most 3 and deduplicated.
    An unknown id gives 404, and every user may read every item.
  - Quote items join the cited industry section.
  - Brief items render as their own blocks, with their own sources, after the
    industry section. A withheld brief is dropped and counted.
  - PowerPoint gets each brief's title and takeaways only.
  - With no case, an acknowledgement is required.
  - The preview equals the download.
  - `generations.library_ids bigint[]` records which items went out.
- **Safety:** matching and rendering use the visible text only (`apply()`
  then `blocked()`), and library content appears only under the disclaimer,
  after the cases.

## Steps

### PR 1: suggestions, research from the Library, browse

1. **`sql/schema.sql`, after `library_items` (around :143):**
   `alter table library_items add column if not exists created_by_name text not null default '';`
   - **`app/library.py` `save()` (around :54):** store `user.name` in it.
   - → verify:
     `docker compose build app && docker compose run --rm app pytest tests/test_library.py`
   - Traps: idempotent DDL; existing rows get `''`.

2. **`app/library.py`, `suggest(bid_text, filters, clients)`:** add
   `SUGGEST_STOP`, `SUGGEST_MAX = 5` and `SUGGEST_CANDIDATES = 300`, per the
   spec. Reuse `_visible_text()` and the same row read as `index()`
   (topic, kind, body, age, stale). Return the dicts the spec lists.
   - → verify: the step 5 tests.
   - Traps:
     - match whole words (`\b`) on lowercased visible text
     - never put the bid text in a log or an exception message
     - don't call a model

3. **`app/search.py` `search_page` (around :211):** call `suggest()` when the
   bid text isn't blank, and pass `library=`. **`app/templates/search.html`:**
   add the "From the library" card above "Other matches", with a link, a kind
   badge, sources, age and a re-check badge. There are no checkboxes yet.
   - → verify: `pytest tests/test_search.py`
   - Traps:
     - the browse GET doesn't call `suggest()`
     - add the import inside the function if a circular import shows
     - the card is hidden when there are no suggestions

4. **Library page:**
   - **`app/library.py` `index()` (around :97):**
     - add the `topic`, `kind` and `due` query parameters
     - build `topics` from the shown topics (case-insensitive, top 20,
       without `[withheld]`)
     - pass `created_by_name`
   - **`library.html`:** add the chips (links that keep `q`), the kind and
     re-check filters, "Saved by <name or a colleague>", and the "Research a
     new topic" card (POST `/research`, the same field name as
     `research.html`).
   - **`library_item.html`:** saved-by.
   - **`research_view.html:20`:** `value="{{ query }}"` on the "Library topic"
     input.
   - → verify: `pytest tests/test_library.py tests/test_research*.py`
   - Traps:
     - check the field name `research.html` posts (read it), and don't add a
       route
     - filters cut at their max length
     - chips use the shown topic, never the stored one

5. **Tests:**
   - **`tests/test_library.py`:**
     - `suggest()`: a two-word match is found, a stoplist-only match isn't, a
       client protected later stops the match, the cap is 5, and no model is
       called (call recorder on `complete_json`)
     - chips and filters
     - saved-by, including "a colleague" for an old row
   - **`tests/test_search.py`:** the POST shows the card, and the GET doesn't.
   - **`tests/test_research_claims.py`:** the topic is pre-filled.
   - → verify: the full suite, alone.

6. **Timing.** In a test, insert 300 library items (half briefs, half quotes,
   realistic sizes) and time `suggest()` with a typical 150-word bid text.
   - Assert it takes under 150 ms; mark the test `slow` if the suite has the
     marker, otherwise keep it.
   - If it's slower, cache the visible text per item within the call, or lower
     `SUGGEST_CANDIDATES`. Record the number in the PR.
   - Traps: time only `suggest()`, not the setup.

### PR 2: library items in bid downloads

7. **`sql/schema.sql`, after `generations` (around :155):**
   `alter table generations add column if not exists library_ids bigint[];`
   - **`app/render.py` `generate()` (around :631, the insert at :644):** store
     the ids.
   - → verify: `pytest tests/test_render.py tests/test_hardening.py`
   - Traps: the audit table may be append-only for the app role, so this is
     an insert only and needs no grant change.

8. **`app/render.py` `build()` (around :553):** add `library_ids=()`.
   - Load each row. An unknown id gives 404, more than 3 give 400, and
     duplicates are dropped.
   - Quote items: `visible_claims(body["quotes"], clients)` appended to the
     merged claims, with the same `(url, quote)` dedupe.
   - Brief items: `brief_section(...)`, collected in `briefs`. `Withheld` is
     counted in the industry note.
   - Library items alone count like `research_id` for the acknowledgement and
     the empty check.
   - `preview()` and `generate()` take `library_ids: list[int] = Form([])` and
     pass them on.
   - → verify: `pytest tests/test_industry.py tests/test_render.py`
   - Traps:
     - every new string goes through the existing `apply()`/`blocked()` path
     - don't change the numbering of the existing industry section
     - case-study formats keep their one-case rule

9. **Rendering briefs:**
   - The Markdown template and `output_preview.html`: a brief loop after the
     industry section, with title, disclaimer, takeaways, themes and own
     sources.
   - **`scripts/make_reference_template.py`:** the same loop in
     `reference.docx` and `case_study.docx` (the shared industry function).
     Regenerate both.
   - **pptx:** each brief's title and takeaways on the industry slide or the
     fallback layout, capped like the other lines.
   - → verify: `pytest tests/test_industry.py tests/test_render.py tests/test_brief.py`
   - Traps:
     - regenerating can rewrite `brand/brief.docx` timestamps; restore it
       (`git restore brand/brief.docx`) and name it in the report
     - escape everything (`| md`, autoescape)

10. **Search card and preview:**
    - **`search.html`:** "Include" checkboxes (`library_ids`, using `form=` to
      the shared "Download all" form and to each case form).
    - **`output_preview.html`:** hidden `library_ids` on the download
      buttons, so download equals preview.
    - → verify: `pytest tests/test_search.py tests/test_industry.py`
    - Traps:
      - the bid text stays out of URLs
      - checkboxes appear only when there are suggestions

11. **Tests:**
    - Quote items appear in docx, md and pptx in the cited section.
    - A brief item renders with its own sources.
    - The preview equals the download.
    - A withheld brief is dropped and counted.
    - An unknown id gives 404, and 4 ids give 400.
    - Library items alone without the acknowledgement give 400.
    - The audit row has the `library_ids`.
    - → verify: the full suite, alone.

12. **By hand in the demo (the session):** save a library item, search with a
    matching bid text, include it, and download Word.

## Deviations

- Step 2: filter values match as whole phrases.
- Step 6:
  - `SUGGEST_CANDIDATES` is 150, not 300, to keep suggestions under budget
    (63 ms against 137-150 ms at 300).
  - A whole-word pre-screen on the stored text runs first, so an item whose
    only match would come from a label that `apply()` inserts is missed.
    That's rare, and noted in the code.
  - Topic chips use the first-seen casing.
- User-reported fixes (2026-10-13):
  - Save to library with no topic defaults to the scrubbed search or brief
    title, else "Untitled research".
  - The research page always sends `research_id`, so Save works for
    non-reviewers.
  - The action row is redesigned into Tick / Export / Write a brief / Save
    to library, then "Download all".
  - `/library/save` errors render an HTML message page (`message.html`)
    for browsers.
- The research page's topic box uses a placeholder ("defaults to the
  search"), not a prefilled value; the server applies the default when
  saving.
- PR 1 review fixes:
  - Tests that can fail: DEBUG logs, no `suggest()` call on GET, a
    whole-word substring case, protected topic chips, "Untitled research".
  - The timing limit is 0.5 s, with the time printed.
  - `kind` is validated.
  - POST filters are capped like GET.
  - Saved-by is "" when the display name equals the sub (never an email or
    UPN).
- Codex review (gpt-6-sol), P2, confirmed: a topic chip sent `due=` when
  "Needs re-check" was off, which gave a 422. It now sends `due=0`. A test
  follows the rendered chip link.
- Step 8: brief items travel inside the `industry` dict (`industry["briefs"]`)
  rather than as a separate return value, so the renderers need no new
  parameter. `build()` returns the ids actually rendered, and those are what
  `generations.library_ids` stores.
- Step 9:
  - An output with only briefs drops the empty "Industry context" heading
    (`show_head`). The withheld note always shows.
  - On the pptx slide, briefs come before quote lines, so a full set of
    quotes can't push them off. They share the `SLIDE_ITEMS` cap.
- Step 10: the "Include" checkboxes belong to the Download all form only. An
  HTML input has a single `form=` owner, so per-case downloads leave library
  items out, and the card says so. With library suggestions and no case, the
  form adds the acknowledgement checkbox.

## Tests

```bash
docker compose build app && docker compose run --rm app pytest   # one run at a time per worktree
```

## Rollback

- Revert the PR. The added columns (`created_by_name` and `library_ids`) are
  nullable or defaulted and ignored by the old code, so no data clean-up is
  needed.
- Revert PR 2 alone to keep the suggestions and browse.
