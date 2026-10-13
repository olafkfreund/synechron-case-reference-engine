---
status: draft
issue: 231
author: olafkfreund
---

# Intent: Research output with substance, visuals and clean formatting

## Problem

A research result for "MLflow / MLOps", seen in the demo on 2026-10-13:

- **Raw Markdown in quotes.** Quotes are stored verbatim from the page's
  Markdown, which is what the "quote really is on the page" check needs.
  They are also shown that way, so `[debug](https://…)`, `**tracking**`,
  backticks and `""…""` appear in the preview, Word, PDF and Markdown
  outputs.
- **Thin and uneven quotes.** Claims are one short quote each. Some are
  barely relevant to the query, for example "MLflow is part of PyCaret's
  dependency". Nothing ranks them by relevance.
- **No technical substance.** Documentation pages hold examples,
  configuration, steps and code, which a bid team would use as technical
  notes. They are never captured.
- **No visuals.** There are no diagrams or images. Images on public pages
  are other people's copyrighted material and brand assets, so they can't
  simply be copied into a customer document.

## Proposed outcome

1. **Clean quotes.** Quotes display as plain text in every format and on
   every page: link text without the URL syntax, no emphasis or code markers,
   and doubled quote marks fixed. The stored quote stays verbatim, so the
   page check is unchanged.
2. **Longer, better quotes.**
   - A claim's quote may be a short passage (up to a few sentences, capped),
     not just one line.
   - Claims are ranked by relevance to the query and source quality, with
     vendor docs and standards first.
   - Weak or off-topic claims are dropped.
3. **Technical notes.**
   - Verbatim examples from documentation pages (configuration, code, CLI,
     numbered steps) are captured as "technical notes", each with its source
     and date.
   - They are shown in a monospace block on the research page, and can be
     selected, exported, put in a brief, saved to the library and attached
     to a case like quotes.
4. **Images from public pages, only with confirmed rights.**
   - Images on the fetched pages are offered as candidates on the research
     page, as thumbnails with their source.
   - Before an image can reach any output, the user must confirm they have
     the right to use it, and it is always credited to its source under the
     image.
   - Unconfirmed images are only linked, never copied.
   - Every image is decoded again and stripped of metadata (the #200 and
     #212 phase C path).
5. **Generated diagrams.**
   - A simple architecture or flow diagram is drawn by us from the cited
     facts, for example "CMDB → feeds → SAM, HAM; ITAM depends on …". Every
     box and arrow traces to a quote.
   - The reviewer or user can edit the diagram before it is used.
   - It goes in briefs, the library and case studies as our own artwork,
     with its sources listed.

## Affected users and systems

- Bid team (research, briefs, library) and reviewers (case attachments).
- `app/research.py` (fetching, claims, images), the research page,
  `app/render.py` (quote display, notes, images, diagrams), `app/brief.py`,
  `app/library.py`, and the case-study output.

## Constraints

- Public sources only. Everything stays cited, and no quote or note is
  rewritten (cleaning is display-only).
- No third-party image reaches an output without a recorded usage-rights
  confirmation and a source credit.
- Fetching images is bounded (size, count, types), treats them as hostile,
  and never follows tracking or redirect tricks. This follows the existing
  fetcher rules.
- Anonymisation and `blocked()` apply to everything new, including image
  alt text and diagram labels.
- The local model must stay usable. Ranking and diagram drafting must not
  blow the context (#217).

## Open questions

1. **Who confirms image rights:** the user who ran the research, or only
   reviewers?
2. **Diagram format:** generate Mermaid-like text rendered to PNG by us, or
   SVG? The spec proposes the lightest one that Word and PDF accept.
3. **Order:** clean quotes first (a quick fix), then better quotes and
   notes, then images, then diagrams?
