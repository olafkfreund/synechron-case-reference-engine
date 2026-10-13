---
status: approved
issue: 243
spec: spec/2026-10-13-243-portal-design-dark-mode.md
---

# Plan: A slicker portal with a dark theme

Worktree `../refs-engine-243`, branch `fix/243-preview-downloads`. The intent
and spec are approved. One PR.

## Approved decisions (from the spec)

1. **Tokens.** Semantic tokens in light and dark sets, with these values:

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
   | `--paper` | `#FFFFFF` | `#F4F6F8` |

   - **Status pairs:**
     - Light: ok `#E5F2E9/#1E6B3A`, warn `#FBF0D9/#6B4600`, bad
       `#F3E3E1/#8A2A1E`.
     - Dark: ok `#173A26/#9DE0B4`, warn `#3D2F12/#F5CF85`, bad
       `#43201C/#F4ABA1`.
   - **Old names stay as aliases:** `--ink`, `--ground`, `--card-border`,
     `--line`, `--thead`, `--input-border`.
   - **Where dark applies:** under
     `@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) }`
     and under `:root[data-theme="dark"]`, with `color-scheme` set to match.
   - **No literal colours.** Component rules hold no literal colour.
     - The header bar is `--header` in both themes, with light text tokens
       for the header only: `--header-text: #D6DDE5`,
       `--header-strong: #FFFFFF`, `--header-tag: #B8C2CE`,
       `--header-border: #5B6675`.
     - Header focus ring: `#73D5CF`.
2. **Scale.**
   - **Type:** 13/15/18/20/28 px, weights 400/500/600.
   - **Spacing:** 4/8/12/16/24/32 px.
   - **Radius:** `--r-control: 8px`, `--r-card: 12px`.
   - **Shadow:** `--shadow`, light
     `0 1px 2px rgb(0 0 0/.04), 0 2px 12px rgb(0 0 0/.06)`; dark
     `0 1px 2px rgb(0 0 0/.3), 0 2px 12px rgb(0 0 0/.3)`.
3. **Buttons.** All are 44 px high, `padding: 0 14px`, weight 500.
   - `.btn` is secondary: surface fill and a border.
   - `.btn.primary` is accent with `--on-accent` text, weight 600.
   - `.btn.ghost` has no border, a subtle hover fill and accent text.
   - `.btn.link` looks like an inline link.
   - `.btn.danger-ghost` is a ghost button with `--bad-fg` text.
   - `.btn.dark` becomes an alias of `.btn`.
   - `.btn-sm` is 36 px visually but keeps a 44 px hit area.
   - Focus: `:focus-visible { outline: 3px solid var(--focus);
     outline-offset: 2px }`.
4. **`details.mini`.**
   - The summary is a 44 px secondary button with a rotating CSS chevron.
   - When open, the summary does not move. The panel is a `--subtle` block
     below it, with 12 px radius and 12 px padding.
5. **Images.** Diagrams and document images sit on `--paper` with 8 px
   padding.
6. **Theme toggle.** `app/static/theme.js` is loaded in `<head>` with no
   `defer`.
   - It reads `localStorage.theme`, either `light` or `dark`, and sets
     `data-theme` before paint. Storage is wrapped in try/catch.
   - The header button, `.theme-toggle`, has `aria-pressed` and
     `aria-label="Dark theme"`, with a sun or moon icon. It sits outside
     `{% if user %}`.
   - A click flips the computed theme, stores it and updates `aria-pressed`.
     The listener is set on DOMContentLoaded, with no inline handlers.
   - Both files are cache-busted with `?v={{ css_version }}`.
7. **Search card.** A header (avatar, title and client, status badges), the
   meta line, chips, sources, then `.result-actions` with a top hairline.
   - **Left side:** Preview (`.btn`), Download Word (`.btn.primary`), Other
     formats (`details.mini`).
   - **Right side:** Research this (`.btn.ghost`), which wraps under 600 px.
   - **Other-format labels:** "PowerPoint", "PDF", "PDF slides", "Markdown",
     "Case study · Word" and "Case study · PDF". Each keeps an `aria-label`
     that names the case.
   - **The Download-all form:** "Preview all", "Download all (Word)" and the
     formats by name.
8. **Source documents.**
   - The summary reads "Source documents · n".
   - The panel is `<ul class="doc-list">` with one row per document: a file
     icon, the label, and "Open" and "Open folder" (`.btn.btn-sm.ghost`,
     same `/go` hrefs, `target=_blank`, `rel="noopener noreferrer"`).
   - A document with no link shows a muted "Stored in the engine. Ask a
     reviewer for a copy."
9. **Library item.**
   - **Header:** the topic with a CSS capital first letter, and a meta line
     using a shared `age` macro ("today", "1 day", "n days").
   - **Toolbar:** one row holding the export group (or, for a brief, a
     format select with Download), Re-check (both buttons side by side, with
     the status line under them) and Delete as `.btn.danger-ghost` at the
     far right. The forms stay separate, with the same actions.
   - **Quotes:** rows with an accent left rule.
10. **Preview.**
    - A "← Back to search" `.btn.link` to `/search`.
    - The form heading is "Download".
    - A "Bid pack" group: Word (primary), then PowerPoint, PDF, PDF slides
      and Markdown.
    - When there is one case, a "Case study (this case only)" group: Word
      (primary) and PDF, with the footer note under them.
    - The groups sit side by side on wide screens.
11. **Downloads unchanged.** Every `name`, `value`, `formaction`, `form=` and
    hidden input stays the same.
12. **Inline styles.** No template keeps an inline `style=` with `color`,
    `background` or `border`. Inline layout styles may stay.

## Steps

1. **Baseline fixtures (before any markup change).** Capture what the forms
   post today, so later steps can't change it.
   - Write `tests/fixtures/forms_243.json`, recording for each form on these
     pages its `action`, every hidden `name=value`, and every
     `button[name=format]` value:
     - search with results (case, top and Download all)
     - preview with one case and with three
     - library item, for quotes and for a brief
   - Generate it with a small script that renders the pages through the
     existing test client and fixtures. Commit the script under `scripts/`
     only if it is reused; otherwise keep it inside the test.
   - Add `test_forms_post_the_same_fields`, which re-renders the pages and
     compares against the fixture.
   - → verify: `pytest tests/test_ui.py -k forms_post` passes on the
     unchanged templates.
   - Traps:
     - order-independent compare
     - ignore `aria-label` and the visible text

2. **Tokens and base components (`app/static/portal.css`).**
   - Replace `:6` with decision 1, including both dark blocks.
   - Replace every literal colour at `:18-63`, `:70`, `:82`, `:95`, `:101`,
     `:106`, `:120`, `:149`, `:203`, `:223` and `:232` with tokens.
   - Add the scale, button kinds, focus ring, badges, `details.mini`, the
     image frame and `.card`/`.section-card` with the shadow (decisions
     2–5).
   - Add a lint test, `test_css_has_no_literal_colours`. It fails on any
     `#hex`, `rgb(` or `rgba(` in `portal.css` outside the token blocks;
     `rgb(0 0 0/…)` is allowed only in `--shadow`.
   - Add a contrast test, `test_theme_contrast`. It parses the token blocks
     and computes WCAG ratios:
     - text, muted and accent on `--bg`, `--surface` and `--subtle`, at
       least 4.5:1
     - `--on-accent` on `--accent`, at least 4.5:1
     - each status fg on its bg, at least 4.5:1
     - focus on surface, at least 3:1

     It runs for both themes.
   - → verify: `pytest tests/test_ui.py`.
   - Traps:
     - keep the old variable names as aliases, because many rules use them
     - `.empty` and `.ok`/`.bad` are used outside badges, so keep those
       classes
     - if a proposed status pair fails AA, adjust the value, record the
       change in Deviations, and don't lower the threshold

3. **Theme toggle.**
   - Add `app/static/theme.js` (decision 6).
   - In `base.html`: the script tag in `<head>`, the toggle button in the
     header before `.site-user`, and the inline style `:7` moved to a class.
   - Add sun and moon icons to `_icons.html`, matching the existing icon
     macro.
   - Add `test_theme_toggle_on_every_page`: logged in and logged out, the
     page has the script and a `.theme-toggle` with `aria-pressed`.
   - → verify: `pytest tests/test_ui.py tests/test_smoke.py`.
   - Traps:
     - the logged-out landing page extends `base.html` too, so check it
     - no `onclick=`

4. **Search card and source documents (`search.html`).**
   - Restructure both card loops, the `sources` macro and the Download-all
     form (decisions 7, 8 and 11).
   - Move all 14 inline styles to classes.
   - Update the tests that assert old labels ("Download this case (…)",
     "Source documents (n)") in `tests/test_search.py` and
     `tests/test_ui.py`, changing only the expected text.
   - New test: a card with a web and a folder link shows both buttons in
     `.doc-list`, and a stored-only document shows the "Stored in the
     engine" line.
   - → verify:
     `pytest tests/test_search.py tests/test_ui.py tests/test_library.py`,
     with `forms_post` still passing.
   - Traps:
     - `form="gen-all"` on the library checkboxes must not move
     - the "top" card keeps "Why it fits", the outcomes and the suggested
       wording inset
     - keep `aria-label`s that name the case

5. **Preview downloads (`output_preview.html`).** Decision 10.
   - Update the label assertions in `tests/test_industry.py` and
     `tests/test_ui.py`.
   - → verify:
     `pytest tests/test_industry.py tests/test_render.py tests/test_ui.py`,
     with `forms_post` passing.
   - Traps:
     - the hidden inputs (`case_ids`, `research_id`, `library_ids`, the
       acknowledgement) stay inside the form
     - the case-study group only appears when there is exactly one case

6. **Library item (`library_item.html`).**
   - Decision 9.
   - Add the shared `age` macro in a new `app/templates/_macros.html`, and
     use it in `library.html` too.
   - Move the 3 inline styles to classes.
   - Update the label assertions in `tests/test_library.py`.
   - → verify: `pytest tests/test_library.py tests/test_ui.py`, with
     `forms_post` passing.
   - Traps:
     - the attach form keeps `id="attach"`, because the quote checkboxes
       use `form="attach"`
     - Delete and Re-check show only when `can_manage`, as today

7. **Inline colours elsewhere.**
   - Run `grep -n 'style="[^"]*\(color\|background\|border\)' app/templates/*.html`.
     Today it finds `base.html:7` and `search.html:57`, both handled above.
   - Re-check every template after steps 3–6, and add
     `test_templates_have_no_inline_colours`.
   - Check the review page's images and the preview's figures for the paper
     frame.
   - → verify: the full suite, run alone:
     `docker compose build app && docker compose run --rm app pytest`.

8. **By hand (the session, not the coder).**
   - Rebuild the demo.
   - Screenshot each page in light and dark, at 1280 px and 390 px:
     - search results with Source documents open
     - preview with one case and with three
     - the library list and a library item
     - research
     - review detail
     - an admin page
   - Show the screenshots to the user before the PR.
   - Then run a read-only Codex review of the diff.

## Tests

- **New:** `forms_post`, the literal-colour lint, contrast, theme toggle,
  `doc-list`, and no inline colours.
- **Updated:** the label expectations only.
- **Full suite:** `docker compose build app && docker compose run --rm app pytest`.

## Rollback

Revert the merge commit. Nothing is stored server-side: the theme choice
lives only in each browser's localStorage, and an unknown value there is
ignored.

## Deviations

- Step 1:
  - The fixture is generated by the test itself with `REGEN_FORMS=1`, run
    once with `tests/fixtures` mounted. There is no script.
  - Later runs use the committed fixture baked into the image.
  - Library items are renamed and given a `query`, so the search page shows
    the library checkboxes and the recheck form is captured.
- Step 2:
  - `--input-border` is its own token (`#76848F` light, `#7D909C` dark),
    because `--border` fails 3:1 for field edges. It is checked at 3:1.
  - Added header tokens `--header-focus` and `--header-hover`.
  - `--line` aliases `--border`.
  - The nav fade mask uses `currentColor`.
  - The image frame is `main img`.
  - The contrast test also covers header text and `accent-hover`.
  - All the plan's status pairs pass AA unchanged; the lowest is 5.66:1.
- Step 3:
  - No route renders HTML without a user, so the logged-out case is tested by
    rendering `message.html` with `user=None`.
  - Two existing tests that count `<script` now allow `theme.js`.
  - Negative controls pass for both step 2 tests: a literal `#fff` fails the
    lint, and a dark muted colour at 2.23:1 fails the contrast test.
- Step 4:
  - `css_version` is now hashed from `portal.css` and `theme.js` together.
  - New `card_head` and `result_actions` macros are shared by both card loops.
  - The old `.result .action-row` phone grid rules are removed.
  - The stored-only check is its own test.
- Step 5: no test asserted the old preview labels. A new test checks the
  Download heading, the Bid pack group, the back link, and that the case
  study group appears for one case and not for three.
- Step 6:
  - The Re-check card is folded into the toolbar, with the status line under
    the buttons.
  - The attach form now comes after the toolbar.
  - `library_item.html` imports the `age` macro as `ago`, because `age` is
    already a page variable.
  - The `.claim` accent rule is reused.
- Step 7:
  - No inline colours remained.
  - The new test also bans `<style>` blocks and `bgcolor`/`color`
    attributes.
  - Three `<script` assertions in `tests/test_review.py` now allow exactly
    one script, `theme.js`.
  - `docs/user-guide/bid-team.md` labels follow the new button text.
  - Full suite: 1118 passed.
