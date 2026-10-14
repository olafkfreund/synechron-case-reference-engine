---
status: draft
issue: 207
author: olafkfreund
---

# Intent: a stalled research fetch frees its thread at the time limit

## Problem

Research fetches each page in a background thread, which the caller stops
waiting for after `TIMEOUT` (10 s). Two kinds of host behave differently:

- **A host that trickles its body** has an open response. The caller closes
  it, and the thread stops at its next read.
- **A host that accepts the connection but never sends headers** leaves
  nothing to close. Its thread stays blocked until httpx's own per-read
  timeout, about another 10 s.

The code marks this as a known shortcut (`app/research.py:397`). It is
bounded: a few idle threads for about 10 s per stalled host. No pile-up has
been observed, since there is no staging yet.

## Proposed outcome

When a fetch passes its time limit, its connection is cut whether or not
headers arrived. The thread ends within moments instead of about 10 s later,
and the shortcut comment goes.

## Affected users and systems

- `app/research.py` (`Fetcher`), used by the research worker.
- Users of online research see no change, apart from fewer lingering
  threads.

## Constraints

- **SSRF protections stay exactly as they are:**
  - the pinned IP
  - the Host and SNI headers
  - `trust_env=False`
  - manual redirects
  - per-hop vetting
- **No new dependency.**
- **Tests use the injected `TRANSPORT`.** A test must show a header stall
  ending at the time limit.

## Open questions

- Fix now or keep it open as watched? The issue says "fix only if threads
  are seen piling up", which hasn't happened. You asked to continue, so this
  intent proposes the fix. The likely shape is small: close the Fetcher's
  connection pool on timeout and open a fresh one. Reject the intent if
  you'd rather keep it open.
