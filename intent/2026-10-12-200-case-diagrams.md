---
status: draft
issue: 200
author: olafkfreund
---

# Intent: Case studies: diagrams

## Problem

Case outputs (Word, PowerPoint, PDF, Markdown) are text only. Bid teams want
an architecture or process diagram in a case study. Nothing in the case
record, the review page or the templates can hold or show an image.

## Proposed outcome

An approved case can carry one or more diagrams. The diagrams appear in Word,
PowerPoint, PDF and the preview (#197). The Markdown output links to or
embeds them.

## Affected users and systems

- Reviewers (who would add or approve diagrams), bid users.
- `sql/schema.sql` (storage), S3 (image objects), `app/review.py`,
  `app/render.py` (python-docx and python-pptx can insert pictures), the
  templates.

## Constraints

- **Anonymisation cannot check an image.** `protect()` scans text only, so a
  diagram with a client name or logo would leak. A diagram must be checked by
  a person, or be generated only from text that has already been anonymised.
- Confidential content goes only to the local or our-cloud model.
- Nothing is fetched from the internet when an output is rendered (the
  template rule in `render.py`).
- No JavaScript in the app.

## Open questions

1. **Where does a diagram come from?** The options, from safest:
   a. **Reviewer upload (recommended first step):** the reviewer uploads a
      cleaned PNG or SVG on the review page. Approval confirms it names no
      client. Simple, and a person checks it.
   b. **Generated from the approved fields:** the local model writes a
      Mermaid or Graphviz diagram from the anonymised solution and
      technology. Rendering needs a new tool in the image (Graphviz is a
      single package; Mermaid needs Node and a headless browser). The model
      may draw things the documents don't say, so the reviewer must still
      approve it.
   c. **Taken from the source documents** (Docling can extract pictures).
      These are the client's own, often branded, so this is the riskiest.
2. One diagram per case, or several with captions?
