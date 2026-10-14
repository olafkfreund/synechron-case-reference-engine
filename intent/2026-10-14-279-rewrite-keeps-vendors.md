---
status: approved
issue: 279
author: olafkfreund
---

# Intent: research keeps the vendor and product a question is about

## Problem

Before research searches the web, the local model rewrites the question into
a short query. Its prompt (`app/research.py:84`) says to "remove every
client, company, person, project and deal name". A technology vendor is a
company, so the product being asked about goes too.

In the #33 local pre-check, 2 of 10 questions came back about the wrong
vendor:

- "Can Snowflake replicate databases across regions…" was searched as
  `database replication cross region cloud provider`. The claims were about
  AWS and AlloyDB.
- "Does Amazon S3 support write-once-read-many…" was searched as
  `object storage write once read many compliance retention`. The claims
  were about Azure Blob.

A bid writer asking what a named product can do gets claims about someone
else's product. Every quote is real, so this is hard to spot.

## Proposed outcome

A question about a named vendor or product keeps that name in the previewed
query, and its claims are about that product. Client, person, project and
deal names are still removed, as today.

## Affected users and systems

- `app/research.py` (`SYSTEM` prompt, `build_query`)
- users of online research, the bid team

## Constraints

- **The client protections stay exactly as they are.** The registry scrub,
  the identifier stripping and the fail-closed check in `finalize()` are
  unchanged.
- **The preview is still exactly what is sent.**
- **Local Ollama only**, with no model change.

## Open questions

- **A client that is also a vendor.** Microsoft, SAP or Amazon could be the
  client in a bid. If it's in the registry, the scrub still removes it,
  though it then also removes the product question about it. If it isn't
  registered, the rewrite would now keep it. Is that acceptable, given the
  registry is the source of truth for protected clients? The alternative is
  a registry flag such as "also a vendor", which is more scope.
