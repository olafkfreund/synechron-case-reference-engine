---
status: approved
issue: 235
author: olafkfreund
---

# Intent: The Library works for you during a bid

Approved in the session on 2026-10-13: the user picked all four
improvements.

## Problem

The Library (#214) is a shelf you have to remember to fill from another page
(Research), and remember to visit. Nothing brings saved research to the place
where a bid is assembled, Find references. A saved brief can't go into a
bid download without re-running research. An empty or growing library also
gives no overview of what it holds.

## Proposed outcome

1. **Suggestions in Find references.**
   - When the bid text or filters match a saved item's topic or visible
     text, a "From the library" panel beside the case results lists the
     matching briefs and quote sets: topic, kind, number of sources, age, and
     a re-check badge.
   - One click adds an item to the download.
   - No model call is made; the matching uses the library's own search
     rules, including the visible-text-only rule from #214.
2. **Research from the Library.** A "Research a new topic" box on the Library
   page starts the normal research flow (query preview, then send). When it
   finishes, the result page offers "Save to library" with the topic
   pre-filled.
3. **Browse by topic.**
   - Topic chips with counts.
   - The newest items first, with who saved each one (the same display name
     the portal uses elsewhere).
   - Filters by kind (brief or quotes) and by re-check due.
4. **Library briefs in bid downloads.**
   - The bid download form (search results and the preview) can include one
     or more saved library items.
   - They are rendered after the cases as cited industry context (brief
     items as brief sections, quote items as the grouped and cited section),
     under the same disclaimer, anonymisation and `blocked()` rules.
   - The preview shows exactly what will be downloaded.

## Constraints

- Public-source content stays visibly separate from Synechron delivery
  evidence, as it is now.
- A library item is re-anonymised every time it's shown or rendered, with
  blocked content dropped and counted.
- The bid text stays in POST bodies, never in URLs.
- The suggestions add no noticeable latency to Find references, and never
  call a model.
