---
status: approved
issue: 213
spec: spec/2026-10-14-213-capability-docs.md
---

# Plan: Presale documents as clearly labelled capability documents

Worktree `../refs-engine-213`, branch `feat/213-capability-docs`. One PR.

## Approved decisions (from the spec)

1. **A third basis `capability`.**
   - **Schema:** the `ReferenceCase.basis` Literal and the
     `cases_basis_check` constraint.
   - **Triage:** `basis_for` returns `capability` for a proposal or deck
     that is not delivered work, and for a contract that is not executed.
     `other` gives `None`. The delivered and engagement rules are
     unchanged.
   - **Extraction:** a `CAPABILITY_SYSTEM` prompt (an offering, never
     results). `outcomes`, `lessons_learned`, metrics and callouts are
     cleared. The commercial strip runs as for engagement, and the
     write-up is told "offering, never delivered".
2. **Label.** `CAPABILITY_NOTE = "Capability document: describes what we
   offer, not delivery evidence."` It appears on review (a badge), search
   rows, preview, outputs and MCP (`basis` plus the note).
3. **Search.**
   - Defaults return delivered and engagement only.
   - The "Capability" kind filter shows only capability documents.
   - A count line under the results reads "N capability documents match
     · Show them".
4. **Outputs.**
   - **Bid pack** (`docx`, `pdf_docx`) and **Markdown:** capability
     documents go in a separate "Our capability" section after "Reference
     cases", with the note and no Results block.
   - **Formats that refuse a capability selection** with 400 "capability
     documents can't be case studies":
     - `case_study_docx` and `case_study_pdf`
     - the slide formats (`pptx`, `pdf_pptx`), which are one reference
       slide per case and so count as the spec's "one-pager"
5. **Admin action.** "Capability drafts from skipped documents (N)"
   queues `extract` jobs with `basis=capability` for live documents that
   have no case and whose `documents.kind` is `proposal`, `deck` or
   `contract`.
6. **MCP.** `kind="capability"` on `search_references` and `list_latest`.
   Rows carry `basis`. `generate_output` follows decision 4.

## Steps

1. **Schema and triage.**
   - `app/schema.py:223`: add `"capability"` to the Literal.
   - `sql/schema.sql:82`: add `'capability'` to the check constraint.
   - `app/ingest.py` `basis_for` (decision 1).
   - → verify: `basis_for` unit tests for all five kinds, and
     `tests/test_db.py`. Existing triage tests stay green.
   - Trap: the schema runs at every start, so drop and re-add the
     constraint idempotently, as `:81-82` already does.
2. **Extraction** (`app/extract.py`).
   - `CAPABILITY_SYSTEM` next to `CONTRACT_SYSTEM` (`:28`).
   - In `build`, choose the prompt by basis (`:205`), and clear
     outcomes, lessons and metrics for `capability` (`:223`, the same
     block as engagement, plus metrics). Callouts are dropped by the
     existing engagement rule; extend it to capability in
     `render.py:232`.
   - Pass an "offering, never delivered" instruction to `writeup.write`
     for capability.
   - → verify: a fake-LLM test where a capability build has no outcomes,
     lessons, metrics or callouts, and the commercial strip ran.
3. **Label, review and search.**
   - `render.py`: `CAPABILITY_NOTE` at `:33`, set in `section()` at
     `:216`.
   - Review templates: the badge.
   - `search.py:80,258`: the kind filter, with default results restricted
     to delivered and engagement.
   - `search.html:27`: the "Capability" option and the count line.
   - → verify:
     - default results never include capability; the filter returns only
       them; the count line is right
     - the existing search tests stay green, with the default result set
       pinned as unchanged
4. **Outputs** (`render.py:658-680` `build` and `to_docx`/`to_markdown`):
   the "Our capability" section and the 400 refusals (decision 4).
   - → verify: docx and Markdown section and label tests; 400 for the
     case-study and slide formats.
5. **Admin action** (`app/sources.py`, `sources.html`), following the
   re-extract action at `:31-39` and `:81`.
   - → verify: it queues exactly the skipped proposals, decks and
     contracts, and N matches.
6. **MCP** (`app/mcp_server.py:476-510`): the kind values and `basis` in
   rows.
   - → verify: MCP tests for the capability kind and the label, and
     `generate_output` refuses a case-study format.
7. **Eval, by the session.** `eval_extraction` on the 11 presale documents
   with local `qwen3:14b`: counts only, masked names, a private file.
   Report:
   - the number of capability drafts
   - unsupported flags
   - commercial-term leaks (0)
   - "delivered"-style phrases in capability prose (0)
8. **Full suite, then the reviews and the PR.**
   `docker compose build app && docker compose run --rm app pytest`, then
   Opus, Codex and agy.

## Tests

As listed per step. Every new check is negative-controlled once.

## Rollback

Revert the merge commit, but first delete the capability cases:
`delete from cases where basis='capability'`. The old `cases_basis_check`
is re-added at every start and would refuse to apply while such rows
exist, which would stop the app starting. This goes in the PR.

## Deviations

- **Step 1.**
  - **Drafts at ingest.** Under decision 1, ingest now queues capability
    drafts for new proposals, decks and unexecuted contracts, because it
    extracts any non-`None` basis. The step 5 admin action therefore
    covers only documents ingested before this change.
  - **Changed tests.** Five existing ingest tests that pinned "skipped"
    were updated to expect `capability`. `other` is the kind that is still
    skipped.
- **Step 2.**
  - **One rule.** Outcomes, lessons and callouts are cleared for any basis
    other than `delivered` (`basis != "delivered"`), which covers
    engagement and capability with one rule.
  - **What "metrics" means.** It is read as the outcomes list.
    `duration_months` and `team_size` are kept, as for engagement. They are
    the intent's "team shape".
  - **Prompts.** The write-up prompt gains a "basis: capability"
    instruction (present or conditional tense, never delivered), and
    `ARCH_SYSTEM` sets callouts to `[]`.
- **Step 3.**
  - **Where the note lives.** `CAPABILITY_NOTE` is in `app/review.py`,
    because `render` imports `review`. `render` re-exports it.
  - **Count line.** It is a real `count(*)`, not capped at 20.
  - **"Show them".** It posts the bid text, so confidential text never
    lands in a URL.
  - **Preview.** It shows the note through `section()` with no template
    change.
- **Step 4.**
  - **Ordering.** Capability cases are sorted after the delivered ones by
    reordering `ids`, which keeps the diagram pairing in `produce`
    aligned.
  - **Heading.** The "Our capability" group heading comes from
    `scripts/make_reference_template.py`, and `brand/reference.docx` was
    regenerated from it (no hand edit). `brief.docx` and `case_study.docx`
    are unchanged.
  - **Merge note.** #231 phase (c) also edits that script (the brief image
    section). Whichever merges second regenerates both templates from the
    merged script.
- **Steps 5 and 6** (full suite 1454 passed):
  - **Admin action.** It skips documents that already have a case or a
    queued or running extract job. It takes the re-extract advisory lock,
    so a repeat click queues nothing, and it is 403 for non-admins.
  - **MCP.** `kind="capability"`, and every row carries a `note`.
    `generate_output` refuses through the shared `build()`.
  - **Test fix.** A step 1 test now filters jobs by its own source.
- **Batch limit.** The capability-drafts action queues at most
  `REEXTRACT_BATCH` (25) per click, lowest ids first, and the next click
  continues. Full suite 1455 passed.
- **Step 7 eval, first run** (counts only, local `qwen3:14b`):
  - 9 of 11 documents became capability drafts.
  - 0 outcomes, lessons and callouts.
  - Commercial terms: 0 real. The two hits were the app's own "…payment
    terms removed" note.
  - Result language: 3 hits ("outcome" ×2, "resulted in" ×1).
- **Follow-up for the result language.**
  - The capability write-up prompt forbids result words.
  - A deterministic guard (`RESULT_WORDS`, `check_prose(offering=True)`)
    flags "claims a result: capability documents describe an offering" on
    capability prose, including a reviewer's own edits. Flagged prose
    already blocks approval.
  - The flag is per section, not per sentence.
  - Full suite 1456 passed.
- **Step 7 eval, second run, after the guard** (counts only, 11 presale
  documents, local `qwen3:14b`):
  - 9 capability drafts and 2 engagements.
  - 0 commercial terms, outcomes, lessons and callouts.
  - The model still wrote result language in 3 documents despite the
    prompt. The guard flagged every one (7 result flags in all), so none
    can be approved without a reviewer's fix.
  - The prompt reduces result language; the guard is the control that
    enforces it.
- **Review fixes** (Opus, Codex and agy; full suite 1524 passed after a
  rebuild):
  - **Result flag can't be overridden.** For capability cases,
    `render.section()` drops any prose, value or summary that matches
    `RESULT_WORDS`, even the reviewer's own.
  - **One approval gate.** `writeup.result_fields(case)` scans every
    output text field: title, summary, capabilities, write-ups, headline
    values and the points of the four sections. `approve()` refuses,
    naming the fields. Reviewer edits are checked at approval, not at
    edit time. A point value copied from the source that uses result
    wording, such as "Delivered via Agile", has to be edited before
    approval; that is deliberately conservative.
  - **Summary.** An extracted capability summary that matches is cleared,
    with a note.
  - **Outcomes and lessons.** The guards are now `!= "delivered"` in
    review and search. Capability has its own completeness Results rule
    (a described solution, no outcomes or duration).
  - **Pick prompt.** It knows about capability documents. A capability
    row's tailored text or reason that matches is replaced or blanked.
  - **Facets** follow the kind.
  - **Admin action.** It never re-queues documents that have any extract
    job.
  - **Formats.** Capability cards offer only Word, PDF and Markdown.
