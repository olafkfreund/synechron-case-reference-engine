---
status: draft
issue: 212
author: olafkfreund
---

# Intent: Case studies rich enough to present to a customer

## Problem

On 2026-10-13 we crawled 11 real presale documents into the local demo stack.
Extraction ran on the local model, `qwen3:14b`. The 2 cases it produced (16
and 17) are too thin to put in front of a customer:

- **Short sections.** Challenge, solution, implementation and configuration
  each hold one sourced value of 1 to 3 sentences. The extraction prompt asks
  for exactly that: "a short value", and "1 to 3 sentences each"
  (`app/extract.py`, `SYSTEM` and `CONTRACT_SYSTEM`). A bid needs paragraphs:
  the client's situation, the approach and its phases, how the platform was
  configured and integrated.
- **Empty summaries.** The summary came back empty on both cases.
- **Gaps in the facts.** Case 16 has no capabilities and no technology, and
  case 17 has no solution or configuration value, although the documents are
  37k-98k characters long.
- **No diagrams from the documents.** #200 lets a reviewer upload a diagram by
  hand. Architecture diagrams already inside the source docx and pptx are
  never offered.
- **No technical references.** These would be the products, versions,
  standards and integration points the work relied on, and #199's public
  sources for them, presented as a reference list.
- **The output reads as a form, not a document.** It is a set of short fields
  per case. It doesn't read as a case study on its own, or as an appendix to a
  bid.

The rule that makes this engine trustworthy still holds: every statement
traces to a verbatim quote in a source document, and the model never writes
from general knowledge (#198). "More text" must not break that rule.

## Proposed outcome

- **Fuller sections.** Challenge, Solution, Implementation approach and
  Configuration and integration each read as one or more customer-ready
  paragraphs, when the documents hold that much. Every sentence is still backed
  by quotes the reviewer can check, and a section the documents don't support
  stays short or empty.
- **Diagrams from the documents.** Architecture and solution diagrams embedded
  in the source documents are offered to the reviewer, who picks which go in.
  Hand uploads (#200) keep working.
- **Technical references.** A case has a section listing the technologies,
  standards and integration points the documents name, plus any public sources
  attached under #199.
- **A presentable document.** A case can be downloaded as a standalone,
  multi-page case study (cover, sections, diagrams, references) and as an
  add-on section to drop into a bid. The preview (#197) shows exactly what
  will be downloaded.
- **Measured improvement.** Re-running the same 11 documents gives visibly
  fuller cases. A before/after on cases 16 and 17 is the check.

## Affected users and systems

- Bid team and reviewers, who will see longer sections to review.
- `app/extract.py`, `app/schema.py` (`ReferenceCase`), the review page,
  `app/render.py` and its templates (`brand/reference.docx`,
  `brand/master.pptx`), the diagrams store (#200) and Docling conversion
  (images).
- The extraction model. Locally that is `qwen3:14b` (with a 24k-token
  context); in AWS it will be Bedrock. Longer output costs more tokens and
  time per document.

## Constraints

- Every sentence shown to a customer comes from the source documents or the
  reviewer, never from the model's general knowledge. Unsourced text is
  flagged, as today.
- Anonymisation (`apply()`, `blocked()`, `protect()`) and the commercial
  filter (`strip_commercial`) apply to all the new text and to diagram
  captions. Diagram images can contain client names and logos, so a reviewer
  must approve every image before it can reach an output.
- Confidential documents still only go to `local` or `our-cloud` models.
- Existing approved cases keep working, and re-extraction must not wipe a
  reviewer's edits (#198 `by_reviewer`).

## Open questions

1. **How "more text" stays sourced.** Option (a): extract more sourced points
   per section and join them into paragraphs. Option (b): add a second
   "write-up" pass that turns only approved, sourced facts into
   customer-ready prose, which the reviewer edits and approves. (b) reads
   better; (a) is simpler to check. The spec chooses, unless you have a
   preference.
2. **Standalone, add-on, or both.** Is the standalone case study a new Word
   template from marketing (#15), or our generated layout until theirs
   arrives?
3. **Presale documents.** 9 of the 11 documents were skipped by design:
   unsigned contracts and proposals are not delivery evidence. Is this work
   only about the cases that pass triage, or should proposals also become a
   different, clearly labelled "capability" document?
4. **Which model counts.** Is the local model's quality the target, or is
   Bedrock the target and the local trial only a smoke test? This decides how
   much to tune prompts for `qwen3:14b`.
