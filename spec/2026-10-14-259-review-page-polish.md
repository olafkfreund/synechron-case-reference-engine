---
status: draft
issue: 259
intent: intent/2026-10-14-259-review-page-polish.md
---

# Spec: Review page that reads well (field rows, text, architecture, notices)

This is a presentation-only change to `/review/{cid}`. Form names, form
ids, routes, `aria-label`s and the POST bodies stay byte-identical, so the
review tests and MCP phase 4 are unaffected.

The case-study text rows (the user's fourth screenshot, 2026-10-14) are
added to scope. They have the same layout fault: a squeezed textarea, the
label beside it, and Save to the right.

## Design

### 1. Field rows: one grid for every row (`portal.css:286-294`, `review_detail.html:4-17`)

- **One grid.** The `.field-row` grid becomes
  `200px minmax(0,1fr) minmax(0,1.4fr) 136px` at ≥900 px. The action column
  has a **fixed** width, not `auto`, so saved and Add rows line up. The
  quote column gets the most room, because it holds the evidence.
- **Name block.**
  - `.fname` becomes a column: the name, then the badge on its own line,
    the same on every row.
  - `text-transform: capitalize` moves off `.fname` and `.mini-label`. Labels
    are written in sentence case at source, which fixes "Written By
    Reviewer" and "One Line Each: Title: …".
- **Quotes shown in full.**
  - Quote textareas use `field-sizing: content` with `min-height: 3lh` and
    `max-height: 14lh`. Browsers without `field-sizing` (Firefox today) fall
    back to `rows=4`.
  - The textarea inside the tinted `.quote` panel drops its own border and
    background, so the evidence reads as one block, not a box in a box.
- **Quiet actions.**
  - Save is `btn` and Remove is `btn danger-ghost`, stacked in the fixed
    column at full width.
  - Add rows get class `add`: a dashed top border, a muted "Add …" name,
    and a primary Add button in the same column.
- **Phones (<900 px).** The single-column layout stays as it is. Actions
  sit in a row under the quote.

### 2. Case-study text rows (`review_detail.html:47-56`)

- **Layout.** The `<form class="row">` becomes a stacked form:
  1. the label above
  2. a full-width textarea (`field-sizing: content`, `min-height: 8lh`,
     `max-height: 30lh`)
  3. Save under it, right-aligned

- **Flags.** They become one compact `warn-line` list under the label,
  replacing a separate beige bar per flag. They read, for example,
  "Unsupported: 'Need', 'Amend'": the terms are joined, and the original
  flag text stays in a `title` attribute.
- **Sourced points** stay as a `<details>` under the text.

### 3. Architecture empty state (`review_detail.html:61-82`, `review.py` `_arch_panel`)

- **When `arch.a` is empty and the reviewer may edit**, the card shows:
  - **A reason line**, chosen by `_arch_panel`:
    - extraction version < 4 gives "Extracted before architecture diagrams
      were added."
    - a `needs_attention` note starting "architecture" gives that note
    - otherwise "No architecture yet."
  - **A primary "Generate diagram" button.** It posts to the existing
    `/architecture/regenerate` route, with the same `v`.
  - **On an approved case**, the existing muted note: "Generating takes this
    case out of search until you approve it again."
  - **The editor**, inside `<details class="mini"><summary>Write it by
    hand</summary>`. The syntax hint goes in a `<p class="field-help">`,
    with no capitalisation.
- **When `arch.a` exists**, the layout stays as it is. "Save architecture"
  (primary), "Regenerate" and "Remove" (danger-ghost) go in one
  `action-row`, so Regenerate no longer drops onto its own line.

### 4. Notices split into action and information (`review_detail.html:37-43`, `review.py:232`)

- **Classification.** `review.py` classifies each `needs_attention` string
  with a small prefix map:

  | Prefix | Kind | One-line explanation shown under it |
  |---|---|---|
  | `read in ` | info | "The document was long, so it was read in parts. Nothing to do." |
  | `… malformed or unknown item(s) skipped` | info | "The model returned an item that failed validation; it was dropped. Check nothing important is missing." |
  | anything else | action | (the text as it is) |

  Unknown notes default to **action**, so nothing new is ever hidden.
- **"Needs your attention".** A card at the top holds the action notes plus
  the "organisation not in client registry" rows.
  - The registry row keeps its form. Its layout becomes name, label input,
    button, with the help text on its own line beneath them.
- **"Extraction notes".** A muted `<details>` (closed) holds the info
  notes.

### 5. Completeness wording (`app/completeness.py:19,24`)

- **Word counts.** `"{n} of {min} words"` becomes `"{n} words (minimum
  {min})"`.
- **Item counts.** `"{n} of {min}"` becomes `"{n} (minimum {min})"`.
- **Ticks.** The ✓/✗ list becomes a two-column list: a status icon from
  `_icons.html`, then the label and detail. Failed items keep their anchor
  links.

## Alternatives rejected

- **Making the field editor read-only text with an Edit toggle.** It needs
  JavaScript state and changes the forms. That is out of scope for a
  presentation fix.
- **A JavaScript auto-grow for textareas.** `field-sizing` is CSS-only and
  degrades to a fixed height.
- **Hiding info notes entirely.** The reviewer still needs to know a
  document was cut or an item dropped. They are muted, not removed.

## Risks

- **Selectors.** Tests that select by label text or badge text could break
  where the case changes (for example "sourced" vs "Sourced"). The
  displayed text keeps its current case: only the CSS transform goes, and
  the source strings are checked.
- **Firefox fallback.** Firefox shows fixed-height quotes; that is
  acceptable.
- **Classifier.** A prefix map can misclassify a future note as info. The
  default is action, and the map is two lines.

## Verification

- **Existing tests.** `tests/test_review*.py` and `tests/test_ui.py` (forms
  baseline, colour lint, contrast) stay green. The forms baseline must show
  no change in form fields.
- **New tests:**
  - every `.field-row` on a case page has the same grid template, so the
    Add rows and saved rows share the fixed action column (CSS
    assertion on the stylesheet)
  - an info note renders inside the "Extraction notes" details, and an
    unknown note renders under "Needs your attention"
  - with no architecture on a version-3 case, the page shows "Extracted
    before architecture diagrams were added." and a "Generate diagram"
    button posting to `/architecture/regenerate`
  - the completeness detail reads "115 words (minimum 80)"
- the text forms post exactly the same fields with `review.js` absent; the
  script is referenced only from the review page
- **Screenshots** of demo case 11 (the architecture empty state) and a case
  with flags and notes: light and dark, 1280 and 390 px, sent to the user
  before merge.
- **Reviews:** Opus and Codex, read-only.
- **Full suite:**
  `docker compose build app && docker compose run --rm app pytest`.
