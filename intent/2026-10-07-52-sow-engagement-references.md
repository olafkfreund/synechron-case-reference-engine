---
status: draft
issue: 52
author: olafkfreund
---

# Intent: Signed SOWs and change orders as engagement references

## Problem

The real document corpus is mostly contracts and sales documents, not case
studies. The first folder of 12 presale documents (NatWest, Deutsche Bank,
Morgan Stanley, Comerica, Southern Water, IPC) holds:

- statements of work and change orders
- fixed-price proposals and bid responses
- an assessment proposal

All 12 parse correctly. But triage only turns documents that describe
*delivered* work into reference cases, so almost all of them would be skipped.
The portal would then have little to offer the bid team, even though a signed
SOW is strong evidence that we were engaged for that scope, with that
technology, for that client.

## Proposed outcome

- A signed SOW or change order becomes an **engagement reference**. It holds:
  - client (anonymised as usual)
  - scope and capabilities
  - technology
  - team size
  - duration or period
  It goes through the same quote checking and human approval as today.
- Outcomes stay empty unless the document actually states results. An
  engagement reference never shows invented or contracted targets as
  achievements.
- Outputs and search make the difference visible. An engagement reference is
  labelled as such, distinct from a delivered case with outcomes, so a bid
  never presents contracted scope as a proven result.
- Proposals and bid responses stay as they are today: searchable as related
  documents, and turned into references only if they describe delivered work.

## Affected users and systems

- Bid team and presales (search results and downloads), reviewers (review
  queue), admins (sources).
- Ingest triage and extraction, the case record, search, and all output formats.

## Constraints

- **Unsigned drafts must not count as engagements.** Several files are clearly
  drafts ("draft v.2", "v0.4"). An unsigned SOW is a proposal, not an
  engagement.
- **Commercial terms never become part of a reference:** prices, day rates,
  payment terms, and names of individual people from either side.
- Everything that protects references today still applies:
  - sourced quotes for every field
  - human approval
  - anonymisation
  - access groups
  - re-review after 12 months
- A delivered-case reference and an engagement reference must never be
  confused in an output.
- Real client documents are processed only by approved models. For this
  development run that's a local model (Ollama, `qwen3:14b`). Nothing is
  committed to the repository, and nothing is sent to cloud-hosted models.

## Open questions

- **How do we know a SOW is signed?** Options:
  - the AI detects a completed signature block
  - an admin marks a source or folder as "executed contracts"
  - both, with the admin setting overriding
- **Several SOWs or change orders for one engagement:** should they merge into
  one reference, or stay one reference per document? (NatWest has Phase 0 and
  Phase 1; Deutsche Bank has an SOW and an amendment.)
- **Display:** should engagement references rank below delivered cases in
  search, or alongside them with a clear label?
