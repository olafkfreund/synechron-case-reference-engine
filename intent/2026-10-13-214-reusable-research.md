---
status: approved
issue: 214
author: olafkfreund
---

# Intent: Research we can reuse as our own content

## Problem

Online research (`app/research.py`) runs a scrubbed web search and fetches the
pages. It then keeps only the claims whose quote really appears on the page,
with publisher, URL and retrieval date. That evidence is sound, but it stops
there:

- **The download is a raw quote dump.** In the 2026-10-13 trial, the
  "Industry context" preview and Word download are one flat bullet list. Each
  bullet holds a label, the full quote, the host and the full URL inline
  (often over 100 characters), and the date. There are no themes, no
  takeaways, no reading order, and nothing that reads as a document. Nobody
  could hand it to a customer or paste it into a proposal.
- **No content of our own.** The page lists quotes, an AI one-line reading of
  each, and the type, an AI label. Nothing turns them into text we could put
  in a proposal. The only output is the "Industry context" download, which
  repeats the quotes as a list.
- **The actions are hard to find.** Preview and Download sit below the
  Comparison table and the long quote list. In the 2026-10-13 trial they
  weren't found at all.
- **Nothing is kept.** A research row belongs to the user who ran it
  (`research.created_by`). It can't be searched, and the next bid on the same
  topic starts again from zero.
- **Research only reaches a case** when it was started from that case
  (#199's Attach needs `research.case_id`). Research started from the
  Research page can't be attached to anything.
- **No simple copy.** You can't select a few quotes and take them, with their
  citations, into a document of your own.
- **Leftover AI labels.** The research page still shows the AI-chosen type
  label ("industry practice"), which #23 removed from outputs.

## Proposed outcome

- **Best-practice brief.** From a research result, the user picks the quotes
  they want and gets a readable brief, for example "ITAM on ServiceNow: CMDB
  first, then SAM and HAM", written only from those quotes, with a numbered
  citation after each claim and a source list (publisher, URL, retrieved
  date). They can edit it before downloading it as Word, PDF or Markdown. A
  sentence, number or name that no selected quote supports is flagged, as in
  #212's write-up checks.
- **Formatted like a document.** Every research output (the brief, and the
  "Industry context" section in bid outputs) has:
  - a title
  - a short "Key takeaways" list
  - the statements grouped by theme, for example "CMDB as the foundation" and
    "How SAM and HAM work together", each with a lead sentence and its quotes
  - numbered citations [1] in the text, in place of inline URLs
  - a numbered source list at the end (publisher, page title, URL, retrieved
    date)

  This applies to Word, PDF, PowerPoint (one theme per slide) and Markdown,
  and the preview shows the same.
- **Knowledge library.** A brief, or a set of chosen quotes, can be saved
  under a topic, for example "ServiceNow ITAM" or "CMDB". Saved items are
  searchable and visible to the team, so the next bid finds them without
  running the search again. Every item keeps its sources and retrieval dates,
  and shows its age.
- **Feeds our case write-ups.** Saved items or research quotes can be
  attached to any case, not only the case the research started from. A case
  study (#212) can then carry an "Approach and industry practice" passage
  citing them, clearly separate from our own delivery facts.
- **Copy and export.** Selected quotes can be copied, or exported as Markdown
  or CSV, with their citations.
- **A clearer research page.** The actions (Brief, Save, Attach, Export,
  Download) sit at the top. The AI-typed label is gone. The AI one-line
  reading is shown as the AI's reading, under the quote.

## Affected users and systems

- Bid team (users), and reviewers, who attach to cases.
- `app/research.py`, `research_view.html`, a new library page and table in
  `sql/schema.sql`, `app/render.py` (brief output), and `app/review.py`
  (attach from anywhere). It reuses #212's write-up pass and prose checks.

## Constraints

- Public sources only, as today. The brief adds no facts beyond the selected
  quotes, and every claim carries its citation.
- Quotes are never rewritten. Publisher and URL are never changed (that would
  forge a citation). Anonymisation and `blocked()` still apply to everything
  saved or downloaded: a query or quote naming a protected client must not
  land in the shared library.
- Nothing in a brief may present industry practice as Synechron's delivery
  evidence. The disclaimer stays on every brief and every case passage.
- The write-up model follows the data-class policy: research is `public`.

## Open questions

1. **Library visibility.** Visible to all users, or to the saver's groups
   (like sources' `acl_groups`)? The content is public, but the topics reveal
   which bids we are working on.
2. **Freshness.** Should saved items expire or ask for re-checking after a
   period, for example 12 months, since vendor pages change?
3. **Order.** This builds on #212 phase B (the write-up pass). Ship #212 A-B
   first, then this. Or ship the cheap parts first: the page fixes and
   export.
