---
status: draft
issue: 262
intent: intent/2026-10-14-262-shadcn-mira-look.md
---

# Spec: Portal look and feel from the shadcn base-mira preset, light and dark

The intent's open questions were approved as proposed (2026-10-14):

1. **Customer outputs** (Word, PowerPoint, PDF, diagrams) keep the
   Synechron brand in `brand/`.
2. **Header:** light in light mode, dark in dark mode, with the active item
   in amber.
3. **Density:** mira-sized controls (32 px, 13 px text), with 15 px kept
   for reading text.
4. **Icons:** all 14 are swapped for Hugeicons equivalents.

## Design

### 1. Tokens: the preset's values in our existing variables (`app/static/portal.css:6-30`)

- **Where.** The existing token names and the three blocks stay in place:
  `:root`, the `prefers-color-scheme: dark` block, and
  `[data-theme="dark"]`. Only the values change, so templates and rules
  that already use `var(--…)` follow automatically.
- **Format.** Values are written as hex, converted from the preset's OKLCH.
  Hex keeps the existing contrast test's parser working, and the
  conversion is listed in the plan.

| Token | Light | Dark | Preset source |
|---|---|---|---|
| `--bg` | `#FAFAFA` | `#0A0A0A` | `sidebar` / `background` |
| `--surface` | `#FFFFFF` | `#171717` | `card` |
| `--text` | `#0A0A0A` | `#FAFAFA` | `foreground` |
| `--muted` | `#737373`, darkened if the test fails on `--subtle` | `#A1A1A1` | `muted-foreground` |
| `--border`, `--line` | `#E5E5E5` | `rgb(255 255 255/.10)` | `border` |
| `--input-border` | `#D4D4D4`, raised to meet 3:1 if needed | `rgb(255 255 255/.15)`, raised likewise | `input` |
| `--subtle` | `#F5F5F5` | `#262626` | `muted` / `accent` |
| `--accent` (primary fill) | `#FDC700` | `#F0B100` | `primary` |
| `--on-accent` | `#733E0A` | `#733E0A` | `primary-foreground` |
| `--accent-hover` | the primary at 80 %, mixed on `--surface` | the same | the button `hover:bg-primary/80` |
| **`--link`** (new) | `#733E0A` | `#F0B100` | primary-foreground (light), primary (dark) |
| `--focus` | `#733E0A` | `#F0B100` | the preset's `ring` grey fails 3:1 (see below) |
| `--bad-fg` / `--bad-bg` | `#E7000B` text on a 10 % tint | `#FF6467` on a 20 % tint | `destructive` |
| `--ok-*`, `--warn-*` | unchanged hues, re-tuned onto the neutral surfaces | the same | not in the preset |
| `--r-control` / `--r-card` | `10px` / `14px` | the same | `radius` 0.625rem (cards use `radius + 4px`) |

**Contrast substitutions.** These were computed on 2026-10-14 and are
enforced by `tests/test_ui.py`:

| Pair | Ratio | Result |
|---|---|---|
| amber `#FDC700` as **text** on white | 1.57 | fails |
| `--link` `#733E0A` on white | 8.69 | passes |
| the preset's `ring` `#A1A1A1` on white | about 2.6 | fails the 3:1 focus indicator; `--focus` uses `#733E0A` in light |
| `#733E0A` on `#FDC700` | 5.54 | passes |
| `#733E0A` on `#F0B100` | 4.54 | passes |
| amber `#F0B100` on `#0A0A0A` | 10.3 | passes |
| amber on `#171717` | 9.4 | passes |
| `#737373` on white | 4.73 | passes; `--muted` on `--subtle` is re-checked by the test |

- **Links.** `a{color:var(--link)}` replaces `var(--accent)`, since amber
  is a fill colour, not a text colour, in light mode.

### 2. Type: Montserrat, self-hosted

- **Files.** `app/static/fonts/montserrat-{400,500,600}.woff2` (Latin
  subset) plus `OFL.txt`, taken from the `@fontsource/montserrat` package
  at a pinned version. They replace IBM Plex Sans in `@font-face` and
  `body`.
- **Monospace.** IBM Plex Mono stays for `code`, `.mono` and `.query-box`.
  The preset sets no mono font.
- **Sizes.** Reading text stays at 15 px with line height 1.5. Montserrat
  is wider than Plex, so long-form blocks (case text, quotes, results) are
  capped at `75ch`.
- **Headings.** Weight 600, `letter-spacing: -0.01em`.

### 3. Controls: mira density (`.btn`, `.btn-sm`, inputs, badges)

| Element | Today | Mira |
|---|---|---|
| `.btn` height | 44 px | 32 px (mira `lg`) |
| `.btn-sm` height | — | 28 px (mira `default`) |
| Control text | 15 px | 13 px, weight 500 |
| Inputs | — | 32 px, `--input-border`, focus ring 2 px at 30 % plus a border in `--focus` |

- **Variants:**
  - **primary:** an amber fill with brown text
  - **default:** an outline: border, transparent fill, `--subtle` on hover
  - **ghost:** transparent, `--subtle` on hover
  - **danger-ghost:** `--bad-fg` text on a 10 % red tint (mira
    `destructive`)
  - **link:** `--link`, underlined on hover
- **Press feedback:** `translateY(1px)` on `:active`, as in mira.
- **Target sizes.**
  - 28 px is at least the WCAG 2.2 AA minimum of 24 px.
  - Under `@media (pointer: coarse)`, `.btn`, `.btn-sm` and the nav links
    keep a 44 px minimum, so phones and tablets are not compacted.
- **Badges:** 20 px pills, 12 px text weight 500, on tinted backgrounds.

### 4. Header (`app/templates/base.html`, `portal.css` header block)

- **Bar.**
  - **Light:** a `--surface` bar with a `--border` bottom line, and
    `--text` brand and nav text.
  - **Dark:** a `#0A0A0A` bar with a `--border` line.
- **Navigation.**
  - **Active item:** amber fill with brown text.
  - **Other items:** hover in `--subtle`.
- **Tokens.** The `--header-*` tokens are rewired to these values. No
  template change is needed beyond what the colour lint allows.
- **Menus and popovers.** The theme toggle and the `details.mini`
  dropdowns are inverted-translucent (`menuColor`):
  - **Light:** a dark panel `rgb(23 23 23/.92)` with
    `backdrop-filter: blur(8px)`.
  - **Dark:** a light panel `rgb(250 250 250/.92)`.
  - **Text:** the inverse of the panel.

### 5. Icons: Hugeicons free set (`app/templates/_icons.html`)

- **Which icons.** The same 14 macro names, so no template changes:
  - search, database, alert, users, shield, inbox, clock, file, check, sun,
    moon, globe, building, plus the 14th, already in the file
- **Source.** The SVG paths come from `@hugeicons/core-free-icons` (MIT) at
  a pinned version, converted once to inline SVG with `stroke-width` 1.5
  and `currentColor`. A licence note is added at the top of the file.

### 6. What is not changed

- Customer outputs and `brand/`.
- The architecture SVG colours inside the portal: it shows the customer
  diagram as delivered.
- Routes, forms and JavaScript behaviour. `theme.js` is unchanged.

## Alternatives rejected

- **Adopting React, Tailwind and shadcn components.** That would rewrite a
  server-rendered app for a visual change. The preset's value is in its
  tokens and sizing, which plain CSS carries fully.
- **Loading Montserrat and Hugeicons from a CDN.** The CSP and the internal
  network forbid it, and self-hosting is a few KB.
- **Using the preset's grey ring and amber links as-is.** They fail WCAG AA
  (1.57:1 and about 2.6:1). The substitutions use the preset's own brown.

## Risks

- **#259 lands first.** Its new rules use today's tokens and are carried
  over, and a rebase conflict in `portal.css` is expected. The plan
  re-runs #259's tests.
- **Montserrat's width** can wrap labels and buttons that fit in Plex.
  Screenshots of every main page, at 390 px and 1280 px, catch it.
- **The 32 px controls** change the forms baseline only if markup changes.
  This is CSS only, so the baseline should hold.

## Verification

- **`tests/test_ui.py`:**
  - the colour lint (no raw colours outside the token blocks)
  - the contrast test extended with the new pairs: `--link` on
    `--surface` and `--bg`, `--on-accent` on `--accent`, `--focus` against
    `--surface`, and `--muted` on `--subtle`, in light and dark
  - the forms baseline unchanged
- **New test:** `@font-face` references only self-hosted files, and the
  licence files exist.
- **Screenshots** in light and dark, at 1280 and 390 px, sent to the user
  before merge:
  - search
  - preview
  - library
  - a review case
  - Sources
  - upload
- **Reviews:** Opus and Codex (design and code), read-only.
- **Full suite:**
  `docker compose build app && docker compose run --rm app pytest`.
