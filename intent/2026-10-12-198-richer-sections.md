---
status: approved
issue: 198
author: olafkfreund
---

# Intent: Richer case studies: implementation, configuration and retrospective

## Problem

A case is extracted from its source documents into `ReferenceCase`
(`app/schema.py`). Its fields are title, client, industry, region, engagement
type, challenge, solution, capabilities, technology, outcomes, duration, team
size and period, plus an AI summary of at most 80 words.

Every field except the summary carries a verbatim `source_quote`. That quote
is checked against the document text (`sourced()`), and approve drops
anything unsourced. Outputs (`render.section`) contain only these approved
fields.

Bid teams want fuller case studies. They want to say how the solution was
implemented, how the platform was configured, and the retrospective
conclusions (what worked, what we would do differently), in descriptive
text, not one line per field. The schema has no place for any of this, so
it is never extracted, reviewed or rendered.

## Proposed outcome

- A case can hold implementation, configuration and lessons-learned
  sections, extracted from its source documents with source quotes like the
  other fields.
- A reviewer sees, edits and approves them on the review page like the other
  fields.
- When a document says nothing about one of them, the section stays empty
  and is left out of the output. It is never written by the model from
  general knowledge.
- Word, PowerPoint, PDF, Markdown and the preview (#197) show the approved
  sections.

## Affected users and systems

- Reviewers (more to check per case), bid users (richer outputs).
- `app/schema.py`, `app/extract.py` (prompt and checks), `app/review.py` and
  `templates/review_detail.html`, `app/render.py` and the Word, PowerPoint and
  Markdown templates, `docs/user-guide/`.
- Existing cases: they have none of the new fields until they are extracted
  again.

## Constraints

- Delivery evidence only: every sentence must be backed by a source quote
  from the case's own documents. Nothing is invented. Public best practice is
  #199.
- Confidential documents go only to the local or our-cloud model (Ollama,
  Bedrock).
- Anonymisation and the fail-closed `protect()` apply to the new text.
- Engagement cases (contracted scope, not delivered) must not claim results.
  A "retrospective" on an undelivered contract is a result claim.
- The PowerPoint slide layout has limited room. Long text must be cut down
  (`excerpt`) or moved to more slides.

## Open questions

1. **Will the source documents contain this?** Contracts, SOWs and closure
   reports rarely state configuration or lessons learned. If they don't, the
   sections will mostly be empty. Should the reviewer be allowed to *write*
   these sections by hand, marked as "added by reviewer, no document quote"?
   That is a new kind of fact the approval rules don't have today.
2. Re-extract the existing cases (one local model call each, and they go back
   to review)? Or fill the new fields only on the next document change?
3. Which sections exactly? Proposed: Implementation approach, Configuration
   and integration, Lessons learned. "Media text and descriptions": is that
   longer descriptive text in these sections, or something else (captions,
   images)?
4. Raise the summary's 80-word limit, or keep the summary short and put the
   detail in the new sections (recommended)?
