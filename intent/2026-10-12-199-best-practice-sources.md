---
status: approved
issue: 199
author: olafkfreund
---

# Intent: Case studies: cite the public best-practice sources used

## Problem

The user wants a case study to list the public sources (standards, vendor
guidance, best-practice articles) behind the approach.

**Much of this exists already.** "Research this (public sources)" on a case
card runs web research (Brave) seeded from the case's solution and
technology. The result has typed claims (`out_of_the_box`, `configuration`,
`industry_practice`, `vendor_claim`), each with a verbatim quote, publisher,
URL and retrieval date. Generating with that research adds the section
"Industry context (public sources)" (`industry_section`, `app/render.py`),
with the disclaimer that these are third-party statements, not our delivery
evidence.

What may be missing:

- the flow is hidden: research first, then download from the research page;
- the heading says "Industry context", not best practice or sources;
- the research is not kept with the case, so it is redone for every output;
- research needs a Brave key (#19), which local and staging don't have yet.

## Proposed outcome

To be settled by the open questions. At least: a user can produce a case
output that lists the public best-practice sources with their URLs, kept
visibly separate from our delivery evidence.

## Affected users and systems

- Bid users; `app/research.py`, `app/render.py`, `templates/search.html`,
  `templates/research_view.html`.

## Constraints

- A public statement is never presented as our delivery evidence (the
  disclaimer and fixed phrases stay).
- The query is previewed before it is sent, and never names a protected
  client (existing).
- The wording is still open in #23.

## Open questions

1. Is today's flow enough once it is easier to find (for example a
   "Include public sources" step on the preview, #197)? Or should a
   reviewer attach approved research to the case, so every output carries
   the same sources without new research?
2. Rename the section heading to "Best practice and public sources"? This
   overlaps with #23.
3. Should this wait until the Brave key (#19) exists, so it can be tried
   against real results?
