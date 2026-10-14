---
status: draft
issue: 259
author: olafkfreund
---

# Intent: Review page that reads well (field rows, architecture, notices)

Follow-up to the #243 design pass. That pass restyled search, preview and
the library, but not the reviewer's case page (`/review/{cid}`), which is
where reviewers spend most of their time.

## Problem

The user flagged three parts of the case page as not fit to put in front of
the business.

1. **Field rows** (tech stack, technical references, capabilities and so
   on). `app/templates/review_detail.html:4-17`,
   `app/static/portal.css:286-294`.
   - The columns don't line up. The action column is `auto`, so every row
     sizes its own grid: the "Add" rows are wider than the saved rows, and
     their quote box starts at a different x position.
   - Source quotes sit in fixed 2-line textareas with scrollbars, so the
     evidence the reviewer must check is cut off mid-sentence.
   - The name and the "Sourced" badge wrap unevenly: some badges sit beside
     the name, some below it.
   - Every row carries full-weight Save and Remove buttons, so the page reads
     as a wall of buttons.
2. **The architecture card with no diagram.**
   - It shows a large empty editor under a cryptic syntax hint, which
     `.mini-label`'s `text-transform: capitalize` mangles into "One Line
     Each: Title: …".
   - "Regenerate" sits below "Save architecture" as a secondary button, even
     though it is the only useful action when nothing exists yet.
   - Root cause of the user's empty example (case 11): it was extracted and
     approved at extraction version 3, before #246 added architectures, and
     only the 9 seeded demo cases ship one. Nothing tells the reviewer that.
3. **Notices.**
   - "1 malformed or unknown item(s) skipped", "read in 2 parts",
     "Re-extracted with richer extraction…" and "organisation not in client
     registry: …" all render as identical beige bars
     (`review_detail.html:37`). Nothing separates information from the
     actions the reviewer must take.
   - The completeness card says "Challenge: 115 of 80 words", which reads
     like a failure when it means "115 words, minimum 80".

## Proposed outcome

- **Field rows** line up in one grid on every row, Add rows included.
  - Quotes are shown in full: the box grows to fit, within a sensible
    maximum.
  - Each name has its status badge in the same place on every row.
  - Row actions are quiet (ghost buttons; Remove as danger-ghost), and the
    Add rows are visibly set apart.
- **A case with no architecture** shows an empty state: why there is none
  (for example "extracted before architectures were added") and a primary
  "Generate diagram" button. The text editor is tucked behind "Write it by
  hand", with the syntax hint in readable case.
- **Notices** are split:
  - Actions the reviewer must take (client registry, re-approval) form one
    "Needs your attention" group at the top.
  - Information (read in parts, items skipped) goes into a quieter, muted
    list, each with a one-line plain-English explanation.
- **Completeness** reads "115 words (minimum 80)".
- **Both themes:** everything works in light and dark mode, and at 390 px
  width.

## Affected users and systems

- Reviewers and admins: `/review/{cid}`.
- Files: `app/templates/review_detail.html`, `app/static/portal.css`, the
  label and notice text in `app/review.py`, and possibly
  `app/completeness.py`.
- Tests: `tests/test_review*.py` and `tests/test_ui.py` (the forms baseline
  and colour lint).
- No data, schema, extraction or MCP change.

## Constraints

- Presentation only. Form field names, form ids, routes and accessibility
  labels stay as they are, so the review tests and the coming MCP phase 4
  `edit_case` are unaffected.
- The #243 tokens and button kinds only: no new colours. Light and dark
  both pass the existing contrast test.
- Screenshots go to the user in light and dark, at 1280 and 390 px, before
  merge, using demo data only (never the presale documents).

## Open questions

1. **Notice text.** Should the plain-English explanation for each notice
   live in `app/review.py`, next to the existing labels? Proposed: yes, as a
   small mapping. Unknown notices fall back to their current text.
2. **"Generate diagram" on approved cases.** It runs the local model and,
   like any edit, takes an approved case out of search until it is approved
   again. Should it show that warning? Proposed: yes, the same note the page
   already shows for edits on approved cases.
