---
status: draft
issue: 47
author: olafkfreund
---

# Intent: Revocable sessions

## Problem

A login cannot be ended from the server side. The whole session lives in
the browser:

- `app/main.py:97-98` adds Starlette's `SessionMiddleware` with
  `max_age=SESSION_MAX_AGE`, which is `8 * 3600` (`app/main.py:17`). The
  cookie is signed but stateless. It holds `sub`, `name` and the filtered
  `groups` (`app/main.py:173-176`).
- `current_user` (`app/main.py:40-47`) trusts any cookie with a valid
  signature that is less than 8 hours old. Nothing on the server records
  which sessions exist.
- `POST /logout` (`app/main.py:179-182`) only clears the browser's copy. A
  copied cookie still works until it expires.
- `sql/schema.sql` has no table for sessions.

So for up to 8 hours after it was taken, a stolen cookie gives full access
with that user's roles and document groups. This includes admin upload
(`app/main.py:188`) and the review and source pages. Logging out does not
stop it, and an operator cannot end one user's sessions. The only kill
switch is to rotate `SESSION_SECRET`, which logs out everyone.

A related gap: group changes in the IdP reach the app only at the next
login. Roles are worked out from the cookie's groups on every request
(`roles_for` in `app/main.py:36-39`), but the groups themselves are fixed
for the life of the cookie.

The reference-engine plan lists this as a known gap ("Known gap" under
step 8 in `plan/2026-10-06-1-reference-engine.md:298-299`). It was accepted
because it is within the one-day ACL lag (line 30). Issue #47 asks for a
decision on whether to move to server-side sessions.

## Proposed outcome

The outcome is a recorded decision, and possibly a change. If the decision
is to make sessions revocable, these things are true:

- After `POST /logout`, the same cookie, replayed from another client, gets
  401 on `/me` (and 303 to `/login` for a browser).
- An admin, or an operator through a documented command, can end every
  session of one `sub`. That user's next request gets 401 without anyone
  else being logged out.
- Rotating `SESSION_SECRET` is no longer the only kill switch.
- Normal login, the 8-hour lifetime, role checks and the CSRF guard behave
  as they do today, and the existing `tests/test_auth.py` cases still pass.
  Some of them may need a different fixture.

If the decision is not to do this, the intent records why, the known gap
stays documented in the README, and #47 is closed with a link to the
decision.

## Affected users and systems

- `app/main.py`: session middleware setup (lines 97-98), `current_user`
  (40-47), `/auth` (173-176), `/logout` (179-182), and possibly a new admin
  route.
- `sql/schema.sql` if sessions are stored in Postgres, which is the only
  shared store: there is no Redis in `infra/` or `docker-compose.yml`.
- `infra/`: web runs `web_desired_count = 2` tasks
  (`infra/variables.tf:74-76`). Any session store must be shared by all
  tasks, so it cannot live in process memory.
- `tests/test_auth.py:29-31` makes logged-in clients by signing a cookie
  the way `SessionMiddleware` does. Tests that rely on that would need a
  new fixture.
- Every user of the portal: a schema change or a change to the cookie
  format logs everyone out once, at deploy.

## Constraints

- No new dependencies unless unavoidable. Use what is already installed:
  Starlette, itsdangerous and the existing Postgres access in `app/db.py`.
- Identity comes only from the validated ID token. The client must never
  be able to set groups or roles (the trap noted in plan step 8).
- Keep the CSRF guard, `same_site=lax`, `https_only` and the 8-hour upper
  bound.
- Must work with two or more web tasks behind the ALB.
- Test data must be public or made up. Test users and groups follow the
  README "Local login" table (`refs-admins`, `refs-reviewers`, ...).
- Tests run with `docker compose build app && docker compose run --rm app
  pytest`. Manual checks use the mock OIDC provider from docker-compose
  (README "Local login"). No real IdP accounts.
- Do not add a cost to every request that the scale does not justify. One
  indexed primary-key lookup per request is the expected ceiling.

## Open questions

1. **Do it at all?** The plan accepted the gap because it fits the one-day
   ACL lag.
   - (a) Keep the gap and document it in the README. Close #47.
   - (b) Shorten `SESSION_MAX_AGE` (for example to 1 hour) and re-login
     silently through the IdP's own session. This is a one-line change, but
     it gives no revoke.
   - (c) Real revocation (question 2).

   Lean: (c), but only if the approver agrees that admin upload and
   `acl_groups` exposure justify it. Otherwise (a). (b) shrinks the window
   without closing it, and makes the IdP round trip more frequent.
2. **How to revoke**, if (c):
   - (i) Server-side sessions: the cookie holds only a random id, and a
     Postgres `sessions` row holds the user and expiry.
   - (ii) Keep the signed cookie, add a session id to it, and keep a
     Postgres table of revoked ids (or a per-`sub` "valid after" time).

   Lean: (ii) with a per-`sub` `sessions_valid_after` time. Revoking one
   user then updates one row; logout sets it too. It is the smallest change
   and keeps the existing cookie and test fixture. Its cost is that logout
   ends that user's sessions on all devices. (i) is the textbook answer,
   but it is a bigger change and needs cleanup of expired rows.
3. **Who revokes, and how?** An admin route in the portal, a script under
   `scripts/`, or both. Lean: a script first, because it needs no UI and
   operators already have database access. Add the route only if admins
   ask for it.
4. **Refresh groups during a session?** Should revocation also cover IdP
   group removal (re-checking groups before the 8 hours are up)? Lean: no.
   That belongs to the one-day ACL lag and needs a Graph or IdP call. It
   stays out of scope.
