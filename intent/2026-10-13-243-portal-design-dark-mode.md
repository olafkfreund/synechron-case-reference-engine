---
status: draft
issue: 243
author: olafkfreund
---

# Intent: A slicker portal with a dark theme

## Problem

The portal works, but three screens the user flagged on 2026-10-13 look
clunky, and the portal has no dark mode.

1. **The search result card** (`app/templates/search.html`) is heavy and
   inconsistent:
   - Tall buttons mix weights, so Preview, Download Word and Other formats
     look like three different kinds of control.
   - "Research this (public sources)" floats alone at the far right.
   - Title, client and badges crowd one row.
2. **"Source documents (n)"** (`details.mini`) looks broken.
   - Closed, its summary is a bare black-outlined box with no padding or
     chevron (`portal.css:146`).
   - Opened, the summary jumps to the right edge with a hard-coded white
     fill (`:149`), and the files appear in a full-width grey slab (`:148`).
   - Each file is a plain muted line with inline styles: "name ·
     stored in the engine; ask a reviewer".
3. **The output preview's downloads** (`output_preview.html:39-47`) are hard
   to scan.
   - Seven equal dark buttons each repeat "Download (…)".
   - The case-study footer note sits between the two button groups.
   - "Back to search" looks like another download.
4. **No dark mode.** The stylesheet has a single light palette. It also has
   37 literal colours outside the tokens, and 16 templates carry inline
   `style=` attributes, about 49 in all, so a dark palette can't be added
   by changing two variables.

A read-only Codex design review (gpt-6-sol) found the same four problems,
ranked by visual impact: download hierarchy, the broken disclosure, the
crowded card, and dark-mode blockers.

## Proposed outcome

- **A small design system in `portal.css`.**
  - Tokens for background, surface, text, muted, border, subtle fill,
    accent, on-accent and focus, in a light set and a dark set.
  - One type scale (13/15/18/20/28 px, weights 400/500/600).
  - Spacing on a 4 px step, 8 px radius for controls and 12 px for cards,
    and one soft card shadow.
  - Buttons in four kinds: primary, secondary, ghost and link. All keep a
    44 px touch target.
  - Every page uses the tokens, with no literal colours left in components.
- **Dark mode on every page.**
  - It follows the system setting by default.
  - A toggle in the header switches it, and the choice is remembered in the
    browser. If storage is blocked, the page falls back to the system
    setting.
  - Text and accent meet WCAG AA in both themes. Codex's proposed tokens
    compute to 6.3:1 or better.
  - Diagrams and document images sit on a light "paper" frame, so a
    transparent or white image stays readable in dark mode.
- **Search result card.**
  - **Header:** title and client on the left, status badges on the right.
  - **Body:** a quiet meta line and compact tech chips.
  - **Actions:** below a hairline, Preview (secondary), Download Word
    (primary) and Other formats (a disclosure with the same styling) on the
    left. "Research this" is a ghost button that wraps under them on a
    phone.
- **Source documents.** The disclosure has padding, a chevron and a subtle
  open panel, and its summary stays where it was. Each document is one tidy
  row: a file icon, the label, then "Open" and "Open folder" as small link
  buttons, or a muted "Stored in the engine. Ask a reviewer for a copy".
- **Preview downloads.**
  - "Back to search" is a link above the card.
  - Under "Bid pack" sit Word (primary), then PowerPoint, PDF, PDF slides
    and Markdown (secondary, format names only).
  - Under "Case study (this case only)" sit Word and PDF, with the footer
    note directly underneath.
- **Downloads unchanged.** Every form keeps its field names, `form=` links,
  hidden inputs and `format` values, so downloads and previews are
  unchanged.

## Affected users and systems

- **Users:** everyone who uses the portal: sales, reviewers, admins.
- **Files:**
  - `app/static/portal.css`
  - `app/templates/base.html` (theme toggle and early theme script)
  - `search.html` and `output_preview.html` (restructure)
  - every template with inline colour styles, moved to classes
  - tests for the preview and search markup
- **Not affected:** generated documents (docx, pptx, pdf, md), which keep
  their own brand templates.

## Constraints

- **Downloads.** No change to what any download contains or posts.
- **No new dependencies.** No CSS framework and no web fonts beyond the
  current one. The toggle is a few lines of inline JS that also work under
  the current Content-Security-Policy, or it moves to a static file if the
  policy blocks inline scripts.
- **Accessibility.**
  - Visible focus rings.
  - Headings for each download group.
  - `aria-pressed` and a label on the toggle.
  - No colour-only meaning: badge words stay visible.
- **Screen sizes.** It works at phone width with no sideways scroll.
- **Confidentiality.** No client names or document text in screenshots
  that are committed.

## Open questions

1. **Accent colour.** Codex proposes a slightly deeper teal, `#075C61`, in
   place of `#0B6A6A`, plus a light teal, `#73D5CF`, for dark mode. Keep the
   current teal for light mode instead? I recommend the proposal: it is
   close to the current teal and has more contrast.
2. **"More formats" on the preview.** Should PDF slides and Markdown go
   behind it? I recommend no: five secondary buttons fit on one row.
3. **Toggle wording.** Should the header toggle cycle system, light and
   dark, or switch only between light and dark? I recommend a two-state
   switch that starts from the system setting.
