---
status: approved
issue: 133
author: olafkfreund
---

# Intent: Toggling "contracts executed" during a crawl is lost for documents being converted

## Problem

`ingest()` reads the source's `config` and `data_class` (`app/ingest.py:88`)
before the long Docling conversion and triage step. It then decides the basis
from that config (`:98`).

An admin may tick **All contracts here are executed** while a contract is being
converted:

- `QUEUE_CONTRACTS` (`app/sources.py`) can't see the document yet, because its
  row isn't written.
- `ingest()` then decides with the old config and queues nothing.

So the contract never becomes an engagement, and later crawls skip it on its
checksum.

Unticking in that window goes wrong the other way. It still queues a "source
marked executed" job after `DROP_FLAGGED_JOBS` has already run.

#58 moved the ACL groups read into the write transaction, under
`for share`. The config read was left behind.

## Proposed outcome

The basis is decided from the source's config as it is when the document row
is written, under the same lock an admin's save takes. A toggle during a crawl
then applies to every document, whichever side of it the document lands on.

## Affected users and systems

- `app/ingest.py` (`ingest`).
- Admins who toggle the flag, and reviewers of engagement cases.
- Tests: `tests/test_ingest.py`.

## Constraints

- Triage keeps running outside the transaction, because it is long and calls
  the model. Only the config read and the basis decision move.
- `data_class` decides which models may read the text during triage, so it
  must still be read before triage.

## Open questions

None. The config is re-read in the write transaction from the
`for share`-locked source row, and `basis` and `reason` are computed there.
