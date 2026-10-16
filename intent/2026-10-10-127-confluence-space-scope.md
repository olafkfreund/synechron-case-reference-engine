---
status: approved
issue: 127
author: olafkfreund
---

# Intent: Confluence page moved to an out-of-scope space stays live with the source's groups

## Problem

An admin maps a Confluence source's `config.spaces` (e.g. ENG) to its
`acl_groups`. In Confluence, who can read a page is decided by space
permissions plus page restrictions. The crawler checks only the restrictions
(`allowed()`, `app/crawl.py:370-386`). Space scope is enforced only by the CQL
filter `space in (...)`.

A page can be moved from ENG to a space that isn't configured (e.g. HR) and
keep its id. When the crawler account can read HR, which is plausible for a
broad Cloud token or a Data Center PAT:

- **The live-document re-check** (`:477-492`) fetches the page by id, sees
  `current` and no restrictions, and keeps `page:<id>` and its attachments
  live. They stay searchable by the source's groups, who should no longer see
  HR content.
- **The `retry_ids` loop** (`:465-469`) and the by-id fetch for an
  attachment's page (`:457`) ingest the page's current body without checking
  its space.

This is an ACL leak.

## Proposed outcome

- A page, or an attachment's page, whose space isn't in `config.spaces` is
  never ingested.
- A document already ingested from such a page is withdrawn on the next
  crawl, the same way a newly restricted page is.

## Affected users and systems

- `app/crawl.py` (`crawl_confluence`: `allowed()` and the by-id fetches).
- Tests: `tests/test_crawl_confluence.py`, whose fake `Conf` needs a
  `space` field.
- Both Cloud and Data Center.

## Constraints

- Fail closed: a page with no space, or an unknown one, is out of scope.
- One check, in `allowed()`, which every path already calls.
- No extra API call per page. Ask for `space` in the existing `expand`.

## Open questions

None. The check compares the page's space key with `config.spaces` in
`allowed()`, and the by-id fetches and search hits add `space` to their
expand.

## Approved answers

None needed.
