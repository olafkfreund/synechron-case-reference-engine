---
status: approved
issue: 246
spec: spec/2026-10-13-246-architecture-diagrams.md
---

# Plan: Architecture diagrams a bid team can show a customer

Worktree `../refs-engine-246`, branch `feat/246-architecture-diagrams`. There
are two PRs: phase A (steps A1–A8), then phase B (steps B1–B5). The
placeholder removal is the separate branch
`chore/246-no-placeholder-diagrams`, which merges first.

## Approved decisions (from the intent and spec)

1. **Our own renderer.** A lane layout feeds four outputs:
   - SVG: review page and preview
   - Pillow PNG at 2x (3200×1800): docx and pdf
   - native python-pptx shapes: pptx
   - Mermaid text: md

   No Graphviz, no headless browser, no cairosvg.
2. **The `Architecture` model on `ReferenceCase`** is hidden from the
   extraction schema and stored as JSON, so no migration is needed.
   - **`components`**, at most 12. Each has:
     - `id`, `name` (at most 40 characters), `role` (at most 70)
     - `zone`: `channels | experience | integration | core | data |
       platform | external`
     - `kind`: `user | app | service | queue | store | external | platform`
     - `cite`
   - **`flows`**, at most 14. Each has `src`, `dst`, `label` (at most 40
     characters), `mode` (`api | event | batch | file | ui | data`) and
     `cite`.
   - **`callouts`**, at most 3. Each has `text` (at most 60 characters) and
     `outcome` (an index).
   - **`title`**, at most 80 characters, default "Solution architecture".
   - **`by_reviewer`** and **`flags`**.
   - **Dropped items:** unknown flow endpoints are dropped, and so is a
     component with no cite and no tech match. The caps keep the first n.
3. **Layout.**
   - **Canvas:** 1600×900.
   - **Columns:** channels, experience, integration, core and data, with
     external on the right and platform as a bottom band. Empty zones
     collapse.
   - **Components:** 230×78 cards with a kind glyph, the name in bold 15 pt
     and the role in 12 pt over at most 2 lines.
   - **Ordering:** barycentre, 2 sweeps.
   - **Flows:** orthogonal elbow connectors through the gutters, with a label
     pill and mode glyph. Parallel flows are offset by 10.
   - **Callouts:** up to 3 numbered outcome callouts, top-right.
   - **Legend and footer:** a legend and a "Source: <case> · reviewed
     <date>" footer.
   - `layout()` is deterministic and returns a Scene with style keys, not
     colours.
4. **Outputs.** An architecture renders only when the case is approved, the
   architecture is present, and its flags are resolved.
   - **Case study:** an "Architecture" section first, then the used document
     diagrams.
   - **Bid docx/pdf:** the image in each case's section.
   - **pptx:** one slide per case, "<title>: solution architecture", built
     from native shapes.
   - **md:** a Mermaid block.
   - **Preview:** the SVG.
   - **Anonymisation:** all text goes through `section()` then `protect()`.
     A blocked architecture is omitted and counted.
5. **Completeness.** The "diagram" item is met by an architecture or by a
   ready document diagram.
6. **Demo.** The 9 approved demo cases get hand-written fictional
   architectures. Each must cite points whose quotes are in the demo text,
   and must pass `check_architecture` before the seed writes anything.
7. **Proposal (phase B).**
   - **When:** one `EXTRACT_MODEL` call after the prose (`ARCH_SYSTEM`).
   - **Input:** numbered sourced points (`solution n`, `implementation n`,
     `configuration n`), `reference n`, the tech stack and the outcomes.
   - **Output:** the flat JSON `{components, flows, callouts}`. Code assigns
     the ids.
   - **On failure:** a class-only note.
   - **Re-extraction:** a `by_reviewer` architecture is kept.
8. **Check (phase B, `check_architecture`).**
   - Names, roles, labels and callouts go through
     `check_prose(text, cited_quotes, all_quotes)`.
   - A component name must match its cite's quote, the tech stack or a
     reference.
   - A callout's number must match its outcome.
   - Commercial terms are flagged.
   - Flags go to `architecture.flags` and `needs_attention`, and block
     approval until fixed or overridden.
9. **Review (phase B).**
   - **The panel:** the SVG, the flags, and the cited points (collapsed).
   - **The line-format editor:** `title:`, `component: name | role | zone |
     kind | cite`, `flow: A -> B | label | mode | cite`, `callout: text |
     outcome`.
     - Errors are reported per line, and the previous version is kept.
   - **Buttons:** Regenerate (with a confirm if edited) and Remove.
   - **Audit:** a change reopens an approved case (#143) and is audited.
   - **Document diagrams:** "Use" stays.

## Steps

### Phase A (PR 1): model, layout, renderers, outputs, demo

**A1. Fonts (`brand/fonts/`).**
- Add the IBM Plex Sans TTF files at weights 400, 500 and 600, plus
  `OFL.txt`, taken from the official IBM/plex release on GitHub.
- Record their sha256 in `brand/fonts/SHA256SUMS`.
- Make sure the Dockerfile copies `brand/` into the image; it does today,
  but check.
- → verify: a test loads each font with `ImageFont.truetype`.
- Traps:
  - don't vendor anything else
  - the woff2 web fonts stay untouched

**A2. Model (`app/schema.py`).**
- Add `Architecture`, `ArchComponent`, `ArchFlow` and `ArchCallout`
  (decision 2), and `ReferenceCase.architecture`, as
  `SkipJsonSchema[Architecture | None] = None`.
- Add architecture text to `search_text()`.
- → verify: `pytest tests/test_schema.py`. New tests cover the caps, unknown
  endpoints dropped, `llm_schema()` without `architecture`, and old case
  JSON still loading.
- Traps:
  - `extra="forbid"` on `_Model`
  - pydantic `Literal`s for zone, kind and mode

**A3. Layout (`app/archdiagram.py`, new).**
- Add `layout(arch, footer) -> Scene` (decision 3). Scene is a dataclass
  holding lists of `Box`, `Line` and `Text`, with style keys.
- → verify: `tests/test_archdiagram.py`.
  - The same input gives the same Scene.
  - No two component boxes overlap.
  - Everything stays inside 1600×900.
  - Empty zones collapse.
  - 12 components and 14 flows still fit.
  - Text is wrapped to 2 lines with an ellipsis.

**A4. Renderers (same module).**
- **`to_svg(scene)`:**
  - escapes all text
  - has no `<script>`, `href` or external references
  - has a `viewBox` and is sized by CSS
  - uses the brand palette as constants in the module
- **`to_png(scene, scale=2)`:** Pillow with the A1 fonts.
- **`to_pptx_shapes(slide, scene, left, top, width, height)`:**
  - `MSO_SHAPE.ROUNDED_RECTANGLE` for components and zones
  - elbow connectors (`MSO_CONNECTOR.ELBOW`) with an end arrowhead, set
    through the line XML `a:tailEnd`
  - text boxes for labels
- **`to_mermaid(arch)`:** `flowchart LR`, a `subgraph` per zone, labelled
  edges, and escaping of `"[]|<>`.
- → verify:
  - the SVG parses as XML and has no script or href
  - the PNG is 3200×1800 with more than 1% non-background pixels in each
    component box (text was drawn)
  - in the pptx, the shape count is at least the components plus zones, the
    connector count equals the flows, and the text reads back
  - the Mermaid snapshot matches

**A5. Outputs (`app/render.py`, templates).**
- **`section()`** adds `architecture`, the text-only dict, after
  `section()`/`protect()`, the same as the other text.
- **Images** are added after `protect()`, like diagrams (`:256-258`).
  - docx: an `InlineImage` of `to_png`, at 160 mm.
  - pptx: a new slide via `_layout(prs, DIAGRAM_LAYOUT, …)`, or blank with a
    title, plus `to_pptx_shapes`. The fallback mirrors `_diagram_slide`
    (`:320-355`).
  - md: the Mermaid block under "Architecture".
- **`output_preview.html`:** the SVG inline, inside `<figure
  class="paper">`. It is rendered server-side; it is not user HTML. Mark it
  safe only after `to_svg` escaping, and test it with a hostile label.
- **`scripts/make_reference_template.py`:** an "Architecture" block before
  the diagrams loop in both docs. Regenerate `reference.docx` and
  `case_study.docx`, but not `brief.docx`.
- **`app/completeness.py:45`:** decision 5.
- → verify: `pytest tests/test_render.py tests/test_industry.py
  tests/test_completeness*.py`. New tests:
  - each format carries the architecture
  - a hostile client name in a component is anonymised or blocked
  - an unapproved case has no architecture
  - the preview matches the downloads
- Traps:
  - an architecture's text must go through `protect()` before drawing; draw
    from the protected dict, never from the raw case
  - `blocked()` on the joined architecture text
  - #243 changes `output_preview.html` too, so rebase on whichever merges
    first and keep both sides

**A6. Check, used by the demo now and phase B later
(`check_architecture(case) -> list[str]` in `app/schema.py`).**
- Implement decision 8, without the approval gate, which comes in B3.
- → verify: unit tests for an unsourced name, a number not in the quote, a
  price, a tech-stack match accepted, and a clean architecture passing.

**A7. Demo (`demo/cases.json`, `scripts/seed_demo.py`).**
- Write an `architecture` for each of the 9 approved demo cases, fictional
  and matching each case's story. Each has 6–10 components, 6–12 flows and
  1–2 callouts, and every cite resolves.
- Validate before any write: `check_architecture` must return `[]`.
- **Refresh.** Store `case.architecture`. Because an unchanged document is
  skipped (`seed_demo.py:155`), the seed also updates `data.architecture` for
  existing demo cases whose spec changed.
  - Compare the stored `architecture` JSON and update only the field, in the
    same transaction, never touching a non-demo source.
- **Tests.** `tests/test_seed_demo.py` expects 9/9 completeness again for the
  approved cases, `architecture` present, and a bad spec failing validation
  before writes.
- → verify: `pytest tests/test_seed_demo.py`.

**A8. Full suite, then the session's own checks.**
- `docker compose build app && docker compose run --rm app pytest`.
- Then the session:
  - rebuilds the demo
  - downloads Word, PDF and PowerPoint for two cases and opens them
  - takes screenshots for the user
  - runs an Opus review and a Codex review
  - opens PR 1

### Phase B (PR 2): proposal, check gate, review editor

**B1. Proposal (`app/writeup.py`).** Implement decision 7: `ARCH_SYSTEM`,
`ArchProposal` (flat), and `propose(case, data_class)` called after
`write()`. On failure, the class-only note. Keep a `by_reviewer`
architecture.
- → verify: a stub reply builds an Architecture with ids; a bad reply gives
  the note; a reviewer's architecture survives re-extraction.

**B2. Gate.** `check_architecture` runs on proposal and on save. Its flags
go into `needs_attention` and block approval, through the override path
used for prose flags.
- → verify: a flagged architecture blocks approve, and an override allows
  it.

**B3. Review (`review.py`, `review_detail.html`).**
- An "Architecture" panel showing the SVG (served inline in the page), the
  flags, and the cited points.
- `POST /review/{cid}/architecture` parses the line format, checks it and
  saves. Errors are listed per line, and the previous version is kept.
- `/regenerate` and `/remove`.
- Use the same `v` optimistic-lock field and `_reopen()` as diagrams
  (`review.py:330`).
- → verify: the line format round-trips, a parse error keeps the old
  version, regenerate and remove work, and audit rows are written.

**B4. Eval (`scripts/eval_extraction.py`).** Per document: components,
flows, flags, and components with a cite. Counts only, `qwen3:14b`, on the
11 trial documents.

**B5. Full suite**, then the session runs the Opus and Codex reviews and
opens PR 2.

## Tests

As listed per step. The full suite runs after A8 and after B5:
`docker compose build app && docker compose run --rm app pytest`.

## Rollback

Revert the merge commits. `architecture` is an optional JSON field, so old
code ignores it. The demo can be re-seeded.

## Deviations

- A1:
  - The fonts come from IBM/plex release `@ibm/plex-sans@1.1.0`
    (`ibm-plex-sans.zip`), and the hashes are in `brand/fonts/SHA256SUMS`.
  - `OFL.txt` is the release's `LICENSE.txt` (SIL OFL 1.1), renamed.
  - The Dockerfile's `COPY . .` already includes `brand/`, so it needs no
    change.
  - The tests are in `tests/test_fonts.py`.
- A2:
  - Dropping a component with no cite and no tech match moves to
    `check_architecture` (A6), because the model can't see the case's tech
    stack.
  - Over-long strings are rejected by `max_length`. B1 trims model output
    before building, and the B3 editor reports the line.
  - `cite` is a plain string.
  - `Architecture.text()` feeds `search_text()`.
- A3:
  - The vertical card gap is 22 (plan: 14), so an 18 px pill fits between
    rows.
  - Cards shrink from 230 to 130 so the gutters stay at least 96 wide.
  - Zones with more cards than fit vertically get sub-columns, and the
    platform band wraps.
  - Flows that skip a column use lanes in a channel above the zones.
  - Known gaps, to fix after the A4 pictures are reviewed:
    - pills are dropped when there is under 48 px of room
    - lines can run behind cards in the same zone or platform row
    - platform-lane pills can touch
- A4:
  - pptx flows are freeform polylines with an arrowhead, not elbow
    connectors, because routes bend up to 5 times.
  - `scripts/arch_sample.py` and the fixtures under `tests/fixtures/arch/`
    are added for visual review.
- Visual fixes after review of the A4 pictures:
  - no truncation anywhere
  - zones sized to their content
  - an 8 px grid router (bend penalty, cards as obstacles), with 1.5 px lines
    and rounded corners
  - platform components as compact chips with "Used by" notes
  - shape glyphs in all renderers
  - the plan's type sizes
  - pills on the longest segment, with 2-line or keyed-marker fallbacks
  - `layout()` is cached
  - the Scene gains `shapes`, `Line.refs` and `Box.ref`
- Scale-to-fit (capped at 1.6) and zone alignment:
  - Wide diagrams are limited by width, so some vertical space stays on a
    16:9 canvas.
  - The test for how much of a zone its cards cover drops from 40% to 15%,
    because zones are now all as tall as the tallest one.
- A5:
  - A blocked architecture is omitted from that output with a note. It
    doesn't withhold the whole output.
  - The case-study template's old "Architecture" heading over document
    diagrams is now "Diagrams", under the new Architecture block.
  - Mermaid escaping also covers backticks and newlines.
  - `.paper svg` CSS is added.
- A6:
  - `check_architecture` uses `check_prose(..., heading=True)`, so a
    capitalised first word isn't flagged.
  - Pruning is a separate `prune_architecture()`; the checker never changes
    the case.
- A7:
  - Each of the 9 approved demo cases has an architecture with 8–10
    components, 7–10 flows and 2 callouts.
  - `validate()` runs `check_architecture` before any write.
  - `refresh_architecture()` updates only the architecture and the search
    text of unchanged documents.
  - Litware goes from 6/9 to 7/9 complete. The others were already 9/9.
- Polish:
  - Documents and the preview crop to the content (`cropped()` closes the
    empty bands); pptx keeps the 16:9 canvas.
  - Row heights fit their tallest card (minimum 46), so cards in a row are
    equal.
- User-requested full size:
  - `GET /cases/{cid}/architecture.png` runs `build()` for one case
    (approved, in date, ACL-visible, protected, blocked-checked).
  - Any failure gives the same 404 "no such diagram".
  - It sends `PNG_HEADERS` and `Cache-Control: private, max-age=300`.
  - The preview links it with the `zoom` macros.
