---
status: approved
issue: 117
author: olafkfreund
---

# Intent: Research view shows protected client names in claims and sources

## Problem

The research page (`app/research.py:163-182`, `app/templates/research_view.html`)
prints every claim's quote, AI statement, publisher and URL, and every source
page's publisher and URL, exactly as fetched from the web. Nothing removes a
protected client's name.

The download path already handles this. `render.industry_section`
(`app/render.py:48-67`) runs `apply()` on each quote and then `blocked()` on the
quote, publisher and URL. A claim that still names a protected client is dropped
and counted in a note. The screen gets none of that.

The query sent to the web is scrubbed of client names, but the pages that come
back can still name one: a vendor case study ("Zorp Bank runs Kafka"), or the
client's own site. The name then appears on screen, in the **Comparison** table
right next to **Our approach**, which is that client's case shown anonymised.
Putting the two side by side defeats the anonymisation.

## Proposed outcome

- The research page never shows a protected client's name or alias, in any
  claim field, publisher, URL or source line.
- The page says how many statements or sources were left out and why, as the
  download does.
- The screen and the download agree: a claim left out of one is left out of
  the other.

## Affected users and systems

- Bid team users of the research page: `app/research.py` (`research_view`) and
  `app/templates/research_view.html`.
- `app/render.py` (`industry_section`) and `app/anonymise.py` are reused, and
  ideally not changed.
- Tests: `tests/test_research_claims.py`.
- Not affected: the research worker, the stored results, and outgoing queries.

## Constraints

- Fail closed: `blocked()` decides, after `apply()`.
- Never rewrite a publisher or URL. That would forge the citation, so anything
  that still matches is dropped, not edited (the same rule as `industry_section`).
- Filter at view time, not by changing stored results. The registry can change
  after the research ran.
- Server-rendered, with no JavaScript. Use only made-up names in tests.

## Open questions

1. **Rewrite or drop a claim whose quote names a protected client?**
   - **A. Same rule as the download.** Run `apply()` on the quote and the
     statement. Drop the claim if `blocked()` still finds a name in the quote,
     statement, publisher or URL.
   - **B. Drop every claim that names one at all**, even when `apply()` could
     rewrite the name.

   **Recommendation: A**, so screen and download match.
2. **Source pages:** drop a page whose publisher or URL names a protected
   client, and count it with the skipped sources. **Recommendation: yes.**

## Approved answers

1. A: apply() on quote and statement, then drop the claim if blocked() finds a name in quote, statement, publisher or URL (the download's rule).
2. Yes: drop a source page whose publisher or URL names a protected client, counted with the skipped sources.
