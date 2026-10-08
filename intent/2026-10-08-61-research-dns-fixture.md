---
status: approved
issue: 61
author: olafkfreund
---

# Intent: Research tests run outside compose

## Problem

The research tests fake DNS so that no page is really fetched. The `Web`
fixture in `tests/test_research.py` (line 101) replaces
`socket.getaddrinfo` for the whole process, not only for `app/research.py`,
because the module it patches is the global `socket`. psycopg resolves the
database host through the same function.

The fake passes only the host named `db` to the real resolver (line 107),
which is the compose service name. With any other database host in
`DATABASE_URL` or `DB_HOST` (CI outside compose, a local Postgres on
`localhost`), the database resolves to the fake public address
`93.184.216.34`. The connection then hangs in SYN-SENT, and the suite hangs
with it instead of failing.

This affects `tests/test_research.py` and `tests/test_research_claims.py`,
which reuses the fixture. The app itself is not affected.

Found in the #36 review.

## Proposed outcome

- The research tests pass against a database at any host, inside or outside
  compose.
- Every other host the tests look up still gets the fake answer, so no test
  reaches the real web.

## Affected users and systems

- `tests/test_research.py` (the `Web` fixture). Developers and any CI that
  runs pytest outside compose. No app code, no hosts.

## Constraints

- No real web traffic from the tests.
- No app change just to suit a test.

## Open questions

None. The fix is to pass through the host the app actually connects to (from
`db.url()`), unless the spec finds a reason not to.
