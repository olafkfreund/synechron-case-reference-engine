---
status: approved
issue: 198
spec: spec/2026-10-12-198-richer-sections.md
---

# Plan: Richer case studies: implementation, configuration and lessons learned

## Decisions (from the approved spec; the user decided these on 2026-10-12)

1. **Three new sections:** `implementation` ("Implementation approach"),
   `configuration` ("Configuration and integration") and `lessons_learned`
   ("Lessons learned"). The 80-word summary is unchanged.
2. **Sourced like other fields:** each section is a `Sourced[str]` scalar,
   extracted with a verbatim quote and checked by `sourced()`, exactly like
   `challenge` and `solution`.
3. **Reviewer-written sections:** a reviewer may write a section by hand by
   ticking "I wrote this: no document quote".
   - This sets `Sourced.by_reviewer = True`, a flag hidden from the LLM
     schema.
   - The flag is accepted only for the three new fields.
   - `check()` never marks a `by_reviewer` item unsourced, and approve keeps
     it.
   - It has no quote, so it can never vouch for numbers in the summary.
4. **No re-extraction of existing cases.** The new fields default to empty, so
   old data validates unchanged and approved cases stay approved.
5. **Engagement cases never have Lessons learned.** It is not extracted,
   editable, merged or rendered for them, the same rule as Outcomes.
6. **Re-extraction keeps reviewer-written text.**
   - If the old field is `by_reviewer` and the new extraction found nothing,
     the old field is copied over, with a note asking the reviewer to check it.
   - A newly quoted value replaces the reviewer's text, with a note.
   - Never for `lessons_learned` on an engagement.
7. **Merged cases:** each section is picked from one member, like the other
   scalars. `lessons_learned` is never merged, because a merged case is
   always an engagement.
8. **Outputs:**
   - Word, Markdown, PDF and the #197 preview show the sections through
     `section()` blocks.
   - Outputs do **not** mark reviewer-written text.
   - **PowerPoint is unchanged** until the marketing master arrives (#15).
9. **Anonymisation:** `protect()` already walks every output string, so it
   covers the new sections with no change.

## Steps

1. **`app/schema.py`: the fields.**
   - `Sourced` (`:55-61`): add
     `by_reviewer: SkipJsonSchema[bool] = False  # a reviewer's own text, no quote (#198); never from the LLM`.
   - `ReferenceCase` (`:96`, after `solution`): add `implementation`,
     `configuration` and `lessons_learned`, each `Sourced[str] = Sourced[str]()`.
   - `quotes()` (`:120-122`): add the three to `items`. A reviewer-written
     item has `source_quote == ""`, so the existing filter skips it.
   - `search_text()` (`:127-131`): add the three values after `solution`.
   - `_TEXTS` (`:171`): append the three names. `FIELDS` and `assemble()`
     follow with no other change.

   → verify:
   `pytest tests/test_schema.py::test_old_case_data_validates_without_new_sections tests/test_schema.py::test_by_reviewer_hidden_from_llm_schema tests/test_schema.py::test_reviewer_text_does_not_vouch_for_summary_numbers`
   (new tests), then `pytest tests/test_schema.py`.

   Traps: `llm_schema()` strips defaults, so check that `by_reviewer` is
   absent from `llm_schema()` *and* from `llm_schema(Extraction)`. Old JSON
   rows have no `by_reviewer` key, so the default must be False.

2. **`app/extract.py`: prompts, price stripping, checks.**
   - `SYSTEM` (`:11-20`): before "Only include facts", add "implementation
     (how it was delivered), configuration (how the platform was configured or
     integrated) and lessons_learned (what the document says worked or would
     be done differently): 1 to 3 sentences each, only when the document
     describes them."
   - `CONTRACT_SYSTEM` (`:22-32`):
     - add "implementation and configuration describe the planned approach";
     - change "Do not give outcome items, targets or SLAs" to "Do not give
       outcome or lessons_learned items, targets or SLAs".
   - `strip_commercial()` (`:62-63`): add the three names to the explicit
     tuple of `Sourced[str]` fields.
   - `check()` (`:84-110`):
     - at the top of `mark()`, add
       `if getattr(item, "by_reviewer", False): item.unsourced = False; return`
       (written as two lines). It goes before `text_for`, so a merged case
       never stamps a `document_id` on reviewer text.
     - add `case.implementation, case.configuration, case.lessons_learned` to
       the first loop (inferred, not literal).
   - `build()` (`:131`): next to `case.outcomes = []`, add
     `case.lessons_learned = Sourced[str]()`.

   → verify:
   `pytest tests/test_extract.py::test_new_sections_extracted_and_checked tests/test_extract.py::test_engagement_never_gets_lessons_learned tests/test_extract.py::test_strip_commercial_clears_new_sections tests/test_extract.py::test_check_leaves_reviewer_text_sourced`,
   then `pytest tests/test_extract.py`.

   Traps:
   - `strip_commercial` lists its fields **by name**. Missing one lets a price
     through in an engagement.
   - Tests stub `complete_json`, so no model and never real documents. Use
     made-up text and names only (the repo is public).

3. **`app/extract.py` `extract()` (`:146-180`): carry reviewer text over a
   re-extraction.**
   - After the two lock checks (`:170-171`) and before the upsert, read
     `select data from cases where document_id=%s for update`.
   - If a row exists, then for each of the three fields: when the old field
     has `by_reviewer`, a value, and is not (`lessons_learned` and
     `case.basis == "engagement"`):
     - if the new field is empty, copy the old field into `case` and append
       "<label>: your text from before the document changed was kept; check
       it still holds";
     - otherwise append "<label>: the document now states this; your earlier
       text was replaced".

     Do this through `case.needs_attention`.
   - Move `data = case.model_dump()` (`:163`) down to after this block.

   → verify:
   `pytest tests/test_extract.py::test_reextract_keeps_reviewer_written_section_with_note tests/test_extract.py::test_reextract_quote_replaces_reviewer_text tests/test_extract.py::test_reextract_engagement_drops_reviewer_lessons`.

   Traps:
   - **Lock order** is source, then documents, then cases. The cases row lock
     comes **after** the `documents ... for share` check, never before.
   - `build()` runs the model outside the lock. Read the old row only after
     it returns.
   - The carry-over must not make `summary_sourced()` fail. A reviewer item
     has no quote and adds no numbers, so it can't.

4. **`app/review.py`: fields, edit and approve.**
   - `SCALARS` (`:24-25`): add the three names after `solution`.
   - Add `WRITABLE = ("implementation", "configuration", "lessons_learned")`.
   - `MERGE_FIELDS` (`:273`): change to
     `(*(f for f in SCALARS if f != "lessons_learned"), "period")  # a merged case is an engagement (#198)`.
     `merge_preview`, `do_merge` and `remerge` follow.
   - `rows()` (`:93-111`):
     - `row()` adds `written=getattr(obj, "by_reviewer", False)` and
       `writable=path in WRITABLE`;
     - the SCALARS loop skips `lessons_learned` when
       `case.basis == "engagement"`.
   - `edit()` (`:167`):
     - new parameter `written: bool = Form(False)`;
     - `written` with a field not in WRITABLE: 400 "unknown field";
     - `lessons_learned` on an engagement: 400 "unknown field" (as outcomes,
       #139);
     - in the SCALARS branch, if `written`: require a non-empty value
       (ValueError gives 400), then set `obj.by_reviewer = True`,
       `obj.source_quote = ""`, `obj.document_id = None`, and skip the quote
       block below;
     - else if `name in WRITABLE`: set `obj.by_reviewer = False`.
   - `approve()` (`:242`): next to the engagement outcomes line, clear
     `case.lessons_learned` for an engagement.
     - The `for n in SCALARS` loop (`:236`) already keeps `by_reviewer` items,
       because check() left them sourced.

   → verify:
   `pytest tests/test_review.py::test_reviewer_writes_section_and_approve_keeps_it tests/test_review.py::test_written_flag_refused_on_other_fields tests/test_review.py::test_save_without_written_returns_to_quote_check tests/test_review.py::test_engagement_lessons_learned_edit_refused tests/test_review.py::test_merged_case_takes_sections_from_picked_member tests/test_review.py::test_merged_case_has_no_lessons_learned`,
   then `pytest tests/test_review.py`.

   Traps:
   - `unsourced` is never read from the form, and neither is `by_reviewer`.
     Only the `written` checkbox sets it, and only on WRITABLE fields.
   - The edit of an approved, in-date case still sends it back to
     `extracted` (#143). Keep that line.
   - The existing #190 re-merge tests use MERGE_FIELDS. Run them all.

5. **`app/templates/review_detail.html` (`:3-13`, the `field_row` macro).**
   - Badge: before `{% elif r.unsourced %}`, add
     `{% elif r.written %}<span class="badge ok">written by reviewer</span>`.
   - In the quote cell, for `r.writable`: add
     `<label class="check"><input type="checkbox" form="{{ f }}" name="written" value="true"{% if r.written %} checked{% endif %}> I wrote this: no document quote</label>`.

   No JavaScript.

   → verify:
   `pytest tests/test_review.py::test_reviewer_writes_section_and_approve_keeps_it`
   (it asserts the badge text on the page).

   Traps: autoescape is on. Never use `| safe`.

6. **`app/render.py` `section()` (`:89`).** Extend `blocks` with
   ```python
   ("Implementation approach", v(case.implementation)),
   ("Configuration and integration", v(case.configuration)),
   ("Lessons learned", None if case.basis == "engagement" else v(case.lessons_learned))
   ```
   - `to_pptx` picks blocks by heading ("Challenge", "Solution"), so it
     ignores the new ones. Don't touch it.
   - Word (`brand/reference.docx` loops `c.blocks`) and `MD` (`:299`) need no
     change.

   → verify:
   `pytest tests/test_render.py::test_new_sections_in_word_and_markdown tests/test_render.py::test_engagement_output_has_no_lessons_learned tests/test_render.py::test_pptx_unchanged_by_new_sections tests/test_render.py::test_new_section_naming_client_is_anonymised`,
   then `pytest tests/test_render.py`.

   Traps: `engagement` is computed further down in `section()`. Use
   `case.basis == "engagement"` inline, or move that line up.

7. **`docs/user-guide/reviewers.md`, "Sourced and unsourced fields" (`:26`).**
   - Step 2's badge list gains "written by reviewer".
   - Add a step: "For Implementation, Configuration or Lessons learned that
     the document doesn't describe, write the text, tick **I wrote this: no
     document quote** and Save. Approving keeps it; outputs show it without
     a mark."

   → verify: `pytest tests/test_user_guide.py`. The bold label must match the
   template text exactly.

## Tests

- After each step, run the node ids above. Use `check.sh` with node ids or
  file names only, never `-k` with spaces. It needs a clean tree.
- At the end, run the full suite (`pytest`): all green, including
  `test_user_guide` and the #190 re-merge tests.
- Optional, needs a local model: `scripts/eval_extraction.py` on the
  sanitised samples (#14), before and after, to check that outcome recall did
  not drop. Report counts only, and never print document content.

## Rollback

- Revert the PR.
- **The old code rejects new rows.** `_Model` has `extra="forbid"`
  (`app/schema.py:52`), so the old code refuses any row written by the new
  code. Those rows contain:
  - the three new keys;
  - `"by_reviewer": false` on **every** `Sourced` item, because `model_dump()`
    writes defaults.
- So before the old code starts, run a one-off clean-up. A short Python
  script (psycopg) walks each `cases.data`, deletes every `by_reviewer` key at
  any depth plus the three section keys, and writes the row back. A plain SQL
  `-` only removes top-level keys, so it is not enough.
- Reviewer-written text is lost. Export it first if wanted:
  `select id, data->'implementation', data->'configuration', data->'lessons_learned' from cases`.
