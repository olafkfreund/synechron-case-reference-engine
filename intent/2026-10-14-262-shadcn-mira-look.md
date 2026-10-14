---
status: draft
issue: 262
author: olafkfreund
---

# Intent: Portal look and feel from the shadcn base-mira preset, light and dark

## Problem

The user wants the portal to look like a specific shadcn/ui preset:
<https://ui.shadcn.com/create?preset=b1ZzvpxAJ>. It must work in both light
and dark mode.

The preset was fetched on 2026-10-14 from
`https://ui.shadcn.com/init?preset=b1ZzvpxAJ`. It is the **base-mira**
style:

| Element | Value |
|---|---|
| Base | neutral greys with no colour tint: background `oklch(1 0 0)` and foreground `oklch(0.145 0 0)` in light; `oklch(0.145 0 0)` and `oklch(0.985 0 0)` in dark |
| Primary | amber: `oklch(0.852 0.199 91.9)` (light) and `oklch(0.795 0.184 86.0)` (dark), with dark-brown text `oklch(0.421 0.095 57.7)` on it |
| Surfaces | cards `oklch(1 0 0)` and `oklch(0.205 0 0)`. Muted, accent and border greys, with translucent white borders in dark (`oklch(1 0 0 / 10%)`) |
| Destructive | `oklch(0.577 0.245 27.3)` and `oklch(0.704 0.191 22.2)` |
| Radius | `0.625rem` (10 px) |
| Font | Montserrat |
| Icons | Hugeicons |
| Density | "mira" is the compact style: 28 px buttons, 12 px control text, subtle ghost hovers, destructive as a tinted button rather than solid red. Menus are inverted-translucent. |

The portal today uses the #243 tokens:
- a teal accent, `#075C61` in light and `#73D5CF` in dark
- IBM Plex Sans
- blue-tinted greys
- a dark header

All colours already go through the CSS variables in `app/static/portal.css`
(`:root`, plus the two dark blocks). Templates use no raw colours: the
`tests/test_ui.py` colour lint enforces that. So the change is mostly a
change of token values. The portal is server-rendered Jinja with plain CSS:
there is no React or Tailwind, and none is needed.

## Proposed outcome

- **Every portal page** takes on the base-mira look, in light and dark:
  - its neutral palette, amber primary, 10 px radius and Montserrat type
  - its quieter controls: ghost hovers, tinted destructive buttons, and
    hairline borders
- **Theme handling stays as it is.** The two-state light/dark toggle and
  the `prefers-color-scheme` default keep working unchanged.
- **Contrast.** Every text and control pair passes WCAG AA in both themes,
  enforced by the existing contrast test. Where the preset's own pairing
  fails, for example amber used as **link text** on white, the nearest
  passing shade from the same palette is used (the preset's dark-brown
  primary-foreground). Each such substitution is listed in the spec.
- **Screenshots** of the main pages, in light and dark at 1280 and 390 px,
  are reviewed by the user before merge.

## Affected users and systems

- Everyone using the portal: every page that uses `portal.css`.
- Files:
  - `app/static/portal.css` (tokens and component rules)
  - new Montserrat font files in `app/static/fonts/`
  - `app/templates/_icons.html` (14 icons)
  - `app/templates/base.html` (the header)
  - `tests/test_ui.py` (the contrast pairs and the forms baseline)
- **Not affected:** the customer outputs (Word, PowerPoint, PDF, the
  architecture diagram renderer). They keep the Synechron brand fonts and
  colours in `brand/`. See question 1.

## Constraints

- **No new runtime dependencies.**
  - No React, Tailwind, CDN or JavaScript framework.
  - Fonts and icons are self-hosted, because the portal is internal and its
    CSP is strict.
  - Montserrat is under the OFL, and Hugeicons' free set under MIT. Their
    licence files go next to the assets.
- **Accessibility.**
  - The compact sizing must keep targets at least 24 × 24 px (WCAG 2.2
    AA).
  - The main navigation keeps a 44 px target on touch screens.
  - Focus rings stay visible in both themes.
- **Order.** This lands **after** #259. #259 restyles the review and
  Sources pages on today's tokens and is in progress. This change then
  re-tokens everything, so the two don't conflict mid-flight.
- **Data.** Screenshots use demo data only.

## Open questions

1. **Customer outputs.** Should the Word, PowerPoint, PDF and diagram
   outputs also move to Montserrat and amber? Proposed: **no**. They are
   Synechron-branded customer documents (`brand/`), and the preset is for
   the portal UI.
2. **The header.** Today it is a dark bar. The preset has no header style
   of its own; shadcn apps typically use a background-coloured bar with a
   bottom border, and the active item in primary. Proposed: a light bar in
   light mode and a dark bar in dark mode, with the active nav item in
   amber.
3. **Density.** Mira's 12 px control text is denser than today's 15 px
   body. Proposed:
   - adopt mira sizing for controls: 32 px buttons, 13 px control text
   - keep 15 px for body and reading text (case-study text, quotes,
     search results), so long-form reading stays comfortable
4. **Icons.** Should the 14 icons in `_icons.html` be swapped for their
   Hugeicons equivalents? Proposed: **yes**, for one consistent look. The
   icon names stay the same, so templates don't change.
