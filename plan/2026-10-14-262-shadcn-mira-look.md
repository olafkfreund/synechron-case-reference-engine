---
status: approved
issue: 262
spec: spec/2026-10-14-262-shadcn-mira-look.md
---

# Plan: Portal look and feel from the shadcn base-mira preset, light and dark

Worktree `../refs-engine-262`, branch `feat/262-shadcn-mira`. It is rebased
onto main **after #259 merges**, because both touch `portal.css`. One PR.

## Approved decisions (from the spec)

1. **Tokens.** The preset's values go into the existing variables in the
   three token blocks, written as hex. The values are in the spec's §1
   table.
   - New `--link`: `#733E0A` in light, `#F0B100` in dark.
   - `--focus` is the same brown or amber.
   - `--on-accent` is `#733E0A`.
   - Radius is 10 px for controls and 14 px for cards.
2. **Contrast substitutions.** Amber is never used as text on white, and
   the preset's grey ring is never used as focus. Links use `--link`.
3. **Montserrat 400/500/600**, self-hosted woff2 with `OFL.txt`, from a
   pinned `@fontsource/montserrat`. IBM Plex Mono stays for code. Reading
   text stays 15 px, capped at 75ch.
4. **Mira controls.**
   - Sizes: `.btn` 32 px and `.btn-sm` 28 px, 13 px text at weight 500,
     inputs 32 px, badges 20 px pills.
   - Variants: primary (amber fill, brown text), default outline, ghost,
     danger-ghost (red text on a 10 % tint), link.
   - `:active` moves the control down 1 px.
   - Under `@media (pointer: coarse)`, every control and nav link keeps a
     44 px minimum.
5. **Header.**
   - Light: a surface bar with a bottom border. Dark: `#0A0A0A`.
   - Active nav: amber with brown text. Hover: `--subtle`.
   - `details.mini` dropdown panels are inverted-translucent with a 8 px
     blur.
6. **Icons.** The 13 icons in `_icons.html` are replaced with their
   Hugeicons free (MIT) equivalents: stroke 1.5, `currentColor`, the same
   macro names, and a licence note in the file.
7. **Unchanged:** customer outputs, `brand/`, the architecture SVG
   colours, routes, forms and `theme.js`.

## Steps

1. **Tokens and contrast (decisions 1 and 2).**
   - Files: `app/static/portal.css:6-30` and the `a{}` rule at `:36`.
   - Every `color:var(--accent)` used as text becomes `var(--link)`; grep
     for `color:var(--accent)`.
   - Extend `tests/test_ui.py::test_theme_contrast` (`:248`) with these
     pairs, in light and dark:
     - `--link` on `--surface` and on `--bg`
     - `--on-accent` on `--accent`
     - `--focus` against `--surface`
     - `--muted` on `--subtle`
   - → verify: `pytest tests/test_ui.py`. If a pair fails, darken or
     lighten the token, within the preset's hue, until it passes. Record
     the final hex in the deviations.
   - Trap: the colour lint forbids raw colours outside the token blocks.
     New colours go in all three blocks.
2. **Montserrat (decision 3).**
   - Download `@fontsource/montserrat` at a pinned version from
     `cdn.jsdelivr.net/npm/@fontsource/montserrat@<ver>/files/montserrat-latin-{400,500,600}-normal.woff2`,
     plus its `LICENSE` saved as `app/static/fonts/OFL.txt`.
   - Swap the `@font-face` rules and the `body` font.
   - → verify: a new test that every `@font-face` url exists under
     `app/static/fonts`, and that `OFL.txt` exists.
   - Trap: `css_version` hashes only CSS and JS, so the fonts are
     cache-busted by name.
3. **Mira controls (decision 4).**
   - File: `portal.css`, the `.btn`, `button`, inputs, `.btn-sm`, badge
     and `details.mini > summary` rules (`:84`, `:106`, `:172`, `:183`).
   - Add the 44 px coarse-pointer block, and keep #259's touch-target
     rules.
   - → verify:
     - `tests/test_ui.py`, where the forms baseline must be unchanged
     - a CSS assertion that `@media (pointer: coarse)` sets
       `min-height:44px` for `.btn`, `.btn-sm`, summaries and
       `.site-nav a`
3b. **Header and menus (decision 5).** Files: the `portal.css` header
   block (`:44-62`) and the `--header-*` tokens.
   - → verify: contrast pairs for the nav text on the header in both
     themes.
4. **Icons (decision 6).**
   - Take the SVG data for the 13 icons from `@hugeicons/core-free-icons`
     at a pinned version (cdn.jsdelivr.net). Convert each to inline SVG
     paths in `_icons.html`.
   - → verify: every name still renders an `<svg>`, with a test that
     loops over the 13 names.
5. **Full suite and rebase** onto main after #259:
   `docker compose build app && docker compose run --rm app pytest`.
6. **Screenshots by the session**, in light and dark at 1280 and 390 px,
   sent to the user before the PR:
   - search
   - preview
   - library
   - a review case
   - Sources
   - upload

   Then the Opus and Codex reviews (design and code), then the PR.

## Tests

As listed per step. Every new check is negative-controlled once.

## Rollback

Revert the merge commit. The change is CSS, fonts and icons only.

## Deviations

None yet.
- **Steps 4 and 2 done first**, because #259 rewrites `portal.css`.
  - **Pinned sources:** `@hugeicons/core-free-icons@4.3.5` and
    `@fontsource/montserrat@5.3.0`, both from jsdelivr.
  - **Icons, in macro order:**
    - Search01
    - Globe02
    - Inbox
    - UserMultiple
    - Database
    - Shield01
    - Clock01
    - File01
    - Tick02
    - Building03
    - Alert02
    - Sun03
    - Moon02
  - **Licence.** The Montserrat licence is appended to the existing
    `OFL.txt`, which already holds IBM Plex's.
  - **Removed.** The unused IBM Plex Sans woff2 files are removed from
    `app/static/fonts`. The architecture renderer keeps its own copy in
    `brand/fonts`.
  - **Tests.** `tests/test_assets.py` checks the font files and the 13
    icons.
- **Step 1, contrast adjustments.** These differ from the spec table and
  are the nearest values that pass the contrast test:

  | Token | Theme | Spec | Used | Reason |
  |---|---|---|---|---|
  | `--border` | dark | white at 10 % | `#2E2E2E` | the 10 % mix onto the surface, as hex |
  | `--input-border` | dark | white at 15 % | `#6B6B6B` | 15 % fails 3:1 |
  | `--input-border` | light | `#D4D4D4` | `#8A8A8A` | meets 3:1 |
  | `--muted` | light | `#737373` | `#6B6B6B` | passes 4.5:1 on `--subtle` |
  | `--accent-hover` | dark | 80 % mix (`#C59205`) | `#F5C842` | the mix gave 3.1:1 with `--on-accent` |
  | `--bad-fg` | light | `#E7000B` | `#C10007` | `#E7000B` gave about 3.9:1 on the tint |

  - Amber is a fill only: the primary button, the active nav item, the
    avatar and the claim edge. Text and icons use `--link`.
  - Ghost and link buttons hover in `--text`.
