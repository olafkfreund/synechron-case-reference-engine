---
status: draft
issue: 259
intent: intent/2026-10-14-259-review-page-polish.md
---

# Spec: Review and Sources pages that read well (field rows, text editing, architecture, notices, sources)

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

### 2b. Easier text editing (the user's request, 2026-10-14: "more modern looking with easier edits to the text and formatting")

The editor is restyled as a writing surface, not a form box:
- **Layout.** It spans the full card width, uses a readable line length
  (`max-width: 75ch`, 1.6 line height) and a calm border that is strong
  only on focus.
- **A footer line under the editor:**
  - a **live word count against the minimum**, for example "69 words ·
    minimum 80", which turns to the ok colour when met
  - an **"Unsaved changes"** marker
  - Save and Revert
- **Shortcuts.** **Ctrl/Cmd+S** saves the focused section. **Revert**
  restores the saved text, client-side. The POST stays the plain form, so
  everything works without JavaScript.
- **Preview.** A toggle shows the text as the case study will show it
  (paragraphs, with the same escaping as the outputs).
- **One small static script.** `app/static/review.js` is loaded only on
  this page, follows the `theme.js` pattern, and loads no libraries. It
  adds no inline script, so the CSP is unchanged.
- **No bold, bullet or heading toolbar.** The outputs render prose as plain
  paragraphs and escape Markdown so that "a paragraph or bullet cannot
  become a heading, list, table or strike-through" (`app/render.py:469`).
  Formatting buttons would promise what the Word, PowerPoint and PDF
  outputs drop. Paragraphs (a blank line) are the formatting that
  survives, and the preview shows them. Rich formatting in the outputs
  needs its own issue, offered separately.

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

### 6. Admin Sources page (`app/templates/sources.html:40-70`; the user's screenshot, 2026-10-14)

Today:
- The `<details>` summaries styled as buttons show the browser's
  disclosure marker over the button.
- Upload and Edit are taller than Crawl now and wrap onto a second line.
- "Last run" prints a raw timestamp with microseconds.
- "Re-extract older cases" floats outside the card, misaligned.

The fix:
- **Summaries.** `summary.btn-sm` (all `details.mini` summaries) gets
  `list-style: none` and `::-webkit-details-marker{display:none}`, and is
  sized exactly like `.btn-sm`. "+ Add source" loses its chevron the same
  way.
- **Each source row** is one grid:

  | Column | Contents |
  |---|---|
  | 1 | icon |
  | 2 | name, then the kind badge |
  | 3 | status chips |
  | 4 | last run |
  | 5 | counts |
  | 6 | the actions, never wrapping (`nowrap`) |

  - The last run uses the existing `age` macro ("2 days ago"), with the
    full time in `title`.
  - An opened Upload or Edit panel spans the full row width beneath it,
    not inside the button column.
- **"Re-extract older cases"** moves into the card header as a secondary
  action, with its count.
- **Phones.** Below 900 px the row stacks: name and chips, then run and
  counts, then the actions.
- Form names and actions are unchanged.

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
- the text forms post the same fields with `review.js` absent; the script
  is referenced only from the review page
- the Sources page: `details.mini` summaries hide the disclosure marker
  (CSS assertion), and the last run renders through `age`
- the text forms post exactly the same fields with `review.js` absent; the
  script is referenced only from the review page
- **Screenshots** of `/admin/sources`, demo case 11 (the architecture empty state) and a case
  with flags and notes: light and dark, 1280 and 390 px, sent to the user
  before merge.
- **Reviews:** Opus and Codex, read-only.
- **Full suite:**
  `docker compose build app && docker compose run --rm app pytest`.
