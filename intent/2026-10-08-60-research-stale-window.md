---
status: draft
issue: 60
author: olafkfreund
---

# Intent: A slow research job is not run twice

## Problem

The worker treats a running job as abandoned once its `updated_at` is older
than a stale window, and lets another worker claim it (`app/worker.py`
`CLAIM`, line 22). Crawls get 6 hours; every other job, research included,
gets 15 minutes.

A research job (`app/research.py` `run()`) can take longer than that:

- up to 8 pages (`MAX_RESULTS`), each with up to 3 redirects. Every hop has a
  10 s read deadline plus connect time, and each new host also fetches
  robots.txt the same way. The network alone can reach roughly 10 minutes;
- each page goes through Docling (`ingest.to_markdown`), PDFs up to 40
  pages, on CPU, with no time limit;
- then one LLM call for the claims.

Nobody has measured a worst-case run. If one passes 15 minutes, a second
worker claims it and starts again: a second Brave call, the same page
fetches again, and a second LLM call, while the first is still running. That
breaks plan step 18 of #1 (no retries against external sites) and doubles
the cost. Whichever finishes last writes the row; the `attempts` guard in
`finish()` stops only the first worker's job status, not its research row.

Found in the #36 review.

## Proposed outcome

- A research job that is still running is never claimed by a second worker.
- A research job whose worker really died is still picked up again, within a
  bounded time.
- The worst-case runtime of a research job is measured and recorded on the
  issue.

## Affected users and systems

- `app/worker.py` (claim), possibly `app/research.py`.
- The worker on ECS. Bid team users of industry research: a dead worker's
  job may take longer to be retried, depending on the design.

## Constraints

- No retries against external sites (#1 step 18): a live job must not be
  repeated.
- Extract and other short jobs keep their 15-minute recovery.
- The measurement must not send confidential documents anywhere; research
  queries are public-web searches, so a made-up query is fine.
- No new dependency.

## Open questions

1. Longer fixed window for research (simple, but a dead worker's job waits
   that long), or a heartbeat that refreshes `updated_at` while the job runs
   (recovers fast, more code)? I lean to the fixed window, sized from the
   measurement, unless the measurement shows the worst case is unbounded
   (Docling on a big PDF); the spec decides.
2. Should a research job also get its own time limit, so it can't run
   forever? Out of scope unless the measurement says otherwise.
