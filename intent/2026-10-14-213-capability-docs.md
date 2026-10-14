---
status: approved
issue: 213
author: olafkfreund
---

# Intent: Presale documents as clearly labelled capability documents

## Problem

Triage keeps only delivery evidence. `basis_for` (`app/ingest.py`) returns:
- `delivered` for case studies, and for proposals or decks that describe
  delivered work
- `engagement` for executed contracts
- `None` for everything else, which is never extracted

In the 2026-10-13 trial, 9 of the 11 real presale documents were skipped:
proposals, unsigned SOWs and capability decks. They are stored and
searchable by nobody.

The bid team wants that material usable, because it describes what
Synechron *offers*: approach, accelerators and team shape. It must never
be mixed with delivered references, or presented as evidence of delivered
work. This was decided in #212, open question 3.

## Proposed outcome

- **A third, separate kind: "Capability".** Proposals, decks and unsigned
  SOWs that are not delivery evidence become capability documents. They
  are extracted into a reduced shape: offering, approach, capabilities,
  technologies, and team shape. They never get outcomes, metrics,
  "delivered for" wording or client results.
- **Always labelled.** A capability document says "Capability: not
  delivery evidence" everywhere it appears: search results, preview, every
  output format, and MCP.
- **Not in reference search by default.**
  - Reference search keeps showing delivered and engagement cases only.
  - Capability documents appear only when the user picks the
    "Capability" kind, or on a separate tab.
  - They are never counted in "references found".
- **The same controls as cases:** client names anonymised by the
  registry, a review and approval step, ACLs from the source, and the
  12-month re-review.
- **In outputs.** A bid pack can include a capability section, under its
  own heading and with the label. It never sits inside "Reference cases".

## Affected users and systems

- Bid teams (search, outputs) and reviewers (a new review shape).
- Code:
  - `app/ingest.py` (`basis_for`, triage)
  - `app/extract.py` (a reduced extraction pass)
  - `app/schema.py` (a `basis = "capability"` shape)
  - `app/search.py` (the kind filter, kept out of default results)
  - `app/render.py` (labels and a separate output section)
  - the review templates
  - the MCP tools (the kind filter and the label)
- The presale source: re-triage turns the skipped documents into capability
  drafts.

## Constraints

- **Never delivery evidence.**
  - No outcome or metric fields.
  - The prose writer is told the text describes an offering, not a result.
  - Existing tests that pin "proposals are not extracted" are kept for
    the delivered and engagement paths.
- **Anonymisation and the commercial strip apply as today:** no prices or
  rates.
- **Evaluated** on the 11 presale documents, counts only: how many become
  capability drafts, unsupported flags, and leaked commercial terms (must
  be 0).
- **Data rules unchanged.** The presale documents are confidential:
  counts only, and local `qwen3:14b` only.

## Open questions

1. **Unsigned SOWs.** A signed SOW is an engagement, and a proposal is a
   capability document. Proposed: an unsigned SOW is a capability document
   (scope offered, not delivered).
2. **Visibility.** Proposed: a "Capability" option in the kind filter,
   plus a "Capability documents" line under the results, with a count and a
   link. Nothing mixed in.
3. **Outputs.** Proposed: a capability document can be added to a bid pack
   only as a separate "Our capability" section. The case-study and
   one-pager formats stay reference-only.
