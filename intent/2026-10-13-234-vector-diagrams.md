---
status: approved
issue: 234
author: olafkfreund
---

# Intent: Vector diagrams in documents become diagram candidates

Approved in the session on 2026-10-13: the user chose to convert vector
images so that more of the documents' own diagrams reach case studies.

## Problem

#212 phase C reads only PNG and JPEG images out of docx and pptx files. In the
11 trial presale documents most diagrams are EMF, WMF or SVG (counted from
the zip media entries), so only 1 document yielded a candidate. Architecture
and solution diagrams in Office files are often vector shapes saved as EMF
or WMF.

## Proposed outcome

- **Vector images become candidates.** EMF, WMF and SVG images inside docx
  and pptx are converted to PNG in the worker and offered as diagram
  candidates. They get the same review flow (Use or Dismiss), the same caps
  and the same hint and caption handling as PNG and JPEG.
- **Same rules as phase C.** Nothing reaches an output until a reviewer
  chooses Use. Every converted image is re-encoded through `_decode()`, and
  logos and small images are filtered out.
- **Older documents covered.** Documents ingested before this change get their
  vector candidates through the same backfill as phase C.

## Constraints

- **Hostile files.** Vector files are treated as hostile:
  - caps on file size and count per document
  - conversion with headless LibreOffice (already in the image for PDF
    output), with a timeout, its own temp profile, a minimal environment and
    the existing `PDF_SLOTS` cap
  - no network access during conversion
  - a failure skips that image only
- **Ingest stays safe.** Ingest must never fail or block on conversion, so it
  runs after the document commit, as phase C does.
- **No new dependency** unless LibreOffice can't convert a format. In that
  case the format is skipped and the spec says so.
