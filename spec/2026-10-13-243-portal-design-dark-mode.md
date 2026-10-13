---
status: draft
issue: 243
intent: intent/2026-10-13-243-portal-design-dark-mode.md
---

# Spec: A slicker portal with a dark theme

The intent answers the three open questions, all as recommended:

- **Accent:** `#075C61` in light mode and `#73D5CF` in dark mode.
- **More formats:** no "More formats" disclosure on the preview.
- **Theme toggle:** two states, starting from the system setting.

**Added since the intent was approved.** The user flagged a fourth screen on
2026-10-13: the library item page, `library_item.html`. It has a title in
lower case, "saved 0 day(s) ago" and "saved by a colleague", a full card just
for three export buttons, stacked Re-check buttons, and a card just for
"Delete". It is in scope here as screen 5.

## Design

### 1. Tokens and base styles (`app/static/portal.css`)

**Theme tokens.** Replace the `:root` line (`:6`) with semantic tokens. The
old names stay as aliases, so untouched rules keep working:
`--ink: var(--text)`, `--ground: var(--bg)`, `--card-border: var(--border)`,
`--line`, `--thead: var(--subtle)` and `--input-border`.

| Token | Light | Dark |
|---|---|---|
| `--bg` | `#F7F8FA` | `#0E151B` |
| `--surface` | `#FFFFFF` | `#17222B` |
| `--text` | `#17212B` | `#EFF5F7` |
| `--muted` | `#526171` | `#B4C2CA` |
| `--border` | `#D5DEE3` | `#40515C` |
| `--subtle` | `#EFF3F5` | `#22313B` |
| `--accent` | `#075C61` | `#73D5CF` |
| `--accent-hover` | `#05474B` | `#9BE3DE` |
| `--on-accent` | `#FFFFFF` | `#10242A` |
| `--focus` | `#075C61` | `#73D5CF` |
| `--header` | `#17212B` | `#0A1015` |
| `--paper` | `#FFFFFF` | `#F4F6F8` (frame for images) |

- **Status colours as tokens:** `--ok-bg`/`--ok-fg`, `--warn-bg`/`--warn-fg`
  and `--bad-bg`/`--bad-fg`.
  - **Light:** the current values, `#E5F2E9/#1E6B3A`, `#FBF0D9/#6B4600` and
    `#F3E3E1/#8A2A1E`.
  - **Dark:**
    - ok: `#173A26/#9DE0B4`
    - warn: `#3D2F12/#F5CF85`
    - bad: `#43201C/#F4ABA1`
  - Each pair must be AA (4.5:1) or better. The plan step checks this with a
    script.
- **Where dark mode applies:**
  - under `@media (prefers-color-scheme: dark)` with
    `:root:not([data-theme="light"])`
  - again under `:root[data-theme="dark"]`

  `color-scheme` is set to match, so form controls and scrollbars follow the
  theme.
- **Every literal colour** in component rules (37 of them) becomes a token.
  - The header keeps a dark bar in both themes (`--header`), with its own
    light text.
  - `.skip:focus`'s `#fff` becomes `var(--surface)`.

**Scale.**

- **Type:** 13 / 15 / 18 / 20 / 28 px, weights 400 / 500 / 600.
- **Spacing:** steps of 4 / 8 / 12 / 16 / 24 / 32 px.
- **Radius:** `--r-control: 8px`, `--r-card: 12px`.
- **Shadow:** `--shadow: 0 1px 2px rgb(0 0 0/.04), 0 2px 12px rgb(0 0 0/.06)`
  in light, `rgb(0 0 0/.3)` in dark.

**Components.**

- **`.card` and `.section-card`:** surface fill, border, 12 px radius and
  the shadow.
- **Buttons:** every kind is 44 px high with `padding: 0 14px`, weight 500.
  - `.btn` is secondary: surface fill and a border.
  - `.btn.primary` is the accent fill with `--on-accent` text, weight 600.
  - `.btn.ghost` has no border, a subtle fill on hover, and accent text.
  - `.btn.link` looks like an inline link.
  - `.btn.danger-ghost` is a ghost button with `--bad-fg` text, for Delete.
  - `.btn.dark` becomes an alias of `.btn`, so no dark slabs are left.
- **`.btn-sm`:** 36 px visual height inside a 44 px hit area, made with a
  transparent outline padding, for link buttons in dense rows.
- **Focus:** `:focus-visible { outline: 3px solid var(--focus);
  outline-offset: 2px }`. In the header the ring uses `#73D5CF` in both
  themes.
- **Badges and chips:** `--subtle` fill, `--muted` text, 12 px text, 999 px
  radius. `.badge.ok`, `.warn` and `.bad` use the status tokens.
- **`details.mini`, the disclosure:**
  - **Summary:** 44 px high, `padding: 0 12px`, secondary-button look, with a
    CSS chevron that rotates when open.
  - **When open:** the summary stays where it was. The panel is a
    `--subtle` block below the row, with 12 px radius and 12 px padding.
  - Remove `margin: auto` and `background: #fff` (`:148-149`).
- **Images:** diagrams and document images (`figure img`, `.diagram img`)
  sit on `--paper` with 8 px padding and the radius, so they stay readable
  in dark mode.

### 2. Theme toggle (`base.html`, `app/static/theme.js`, new)

- **Early script.** `theme.js` is loaded from `<head>` without `defer`, so
  the theme is set before the first paint and there is no flash. It reads
  `localStorage.theme`. If the value is `light` or `dark`, it sets
  `document.documentElement.dataset.theme`. Every storage access is inside
  try/catch.
- **The button.** A header button,
  `<button class="theme-toggle" aria-pressed="…" aria-label="Dark theme">`,
  shows a sun or moon icon from `_icons.html`.
  - A click flips the current computed theme, stores the choice, and
    updates `aria-pressed`.
  - The listener is attached in the same file on `DOMContentLoaded`, with no
    inline handlers.
  - It works when logged out too: the button sits outside the `{% if user %}`
    block.
- **A static file, not an inline script.** Today's CSP only sets
  `frame-ancestors`, but a file keeps a future `script-src 'self'` working.
  `css_version` busts the cache for both files.

### 3. Search result card (`search.html`)

The macro is shared by the `rows` and `top` loops, so one change fixes both.

```
<article class="section-card result">
  <header class="result-head">avatar · <div class="result-id"><h3>title</h3><span>client</span></div> · <div class="result-status">badges</div></header>
  <p class="result-meta">industry · region · Approved date</p>
  <div class="chips">tech badges</div>
  {{ sources(...) }}
  (top only: "Why it fits", outcomes, suggested-wording inset, unchanged)
  <div class="result-actions">
    <form class="generate">Preview (.btn) · Download Word (.btn.primary) · Other formats (details.mini)</form>
    <form class="research-form">Research this (.btn.ghost, "Research this" + visually hidden "(public sources)")</form>
  </div>
</article>
```

- **Actions row:** `.result-actions` has a top hairline and 12 px padding.
  It is a flex row that wraps. `.research-form` uses `margin-left: auto` on
  wide screens and drops to its own line under 600 px.
- **"Other formats" panel:** its buttons become `.btn` (secondary), labelled
  by format only: "PowerPoint", "PDF", "PDF slides", "Markdown",
  "Case study · Word" and "Case study · PDF". Each keeps an `aria-label` with
  the case title.
- **Download all form:** the same treatment. Labels become "Preview all",
  "Download all (Word)" and the formats by name.
- **Inline styles:** all 14 `style=` attributes in `search.html` become
  classes.
- **Unchanged:** every `name`, `value`, `formaction`, `form=` and hidden
  input stays exactly the same.

### 4. Source documents (the `sources` macro, `search.html:4-6`)

- **Summary:** "Source documents · n", in the new disclosure style.
- **Panel:** a list (`<ul class="doc-list">`) with one `<li>` per document,
  in a flex row:
  - a file icon
  - the label (with the existing anonymisation, unchanged)
  - "Open" and "Open folder" as `.btn.btn-sm.ghost` links to `/go/{id}` and
    `/go/{id}/folder`, opening in a new tab with
    `rel="noopener noreferrer"`, as today
  - or, with no link, a muted "Stored in the engine. Ask a reviewer for a
    copy."
- **Narrow screens:** the row wraps.

### 5. Library item page (`library_item.html`)

- **Header:**
  - The topic, with a capital first letter (CSS
    `::first-letter { text-transform: uppercase }`, so the stored text is
    unchanged).
  - A meta line: "Quotes · saved today · checked today · saved by a
    colleague". It uses the same "today / 1 day / n days" wording as
    `library.html`, from a shared `age` macro.
  - A stale badge where it applies.
- **Toolbar.** One toolbar row under the header replaces the three bottom
  cards:
  - **Export:** Markdown, CSV and Copy as `.btn-sm` secondary buttons in a
    small group. For a brief, the format select and Download.
  - **Re-check:** "Search again" and "Mark re-checked" side by side, with
    the status line under them.
  - **Delete:** at the far right as `.btn.danger-ghost`.

  The forms stay separate `<form>`s with the same actions.
- **Quotes:** each quote is a card row with an accent left rule, the quote
  in 15 px, and a meta line (publisher link · retrieved date · "still on
  the page" badge). The attach checkbox stays.
- **Attach panel:** unchanged, restyled with the tokens.
- **Inline styles:** the 3 inline styles become classes.

### 6. Preview downloads (`output_preview.html:35-47`)

- **Back link:** "← Back to search" is a `.btn.link` above the page head.
  It goes to `/search`; it currently points at `/`.
- **The form.** It keeps every hidden input and contains:
  - `<h2>Download</h2>`
  - a "Bid pack" group (`h3`): Word (primary), then PowerPoint, PDF, PDF
    slides and Markdown (secondary)
  - when there is one case, a "Case study (this case only)" group: Word
    (primary) and PDF (secondary), with the footer note under them as
    13 px muted text
- **Groups:** each is `.download-group`, a heading plus a wrapping button row
  with 8 px gaps. On wide screens the two groups sit side by side; on a
  phone they stack.
- **Unchanged:** every `name="format"` value.

### 7. Inline colours elsewhere

Every other template's inline `style=` that sets a colour or background
becomes a class or token. Inline layout-only styles (margins, widths) may
stay, but none may hold a colour, so dark mode reaches every page. Pages
whose layout is otherwise unchanged:

- `research.html`, `research_view.html`, `research_preview.html` and
  `research_export.html`
- `review_list.html`, `review_detail.html` and `review_merge.html`
- `documents.html`, `brief.html`, `landing.html` and `models.html`
- the admin pages

## Alternatives rejected

- **A CSS framework (Tailwind, Pico or similar).** It is a new dependency and
  a build step, and every template would need rewriting. Tokens on top of
  the current 269-line sheet get the same look with far less churn.
- **Dark mode by `filter: invert()`.** It breaks images, brand colours and
  contrast, and can't be tuned.
- **Toggle state in a cookie or on the server.** It costs a round trip and
  needs storage per user. A browser-only preference is enough, and the
  system setting is the fallback.
- **A three-state toggle (system, light, dark).** The user chose two states.

## Risks

- **Missed literal colours.** One would show up as a white or light box in
  dark mode. Mitigation: a test fails on any hex colour, `rgb(` or `#fff`
  outside the token blocks in `portal.css`, and on any `style=` with
  `color`, `background` or `border-color` in templates.
- **Tests that match markup.** Tests in `test_ui.py`, `test_search.py`,
  `test_industry.py` and `test_library.py` that look for exact labels such
  as "Download (PowerPoint)" or "Download this case" will fail. Those
  expectations are updated in the same step, and none of them check
  behaviour that changes.
- **Theme flash.** If `theme.js` failed to load, the page would show the
  system theme. That is acceptable, because the stored choice is a
  convenience.
- **Accessibility regressions.** Mitigations:
  - contrast is checked by a script over every token pair
  - the 44 px targets are kept
  - the `aria-label`s that name the case stay

## Verification

- **Unit tests (`tests/test_ui.py`):**
  - **Literal-colour lint:** the test described under Risks.
  - **Download form fields:** every download form posts the same field names
    and format values as before, compared against a list written before the
    change.
  - **Theme toggle:** it is in `base.html` for logged-in and logged-out pages
    and has `aria-pressed`.
  - **Search card:** the "Open" and "Open folder" links and the "stored in
    the engine" line render in the new list.
  - **Library item:** the toolbar contains the export, re-check and delete
    forms, with the same actions.
  - **Contrast:** a script computes WCAG ratios for each text/background
    token pair in both themes, all at least 4.5:1 (3:1 for the focus ring
    and borders against their surface).
- **By hand in the demo,** both themes, at 1280 px and 390 px: search
  results with "Source documents" open and closed, the preview with one case
  and with several, the library list and an item, research, review, and an
  admin page. Before and after screenshots go to the user. Committed
  screenshots use only the fictional demo data.
- **Codex:** a read-only Codex review of the finished diff, as for every
  task.
- **Full suite:**
  `docker compose build app && docker compose run --rm app pytest`.
