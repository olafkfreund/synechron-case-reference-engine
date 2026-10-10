---
status: approved
issue: 145
author: olafkfreund
---

# Intent: A session can be renewed indefinitely, keeping groups the IdP has since removed

## Problem

A login stores the user's id, groups and roles in a signed session cookie. The
cookie's lifetime is the signer's `max_age` of 8 hours (`app/main.py:119`).
Starlette re-signs the cookie, with a fresh 8 hours, whenever the session
changes.

`GET /login` (`:174-179`) stores Authlib's OIDC state in the session before
redirecting to the IdP. So a visit to `/login` that never completes the IdP
round trip still re-signs the existing `user` entry for another 8 hours.

`current_user` (`:44-50`) checks the login time (`iat`) only against the
revocation table (`session_cutoffs`), never against a maximum age.

The result: anyone holding a live cookie, whether its owner or a thief, can
keep it alive forever, with the groups and roles of the original login.
Removing someone from an admin or ACL group in Entra never takes effect, which
breaks the 8-hour limit that #47 relies on.

## Proposed outcome

- A session ends at most 8 hours after the login that created it, however
  often the cookie is re-signed.
- After that the user must sign in again, which picks up their current groups.

## Affected users and systems

- Every user: `app/main.py` (`current_user`, `/login`).
- Tests: `tests/test_auth.py`.

## Constraints

- Fail closed: a session with no `iat` is treated as expired.
- No schema change.
- Revocation through `session_cutoffs` keeps working.

## Open questions

None. `current_user` refuses a session whose `iat` is older than
`SESSION_MAX_AGE`, and `/login` drops any existing `user` before starting a
new sign-in.
