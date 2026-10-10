---
status: draft
issue: 145
intent: intent/2026-10-10-145-session-absolute-lifetime.md
---

# Spec: A session ends 8 hours after its login, however often it is re-signed

## Design

Two changes in `app/main.py`. No schema change.

1. **`current_user` (`:45-55`) enforces the age of the login.** Right after it
   reads `s`, and before the `session_cutoffs` query:

   ```python
   iat = s.get("iat")
   if not isinstance(iat, (int, float)) or time.time() - iat > SESSION_MAX_AGE:
       request.session.clear()  # the browser drops the dead cookie
       raise HTTPException(401, "session expired; log in again")
   ```

   - The limit is `SESSION_MAX_AGE` (`:20`, 8 hours). It is the same constant
     the cookie signer uses, so the two limits can't drift apart.
   - `iat` is written only by `/auth` (`:198`) from `time.time()`, which is the
     same clock this check uses.
   - **Fail closed.** A session with no `iat`, or with an `iat` that isn't a
     number, is refused.
   - The 401 goes through the existing handler (`:136-142`). Browsers are sent
     to `/login` and API clients get JSON, as with revocation today.
   - The `session_cutoffs` check below it is unchanged, so revocation through
     `/logout` and `revoke_sessions` (#47) keeps working.

2. **`/login` (`:174-179`) drops any existing `user`** with
   `request.session.pop("user", None)` before `authorize_redirect`.
   - Starting a sign-in no longer re-signs the old identity for another
     8 hours.
   - An abandoned sign-in leaves the browser logged out, which is the honest
     state.
   - `/auth` already calls `session.clear()` on success (`:195`), so a
     completed login behaves as it does today.

Change 1 is the fix on its own. Starlette re-signs the cookie on every session
change, so the cookie's `max_age` can never bound a session; only `iat` can.
Change 2 is one line, and it shuts the specific renewal route the issue found.

## Alternatives rejected

- **Stop re-signing the cookie (a custom `SessionMiddleware`, or a separate
  cookie for the OIDC state).** This means more code, and it still trusts the
  cookie's timestamp. Any future write to the session would reopen the gap.
  Checking `iat` covers every path that re-signs.
- **Sliding expiry (an idle timeout).** This keeps the forever-renewal the
  issue is about. The requirement from #47 is an absolute 8 hours from login.
- **Re-reading groups from the IdP on each request.** This needs a refresh
  token and a call to Graph on every page. Re-login every 8 hours already
  picks up group changes, which is the outcome the intent asks for.
- **A grace period for cookies with no `iat`.** Cookies from before #47 have
  been past 8 hours old for a long time, and they can't be told apart from a
  forged downgrade. The intent says fail closed.

## Risks

- **Everyone signs in again at least every 8 hours,** even while active. The
  documented limit is already 8 hours, so the only change is that it is now
  enforced.
- **A form posted after the limit gets a 401 and the edit is lost.** The same
  thing happens today when the cookie itself expires, so this is not new.
- **Test helpers.** `tests/test_auth.py::client` signs the cookie with
  `iat=time.time()` by default, so other test modules that import it are
  unaffected. `test_cookie_without_iat` currently asserts that a cookie with
  no `iat` is *accepted*, so it must change to assert a 401. No other test
  writes a session (`git grep TimestampSigner tests`).
- **Clock jumps on the app host** could shorten or lengthen a session. The
  check uses the same clock that wrote `iat`, so the only effect is a jump's
  size, never a mismatch between hosts. Every task in a deployment uses
  NTP-synced clocks.

## Verification

New and changed tests in `tests/test_auth.py`. The first three must fail on
main's `app/main.py`:

- `tests/test_auth.py::test_session_expires_after_max_age`:
  - `client([USER], iat=time.time() - main.SESSION_MAX_AGE - 5)` gets a 401
    from `/me`;
  - with `Accept: text/html`, it gets a 303 to `/login`;
  - a client whose `iat` is 60 seconds inside the limit gets a 200.
- `tests/test_auth.py::test_cookie_without_iat` (changed): a cookie with no
  `iat` gets a 401 both with and without a cutoff row.
- `tests/test_auth.py::test_login_drops_existing_user`:
  - set up OIDC as `test_callback_takes_groups_from_token_claim` does, and
    monkeypatch `authorize_redirect` to return a `RedirectResponse`;
  - a signed-in client gets a 200 from `/me`;
  - after `GET /login`, the same client gets a 401 from `/me`.
- `tests/test_auth.py::test_login_after_cutoff_works`,
  `test_logout_revokes_replayed_cookie` and `test_cut_sessions_is_per_user`
  still pass unchanged, which shows revocation is intact.
- The full suite passes.
