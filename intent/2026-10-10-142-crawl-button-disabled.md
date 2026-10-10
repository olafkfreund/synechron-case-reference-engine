---
status: draft
issue: 142
author: olafkfreund
---

# Intent: "Crawl now" is shown on disabled sources and gives a raw 404

## Problem

The Sources page (`app/templates/sources.html:45`) shows a **Crawl now**
button on every row. The route (`app/sources.py:138-140`) only crawls enabled
sources. For a disabled one it raises `404 "no such enabled source"`, which
the browser shows as a bare JSON page. The button can never work there.

The Upload button on the next line already shows only for enabled sources.

## Proposed outcome

**Crawl now** is shown only on enabled sources. A disabled source's row
explains nothing more than it does today.

## Affected users and systems

- Admins: `app/templates/sources.html`.
- Tests: `tests/test_sources.py`.

## Constraints

- The route keeps refusing disabled sources. This is a display fix only.

## Open questions

None.
