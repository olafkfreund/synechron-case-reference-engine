---
status: draft
issue: 23
author: olafkfreund
---

# Intent: The industry context wording claims no more than the quote

## Problem

Each item in the "Industry context (public sources)" section starts with one of
four fixed phrases from `PHRASES` in `app/render.py`:
"Supported out of the box", "Available through configuration",
"Common industry practice" or "Vendor statement". The verbatim quote,
publisher, URL and date follow it.

The phrase comes from a type that the AI picks for each claim
(`out_of_the_box | configuration | industry_practice | vendor_claim`). The
quote is checked word for word against the source; the type is not. So the
phrase can say more than the quote does. For example, a vendor's marketing line
can be labelled "Supported out of the box". In a bid document, Synechron would
then be stating something its source does not back.

Plan step 20 kept the typed phrases instead of a reviewer's neutral
"X states:". #23 revisits that, and the user has now chosen neutral wording.

## Proposed outcome

- Every industry statement, in Word, PowerPoint, Markdown, PDF and the output
  preview, is introduced by its source, in the form "Microsoft states:", then
  the verbatim quote, URL and date. No output contains an AI-chosen strength
  label any more.
- The quote, the citation and the anonymisation rules are unchanged: only the
  quote is anonymised, and a claim that still names a protected client is
  dropped and counted.
- Plan step 20 records the decision (#23).

## Affected users and systems

- Bid team and readers of generated outputs: the wording of the industry
  context section.
- `app/render.py` (`PHRASES`, `industry_section()`, `slide_statements()`, the
  Markdown template), `app/templates/output_preview.html`, and the tests that
  read the phrases (`tests/test_industry.py`).
- Outputs generated before the change keep their old wording. They are not
  regenerated.

## Constraints

- The publisher and URL must never be rewritten: changing them would forge the
  citation.
- The verbatim quote stays next to the source in every format, slides included.
- The publisher must not appear twice in one line.
- No AWS or Bedrock is needed to build or test this.

## Open questions

- Whether the AI should still type claims. The type filters out claims with no
  valid type (`render.py:55`) and may be used elsewhere. The spec decides
  whether to keep it as a filter only or drop it.
