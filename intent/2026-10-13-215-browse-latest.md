---
status: approved
issue: 215
author: olafkfreund
---

# Intent: Open on what we have: latest cases, documents and their state

## Problem

Reviewed in the demo portal on 2026-10-13:

- **Search opens empty.** `GET /search` (`app/search.py`, `search_form`)
  renders only the form. Nothing is listed until you paste a bid text or type
  a filter, so a user can't see what the library holds or browse it.
- **Filters are guesses.** Industry, Region and Technology are free-text
  boxes matched as substrings (`search()`). You have to guess the words the
  cases use ("banking" or "Retail banking"?), and a wrong guess silently
  returns nothing. There is no filter for the kind of case (delivered or
  contracted engagement), and no sort.
- **Every search goes through the model.** Even a filter-only search runs the
  AI pick (`pick()`, `DRAFT_MODEL`). That takes seconds to minutes on the
  local model, which is slow when all you want is a list.
- **Nobody can see what was crawled.** After the trial crawl of 11 documents,
  nothing outside the admin Sources counts and the review queue showed it:
  - which documents arrived and when
  - which became cases, and which are waiting for review or approved
  - which were skipped and why: 6 "contract not executed", 3 "proposal, not
    delivered work", 1 "unsupported .doc"

  The skip reasons exist in the pipeline but are never shown.
- **The home page is static.** `landing.html` is a set of links. It shows
  nothing new: no recent cases, no research, no count of cases waiting for
  review.

## Proposed outcome

- **Search opens on a list.** The approved, in-date cases this user may see
  (the same visibility rules as search), newest first. Each row shows title,
  client label (anonymised as today), industry, region, technology chips, the
  kind badge, and the approval date, with Preview and Download in the row.
  The list is paged.
- **Filters you pick, not guess.** Industry, Region, Technology and Kind are
  chosen from the values that exist in the visible cases, with counts, for
  example "Banking (4)". Choosing a filter narrows the list straight away,
  with no model call. Sort by newest, title or industry.
- **Bid text is the smart search.** Pasting a bid requirement still ranks the
  list and runs the AI pick ("Why it fits", suggested wording), as today. The
  filters apply on top.
- **A "Latest documents" view.** For reviewers and admins, the most recently
  crawled or uploaded documents across the sources they may see. Each shows
  its source, when it arrived, and one plain status:
  - Waiting for extraction
  - Case waiting for review (with a link)
  - Approved (with a link)
  - Skipped, with the reason in plain words: "contract not marked executed",
    "proposal: not delivered work", "file type not supported", "too large"
  - Failed

  Skipped contracts link to the source's "All contracts here are executed"
  setting, so an admin can act on them.
- **A home page that shows what is new:** the latest approved cases, your
  recent research, and, for reviewers, how many cases are waiting.

## Affected users and systems

- All users (search and home), and reviewers and admins (latest documents).
- `app/search.py`, `app/templates/search.html`, `landing.html`, `base.html`
  (navigation), and a new latest-documents route and template. The pipeline
  must record why a document was skipped (`app/ingest.py` triage) and show it.

## Constraints

- **Visibility is unchanged.** A user only sees cases and documents allowed by
  their groups (`VISIBLE`, `acl_groups`). Users see approved cases only.
  Document titles, which can contain client names, are shown only to
  reviewers and admins, and go through the same anonymisation as review.
- The bid text stays in POST bodies, never in URLs or logs. Browsing and
  filtering can use GET, because filter values are not confidential bid text.
- Browsing and filtering never call a model.
- The list must stay fast with a few thousand cases: indexed queries and
  paging, no full load into memory.

## Decisions (2026-10-13, approved with the intent)

1. Plain users see approved cases only. "Latest documents" is for reviewers
   and admins.
2. #215 ships first. Case rows pick up #212's prose when it lands.
