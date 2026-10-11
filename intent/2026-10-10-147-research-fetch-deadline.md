---
status: approved
issue: 147
author: olafkfreund
---

# Intent: a slow web server cannot hold the research worker

## Problem

`Fetcher._get` in `app/research.py` gives each fetch a 10-second
`deadline`, but checks it only between body chunks. The httpx `timeout`
is per socket read, so a server that sends one header byte every few
seconds never trips either. The status line and headers can trickle for
hours, and a robots.txt fetch is the same path.

Meanwhile the worker's heartbeat (`app/worker.py`) keeps the job marked
alive, so it is never treated as stale. There is one worker, so every
other job, crawls and extracts included, waits behind it. #45 bounded
Docling conversion, but not the fetch.

## Proposed outcome

- Every single HTTP request in research, headers and body, robots.txt
  included, ends within a fixed wall-clock limit (today's 10 seconds),
  however slowly the server sends.
- A page that hits the limit is reported like any other failed page, and
  the run carries on with the others.
- A research job as a whole finishes in bounded time, so the queue keeps
  moving.

## Affected users and systems

- Bid team members running online research.
- The worker, and every job queued behind a research job.
- `app/research.py` (`Fetcher._get`, `allowed`, `fetch`).

## Constraints

- Keep the SSRF protections exactly as they are: the pinned IP, Host and
  SNI, manual redirects, and vetting every hop.
- No new dependency.
- A timeout must not count as "robots.txt unreachable, so allowed". The
  current fail-closed rule for robots stays.
- Tests must use the existing `TRANSPORT` injection and must not touch
  the network.

## Open questions

None.
