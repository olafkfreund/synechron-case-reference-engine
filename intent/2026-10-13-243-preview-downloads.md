---
status: draft
issue: 243
author: olafkfreund
---

# Intent: The preview page's downloads are easy to scan

## Problem

The bottom of the output preview (`app/templates/output_preview.html:39-47`)
puts every action in one wrapping row, and it is hard to read:

- **Equal weight.** Seven dark buttons look the same. Each one repeats
  "Download (…)", so the format, which is the only part that matters, is
  buried.
- **Split groups.** The bid-pack formats wrap over two lines, and the
  case-study note ("Case study downloads also end with: …") sits between the
  two groups of buttons, so it reads like a stray line.
- **Back button.** "Back to search" is styled like another action and sits
  next to the downloads.

## Proposed outcome

- **Grouped downloads.** The downloads are grouped under short headings. Each
  button names only its format, and the PDF buttons say which source each is
  made from:

  ```text
  ← Back to search

  Bid pack
  [ Word ]  PowerPoint · PDF (from Word) · PDF slides · Markdown

  Case study (this case only)
  [ Word ]  PDF
  Ends with: Based on 1 source document reviewed on 2026-10-13
  ```

- **One main action per group.** Word is the main action in each group. The
  other formats are lighter secondary buttons in the same row.
- **Back link.** "Back to search" becomes a plain link at the top of the
  card.
- **Case study only when it applies.** The case-study group appears only when
  one case is previewed, as it does today. Its footer note sits under its own
  buttons.
- **Phone width.** On a phone the groups stack and the buttons wrap cleanly,
  with no sideways scroll.
- **Same downloads.** Every download still posts the same form fields, so
  what you download is still exactly what the preview shows.

## Affected users and systems

- **Users:** anyone downloading a bid pack or a case study.
- **Files:**
  - `app/templates/output_preview.html`
  - `app/static/portal.css`
  - a preview test

## Constraints

- **No behaviour change.** Keep the same `format` values and the same hidden
  fields: `case_ids`, `research_id`, `library_ids` and the acknowledgement.
- **Accessibility.** Keep real `<button>`s in the one form. Each group gets a
  heading, so a screen reader announces "Bid pack" and "Case study". Buttons
  keep a 44 px target.
- **Shared styles.** Reuse the existing `.btn`, `.btn.primary` and
  `.section-card` styles. Add no new colours.

## Open questions

- **Hidden formats.** Should PDF slides and Markdown sit behind a "More
  formats" disclosure, to shorten the row further? My proposal is no: five
  formats fit in one row on a desktop.
