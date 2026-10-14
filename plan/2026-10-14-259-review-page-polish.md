---
status: approved
issue: 259
spec: spec/2026-10-14-259-review-page-polish.md
---

# Plan: Review and Sources pages that read well

Worktree `../refs-engine-rows`, branch `feat/259-review-page-polish`, based
on main. One PR.

## Approved decisions (from the spec)

1. **Presentation only.**
   - Form `name`s, ids, `action`s, `aria-label`s and POST fields stay
     byte-identical.
   - #243 tokens and button kinds only: no new colours.
   - Light and dark, ≥900 px and phone widths.
2. **Field rows** (`.field-row`):
   - **Grid.** At ≥900 px:
     `200px minmax(0,1fr) minmax(0,1.4fr) 136px`. The action column is
     fixed, so saved and Add rows line up.
   - **Name block.** `.fname` is a column: name, then badge.
   - **No forced capitals.** Remove `text-transform:capitalize` from
     `.fname`, `.mini-label` and the badges where it mangles text. Write
     labels in sentence case at source.
   - **Quotes.** `field-sizing:content`, `min-height:3lh`,
     `max-height:14lh`, with a `rows=4` fallback. No inner border inside
     `.quote`.
   - **Actions.** Save `btn`, Remove `btn danger-ghost`, stacked full width.
   - **Add rows.** Class `add`: a dashed top border and a primary Add
     button.
3. **Case-study text.**
   - A stacked form (label, then a full-width writing surface, then a
     footer).
   - The writing surface is `max-width:75ch`, line height 1.6, and
     `field-sizing:content` (`min 8lh`, `max 30lh`).
   - The footer holds a live word count against the minimum ("69 words ·
     minimum 80", the ok colour when met), "Unsaved changes", Save and
     Revert.
   - Ctrl/Cmd+S submits the focused section.
   - A Preview toggle shows paragraphs, using the outputs' escaping.
   - The flags become one compact `warn-line` ("Unsupported: 'Need',
     'Amend'"), with the full texts in `title`.
   - No bold or bullet toolbar: the outputs render plain paragraphs.
   - `app/static/review.js` follows the `theme.js` pattern: no library, no
     inline script, loaded only on the review page. Everything works
     without JavaScript.
4. **Architecture with none yet.** When the reviewer may edit, the card
   shows:
   - **A reason line** from `_arch_panel`:
     - extraction version < 4 gives "Extracted before architecture diagrams
       were added."
     - an "architecture…" `needs_attention` note gives that note
     - otherwise "No architecture yet."
   - **A primary "Generate diagram" button**, posting to the existing
     `/architecture/regenerate` with `v`.
   - **On an approved case**, the note "Generating takes this case out of
     search until you approve it again."
   - **The editor** inside `<details class="mini"><summary>Write it by
     hand</summary>`, with the hint as `field-help` in plain case.
   - **When an architecture exists:** Save (primary), Regenerate and Remove
     (danger-ghost) in one `action-row`.
5. **Notices.**
   - A prefix map in `review.py`:
     - `read in ` is info: "The document was long, so it was read in
       parts. Nothing to do."
     - `… malformed or unknown item(s) skipped` is info: "The model
       returned an item that failed validation; it was dropped. Check
       nothing important is missing."
     - Anything else is action.
   - A "Needs your attention" card at the top holds the action notes plus
     the client-registry rows: name, label input and button on one line,
     the help text beneath.
   - A closed, muted "Extraction notes" `<details>` holds the info notes.
6. **Completeness.**
   - Wording: `"{n} words (minimum {min})"` and `"{n} (minimum {min})"`.
   - Ticks: a status icon from `_icons.html` instead of ✓/✗, with the
     anchors kept.
7. **Sources page.**
   - **Summaries.** `details.mini > summary` gets `list-style:none` and
     `::-webkit-details-marker{display:none}`, and is sized exactly like
     `.btn-sm`. The same goes for "+ Add source".
   - **Source rows.** A grid: icon, name and kind, chips, last run (as an
     age, with the full time in `title`), counts, and the actions
     (`nowrap`).
   - **Panels.** An opened panel spans the full row beneath.
   - **"Re-extract older cases"** moves into the card header.
   - **Phones.** The row stacks below 900 px.

## Steps

1. **Field rows and completeness (decisions 2 and 6).**
   - Files:
     - `app/static/portal.css:286-294`, plus `.mini-label` at `:335`
     - `app/templates/review_detail.html:4-17` (the `field_row` macro: add
       class `add` when `r.new`)
     - `review_detail.html:34-36` (completeness)
     - `app/completeness.py:19,24`
   - → verify:
     - `pytest tests/test_review*.py tests/test_ui.py tests/test_completeness*.py`
     - update only the assertions on the old "of N words" wording, and say
       which
   - Traps:
     - `tests/test_ui.py`'s colour lint: only `var(--…)` tokens.
     - The forms baseline must not change.
     - Check every place `completeness` detail strings are used: the
       review list, MCP and the outputs. Change only the display wording,
       never the `ok` logic.
2. **Case-study text and `review.js` (decision 3).**
   - Files:
     - `review_detail.html:47-56`
     - new `app/static/review.js`, included through `{% block head %}`
       with `?v={{ css_version }}`, as `base.html:5` does for `theme.js`
     - CSS for `.writer`, `.writer-foot` and `.preview`
   - Expose the minimum words through a `data-min` attribute on the
     textarea, from `completeness.CHECKS` (challenge and solution only;
     others have no minimum, so show the count only).
   - → verify:
     - a test that the text form fields are unchanged
     - a test that `review.js` is referenced only on `/review/{cid}`
     - `node --check app/static/review.js`, if node is in the image;
       otherwise skip it and say so
   - Trap: the preview must escape HTML. Build it with `textContent`
     only, never `innerHTML`.
3. **Architecture empty state (decision 4).**
   - Files: `review_detail.html:61-82` and `app/review.py:527`
     (`_arch_panel` gets `reason`).
   - → verify: a test with a version-3 case without architecture shows the
     reason line and a "Generate diagram" button posting to
     `/review/{cid}/architecture/regenerate`; the existing architecture
     tests stay green.
4. **Notices (decision 5).**
   - Files: `app/review.py:232` (split `notes` into `actions` and `infos`
     with the explanations) and `review_detail.html:37-43`.
   - → verify:
     - a test where an info note sits inside "Extraction notes" with its
       explanation, and an unknown note sits in "Needs your attention"
     - the registry form is unchanged
     - the header badge "N notes" counts the action notes only
5. **Sources page (decision 7).**
   - Files: `app/templates/sources.html:30-70` and `portal.css`.
   - Last run as an age: compute days in `app/sources.py`, next to the
     row query, and render through `_macros.age`, with the full timestamp
     in `title`.
   - → verify: `pytest tests/test_sources.py`, plus a stylesheet assertion
     that `details.mini > summary` hides the marker.
6. **Full suite**, then screenshots by the session:
   `docker compose build app && docker compose run --rm app pytest`.
   - The session takes light and dark screenshots at 1280 and 390 px of:
     - `/admin/sources`
     - demo case 11 (the architecture empty state)
     - a demo case with flags and notes

     It sends them to the user before the PR.
   - Then the Opus and Codex reviews, then the PR.

## Tests

As listed per step. Every new check is negative-controlled once.

## Rollback

Revert the merge commit. There is no data or schema change.

## Deviations

- Step 1:
  - Labels stay lowercase at source, because the `aria-label`s that embed
    them must stay byte-identical. The capital comes from the existing
    `.cap-first::first-letter` rule.
  - The Remove button's `danger-ghost` class went in with step 1
    (decision 2).
  - No existing test asserted the old "of N words" wording.
- Step 2:
  - The footer controls (count, Unsaved, Revert, Preview) are added by
    `review.js`, so the page without JavaScript shows the textarea and
    Save only.
  - The flags line joins the flag texts with "; ". The flags are free-form
    strings from several checkers, so no "Unsupported:" prefix was
    invented.
  - Two existing assertions count `<script` on the detail page as 2
    (`theme.js` and `review.js`). `css_version` also hashes `review.js`.
  - `node --check` ran on the host, because the app image has no node.
- Step 3:
  - With no architecture, the editor's own "Save architecture" sits inside
    "Write it by hand". The shared action row (Save, Regenerate, Remove)
    appears once an architecture exists.
  - The edit form got `id="arch-form"`, so the row's Save can use
    `form="arch-form"`. Nothing was removed or renamed.
- Step 4:
  - The "malformed or unknown item(s) skipped" note is matched by its
    suffix, since it starts with a count.
  - The registry help text moved below its form. The form itself is
    unchanged.
  - "Extraction notes" is muted through `p.muted` on its items.
- Step 5:
  - The source row query gives the last run's age in days. The "+ Add
    source" summary gets the same sizing as the others.
  - The marker hiding already existed; the step only asserted it.
  - `details.mini > summary` sizing also applies to "Cited points" and
    "Write it by hand", per the plan's wording.
  - Below 900 px the existing flex wrap stacks the row.
  - Full suite: 1394 passed. The branch predates #261.
