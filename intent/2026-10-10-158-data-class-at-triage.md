---
status: draft
issue: 158
author: olafkfreund
---

# Intent: triage uses the source's data class as it is now

## Problem

`ingest()` in `app/ingest.py` reads the source's `data_class` before it
stores the original in S3 and converts it with Docling. It then passes
that early value to `triage_text`, and `complete_json` decides from it
which models are allowed.

Conversion of a large PDF can take minutes. If an admin raises the source
from `public` to `confidential` in that time, triage still runs under
`public`. A cloud model is allowed, and confidential text leaves our
environment. #133 fixes the same race for "contracts executed" and
explicitly leaves the data class out.

Extraction is not affected: `extract()` (`app/extract.py`) reads the data
class when the job runs.

## Proposed outcome

- Triage uses the data class the source has after conversion, not the one
  it had when the crawl picked up the file.
- A document whose source is raised to `confidential` while it converts is
  triaged only by a model `confidential` allows.

## Affected users and systems

- Admins who change a source's data class.
- Owners of confidential documents.
- `app/ingest.py` (`ingest`), and the triage call to `app/llm.py`.

## Constraints

- Never weaken `llm.allowed`. An unknown class still fails closed.
- Don't hold a database lock across the Docling conversion or the model
  call, or an admin's save would wait minutes.
- #133 changes the same lines in `ingest()`, since it moves the config read
  into the write transaction. Whichever lands second rebases on the other,
  so build on #133's shape.

## Open questions

1. **How small must the window be?**
   - **A. Read the class again just before `triage_text`.** A raise that
     lands during the model call itself, a few seconds, still goes out
     under the old class.
   - **B. A, and lock the source row `for share` across the model call.**
     This closes the window, but an admin's save waits for the triage
     call.

   **Recommendation: A.** It closes the minutes-long conversion window,
   which is the real exposure. The seconds-long call window is the same
   one any in-flight request has when a setting changes, and B would make
   admin saves wait on model latency.
