---
status: approved
issue: 246
author: olafkfreund
---

# Intent: Architecture diagrams a bid team can show a customer

## Problem

The diagrams on case studies can't be put in front of a customer.

- **The demo case diagrams** are the case's tech-stack list drawn as a chain
  of plain boxes, for example "Azure → Spark → Python → Kubernetes"
  (`scripts/seed_demo.py:48-60`, `:184-187`).
  - They have no components, roles, data flows, groupings or integration
    points.
  - The caption repeats the list.
  - The text is tiny, and the image is a low-resolution PNG that blurs on a
    slide.
- **Diagrams taken from source documents** (#212 C, #234) are only as good as
  whatever the document contained, and many documents have none. The trial
  folder gained no usable vector diagrams.
- **The planned generator** in #231 phase (d) would draw the same kind of
  layered boxes from public quotes, so it would miss the bar the same way.

What a sales slide needs is a **solution architecture**:
- the client's channels or users
- the main components we built or configured, each with its role
- the systems we integrated with, and how (API, events, batch, file)
- the data stores and the platform it runs on
- the flows between them, labelled
- all grouped into clear zones, in our brand style

## Proposed outcome

- **A diagram that comes from the case, not just its tech list.** It is built
  from the approved case:
  - its solution, implementation and configuration points
  - its technical references (standards, interfaces, products with versions)
  - its tech stack

  Every component and every flow label traces back to a sourced point or
  reference, so the diagram claims nothing the case doesn't support. This is
  the same rule as the written sections (#212 B, `check_prose`).
- **Content with substance:**
  - **Zones:** typically Channels / users, Experience, Integration, Core and
    data, Platform and ops, and External systems.
  - **Components:** a name plus a short role, for example "Kafka · event
    backbone for claims status".
  - **Flows:** labelled with what moves and how, for example "claims events
    (Kafka topic)" or "nightly batch (SFTP)".
  - **Callouts:** an optional 1–3 for outcomes or numbers, each tied to its
    sourced outcome.
- **Looks professional:**
  - brand colours and fonts
  - readable text at slide size
  - a clean layered layout with no crossing spaghetti
  - consistent shapes per component type, and a small legend
- **Works in every output:**
  - Word and PDF: a sharp vector image (SVG or EMF), with PNG only where a
    format needs it.
  - PowerPoint: **native shapes**, so the bid team can edit the diagram on
    the slide.
  - The preview shows exactly what the downloads show.
- **The reviewer stays in charge.** The diagram is proposed when the case is
  drafted, then shown on the review page next to its sources. The reviewer
  can:
  - edit components, zones and flows in a simple structured editor or text
    form
  - regenerate it
  - remove it

  Only an approved diagram reaches an output, as today. Flags such as "names
  'Snowflake', which no point supports" block approval until fixed or
  overridden.
- **The demo shows the real thing.** The chain-of-boxes placeholder goes, and
  each demo case gets a proper fictional architecture spec in
  `demo/cases.json`, drawn by the same renderer.

## Affected users and systems

- **Users:** bid teams and sales, who download decks and case studies, and
  reviewers, who approve diagrams.
- **Code:**
  - `app/diagrams.py`
  - the case schema (a structured `architecture` field)
  - the extraction and write-up step that proposes it
  - review: page, edit and check
  - `app/render.py`, for docx, pptx, pdf, md and the preview
  - `scripts/seed_demo.py` and `demo/cases.json`
  - the brand templates
- **#231 phase (d)** (generated diagrams for research briefs) is paused. It
  should reuse this renderer and model rather than build its own.

## Constraints

- **Sourced, not invented.** Every component and label must be backed by a
  quote. Client names go through anonymisation and `blocked()`, as all other
  text does.
- **Models.** Locally, `qwen3:14b` only, and never a `-cloud` model. The
  diagram proposal is one small call over the sourced points, not the whole
  document.
- **No heavy dependencies in the worker.** No headless browser and no
  Node/mermaid-cli. A small, pinned layout library or our own layered layout
  is fine if the spec justifies it.
- **Editable by people.** The structure lives in the case's JSON, so a
  re-extraction keeps a reviewer's edits (the `by_reviewer` rule).
- **Confidentiality.** Nothing from the real presale documents appears in
  commits, fixtures or screenshots.

## Open questions

1. **Rendering approach.** I'd propose our own SVG renderer with a fixed
   zone layout (zones as lanes, components placed in their zone, orthogonal
   flow lines), plus native python-pptx shapes for slides. The alternative,
   Graphviz `dot`, is one system package in the image and lays out freely,
   but it's harder to make look on-brand.
2. **Who proposes the first version.** The model drafts it from the sourced
   points and the reviewer edits it, as I propose. Or reviewers draw every
   diagram themselves.
3. **Document diagrams (#212 C).** Keep "Use" for real diagrams taken from
   the source documents alongside the generated one, as I propose. Or show
   only one diagram per case.
4. **Placeholder diagrams now.** Should I stop seeding the chain-of-boxes
   placeholders today, so they leave the demo before this ships? It's a
   one-line change to `demo/cases.json` plus a demo refresh.

## Decisions (user, 2026-10-13: "approved all, recommendations as proposed")

1. **Renderer:** our own SVG lane layout plus native python-pptx shapes. No
   Graphviz.
2. **First draft:** the model proposes it from the sourced points, and the
   reviewer edits it.
3. **Document diagrams:** "Use" for source-document diagrams stays, next to
   the generated diagram.
4. **Placeholders:** the chain-of-boxes placeholders stop being seeded now,
   ahead of this work.
