---
status: draft
issue: 198
intent: intent/2026-10-12-198-richer-sections.md
---

# Spec: Richer case studies: implementation, configuration and lessons learned

## Decisions

Decided by the user, 2026-10-12:

1. Three new sections: **Implementation approach**, **Configuration and
   integration**, **Lessons learned**. The 80-word summary stays as it is;
   the detail goes in the new sections.
2. They are extracted from the case's own documents with a source quote,
   like every other field. When a document says nothing, a reviewer **may
   write the section by hand**. It is then marked reviewer-written, with no
   document quote.
3. Existing cases are not re-extracted. The sections are filled on the next
   document change, or by a reviewer. Approved cases stay approved.
4. A case on an engagement basis (contracted scope, not delivered) never has
   a Lessons learned section. That would claim a result, as Outcomes would.
5. Client outputs do **not** mark reviewer-written sections. Approval is the
   reviewer's attestation.
6. PowerPoint stays unchanged until the marketing master arrives (#15). There
   is no interim slide.

## Design

### 1. Three new scalar fields

`ReferenceCase` (`app/schema.py:89`) gains three fields:

```python
implementation: Sourced[str] = Sourced[str]()
configuration: Sourced[str] = Sourced[str]()
lessons_learned: Sourced[str] = Sourced[str]()
```

- **Old data stays valid.** The fields default to empty, so
  `ReferenceCase.model_validate(old_data)` still works. No migration, and no
  change to approved cases.
- **They are plain scalars, like `challenge` and `solution`.** Adding them to
  `SCALARS` (`app/review.py:24`) brings in the rest for free:
  - the review rows (`rows()`) and the edit route (`edit()`, `:167`);
  - approve dropping the field when it is unsourced (`approve()`, `:236`);
  - `MERGE_FIELDS`, so a merged case picks each section from one member
    (`combine()`, `:283`).
- **The schema methods include them:**
  - `quotes()`, so their quotes can back numbers in the summary;
  - `search_text()`, so search finds words in them.

### 2. Extraction

- **Field names.** `_TEXTS` (`app/schema.py:171`) gains `implementation`,
  `configuration` and `lessons_learned`. `FIELDS`, and with it the field list
  in both prompts, follows. `assemble()` already takes the first value for
  each name in `_TEXTS`.
- **`SYSTEM` (`app/extract.py:11`)** gains one sentence: "implementation (how
  it was delivered), configuration (how the platform was configured or
  integrated) and lessons_learned (what the document says worked or would be
  done differently): 1 to 3 sentences each, only when the document describes
  them."
- **`CONTRACT_SYSTEM` (`:23`)** allows implementation and configuration as
  planned scope, and adds lessons_learned to the "Do not give" list.
- **`check()` (`:80`)** marks the three fields like `challenge` and
  `solution`: an inferred value whose quote must be in the document and carry
  its numbers (`sourced()`).
- **`build()` (`:118`)** clears `lessons_learned` for an engagement, next to
  `outcomes = []`, whatever the model returned.
- **`strip_commercial()` (`:52`)** lists its fields by name, so the three new
  fields are added to that list. A price in an engagement's implementation
  text is cleared like any other field.

### 3. Reviewer-written sections

`Sourced` (`app/schema.py:62`) gains a flag:

```python
by_reviewer: SkipJsonSchema[bool] = False
```

- **The model can never set it.** It is hidden from the LLM schema, like
  `unsourced`, and `assemble()` builds every item fresh.
- **Edit (`app/review.py:167`).** A new form field `written` ("I wrote this:
  no document quote"):
  - It is accepted only for the three new fields (`WRITABLE`); anything else
    returns 400.
  - With `written`, the route needs a non-empty value, then sets
    `by_reviewer = True`, `source_quote = ""` and `document_id = None`.
  - A save without `written` sets `by_reviewer = False`, so the field goes
    back to normal quote checking.
  - Ticking the box is the reviewer's explicit choice to keep the text.
    Nothing else marks a field reviewer-written.
- **`check()`.** A `by_reviewer` item with a value gets `unsourced = False`,
  and its quote is not checked. An empty one is simply empty.
- **Approve keeps it.** It is not unsourced, so the existing loop leaves it
  alone. The approving reviewer is recorded in `approved_by` as today.
- **No laundering numbers.** A reviewer-written item has no quote, so
  `quotes()` skips it, and its numbers can never vouch for numbers in the
  summary.
- **Review page (`templates/review_detail.html`).**
  - `rows()` passes `written=obj.by_reviewer`, and the field shows a new
    badge, "written by reviewer", beside "sourced" and "unsourced".
  - For the three fields, the edit form has the `written` checkbox,
    pre-ticked when the field is already reviewer-written.
  - Engagement cases get no Lessons learned row or checkbox: `edit()` returns
    400 for `lessons_learned` on an engagement, as it does for outcomes
    (#139).
- **Outputs do not mark it** (decision 5). Approval is the reviewer's
  attestation, and the output already carries only approved content.
- **Anonymisation is unchanged.** `protect()` walks every string in the
  sections (`_walk`), so the new text is anonymised and checked fail-closed
  like the rest.

### 4. Re-extraction keeps reviewer-written text

Today the case upsert in `extract()` (`app/extract.py:163`) replaces `data`
whole, so a document change would wipe a reviewer's written section.

- **What's kept.** Before the upsert, `extract()` reads the old row:
  `select data from cases where document_id = %s for update`. This is after
  the document lock, so the order stays source, then documents, then cases.
  For each of the three fields where:
  - the old field is `by_reviewer`, and
  - the new extraction found no value,

  `extract()` copies the old field into the new case. It also adds a note:
  "<section>: your text from before the document changed was kept; check it
  still holds".
- **What's not kept:** `lessons_learned` when the new basis is engagement.
  The case goes back to review as it does today.
- **A new quote wins.** When the new document now states the section, the
  quoted value replaces the reviewer's text. The note says so, so the
  reviewer can compare.

### 5. Merged cases

- **No new code.** `combine()` takes each section whole from the picked
  member through `MERGE_FIELDS`, with `stamped()` setting its
  `document_id`. A reviewer-written member section keeps `by_reviewer`,
  copied by `deepcopy`.
- **Lessons learned.** A merged case is always an engagement, so `combine()`
  returns an empty `lessons_learned`, next to `outcomes=[]`.
- **Writing on the merged case.** A reviewer can also write sections directly
  on the merged case.
- **Re-merge (#190)** reuses `combine()`, so it needs no change.

### 6. Outputs

`section()` (`app/render.py:82`) gets the new blocks:

```python
blocks = [("Challenge", ...), ("Solution", ...),
          ("Implementation approach", v(case.implementation)),
          ("Configuration and integration", v(case.configuration)),
          ("Lessons learned", None if engagement else v(case.lessons_learned))]
```

- **Word and Markdown** loop over `c.blocks` (`brand/reference.docx`
  `{%p for b in c.blocks %}`, `MD` at `app/render.py:299`). Their templates
  don't change.
- **PowerPoint is unchanged.** The "Reference case" layout has fixed
  placeholders (`PLACEHOLDERS`, `:27`) and no room. The slide stays a
  summary, and the Word file has the full text (as `SLIDE_TEXT` already says).
  An extra slide waits for the marketing master (#15) (decision 6).
- **PDF** is built from Word or PowerPoint, so it follows them.
- **The preview (#197)** renders `section()`, so it shows the new blocks with
  no extra work.

### 7. Docs

`docs/user-guide/reviewers.md`, "Sourced and unsourced fields": add the
"written by reviewer" badge and when to use the checkbox. `test_user_guide`
checks the labels.

## Alternatives rejected

- **One free-text "case study" field.** It can't be quote-checked piece by
  piece. It also can't be merged per section or left out of an engagement.
- **Lists of bullets instead of text.** These are narratives. A list would
  need the add and remove machinery of capabilities for no gain.
- **Letting the model fill gaps from general knowledge.** This breaks the
  delivery-evidence rule. Public best practice is #199.
- **Treating a reviewer-written section as unsourced but kept.** Approve
  drops unsourced fields, and an exception for them would blur what
  "unsourced" means. An explicit flag is clearer.
- **Marking reviewer-written text in client outputs** (for example "Reviewer
  note"). The bid document is a client deliverable, and approval already
  covers it (decision 5).
- **Re-extracting every case now.** The user decided against it (decision 3).
- **A second PowerPoint layout now.** The master comes from marketing (#15).
  Adding a layout to the placeholder master would be thrown away.

## Risks

- **Longer prompt, small local model.** Three more fields may lower
  extraction quality for the rest (qwen3:14b locally). Mitigation: the eval
  script (`scripts/eval_extraction.py`) on the sanitised samples (#14), run
  before and after. An empty section is fine; a regression on outcomes is
  not.
- **Paraphrase drift.** A 3-sentence value backed by one quote can say more
  than the quote. The same risk exists today for `challenge` and `solution`,
  and `sourced()` checks numbers only. The reviewer still reads every field.
- **Reviewer-written text is not checked against anything.** It is the
  reviewer's word. It can name a client, but `protect()` still withholds or
  anonymises the output. It can state a number the documents don't, by
  design of decision 2.
- **The re-extraction carry-over takes a row lock** on the case inside the
  extract transaction. The lock order is unchanged: documents, then cases.
- **Word output grows longer.** That is intended.

## Verification

New tests, by file:

- `tests/test_schema.py`
  - `test_old_case_data_validates_without_new_sections`
  - `test_by_reviewer_hidden_from_llm_schema`
  - `test_reviewer_text_does_not_vouch_for_summary_numbers`
- `tests/test_extract.py`
  - `test_new_sections_extracted_and_checked`: a quote not in the document
    is unsourced.
  - `test_engagement_never_gets_lessons_learned`
  - `test_strip_commercial_clears_new_sections`
  - `test_reextract_keeps_reviewer_written_section_with_note`
  - `test_reextract_quote_replaces_reviewer_text`
- `tests/test_review.py`
  - `test_reviewer_writes_section_and_approve_keeps_it`
  - `test_written_flag_refused_on_other_fields`
  - `test_save_without_written_returns_to_quote_check`
  - `test_engagement_lessons_learned_edit_refused`
  - `test_merged_case_takes_sections_from_picked_member`
  - `test_merged_case_has_no_lessons_learned`
- `tests/test_render.py`
  - `test_new_sections_in_word_and_markdown`
  - `test_engagement_output_has_no_lessons_learned`
  - `test_pptx_unchanged_by_new_sections`
  - `test_new_section_naming_client_is_anonymised`

Then:

- The full suite stays green, including `test_user_guide`.
- Run `scripts/eval_extraction.py` on the sanitised samples (#14), before and
  after the change. This needs a local model, and its results are reported
  as counts only.

## Open questions

None. Decisions 5 and 6 settled the last ones (the user, 2026-10-12).
