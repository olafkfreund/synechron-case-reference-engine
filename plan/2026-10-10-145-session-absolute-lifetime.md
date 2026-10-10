---
status: approved
issue: 145
spec: spec/2026-10-10-145-session-absolute-lifetime.md
---

# Plan: A session ends 8 hours after its login, however often it is re-signed

Approved decisions. All are in `app/main.py`, with no schema change:

- `current_user` refuses a session whose `iat` is missing, isn't a number, or
  is older than `SESSION_MAX_AGE` (8 hours). It clears the session and raises
  a 401 with "session expired; log in again".
- The existing 401 handler sends browsers to `/login` and gives API clients
  JSON.
- Fail closed: there is no grace period for cookies with no `iat`.
- The `session_cutoffs` revocation check (#47) stays below it, unchanged.
- `GET /login` runs `request.session.pop("user", None)` before
  `authorize_redirect`, so starting a sign-in no longer re-signs the old
  identity. `/auth` already clears the session on success.
- Rejected: a custom `SessionMiddleware`, a sliding or idle timeout,
  re-reading groups from the IdP, and a grace period for cookies with no
  `iat`.

The plan has two steps that edit two files (`tests/test_auth.py` and
`app/main.py`), so I implement it myself, with no coder handoff.

## Steps

1. `tests/test_auth.py`: tests first.
   - Change `test_cookie_without_iat` (lines 244-248) so that both assertions
     expect 401:
     ```python
     def test_cookie_without_iat(env):
         sub = fresh()
         assert client([USER], sub=sub, iat=False).get("/me").status_code == 401  # fail closed (#145)
         cut(sub)
         assert client([USER], sub=sub, iat=False).get("/me").status_code == 401
     ```
   - Add these two tests after it:
     ```python
     def test_session_expires_after_max_age(env):
         old = time.time() - main.SESSION_MAX_AGE - 5
         assert client([USER], iat=old).get("/me").status_code == 401
         r = client([USER], iat=old).get("/me", headers={"Accept": "text/html"}, follow_redirects=False)
         assert r.status_code == 303 and r.headers["location"] == "/login"
         assert client([USER], iat=time.time() - main.SESSION_MAX_AGE + 60).get("/me").status_code == 200


     def test_login_drops_existing_user(env, monkeypatch):
         for k, v in dict(OIDC_METADATA_URL="http://idp/.well-known", OIDC_CLIENT_ID="c",
                          OIDC_CLIENT_SECRET="s").items():
             monkeypatch.setenv(k, v)
         c = client([USER])

         async def fake(request, redirect_uri):
             return RedirectResponse("http://idp/authorize", status_code=302)
         monkeypatch.setattr(c.app.state.oauth.oidc, "authorize_redirect", fake)
         assert c.get("/me").status_code == 200
         assert c.get("/login", follow_redirects=False).status_code == 302
         assert c.get("/me").status_code == 401
     ```
   - Add `from fastapi.responses import RedirectResponse` to the imports.
   - Verify:
     `docker compose build app && docker compose run --rm app timeout 900 pytest -q -p no:cacheprovider tests/test_auth.py::test_cookie_without_iat tests/test_auth.py::test_session_expires_after_max_age tests/test_auth.py::test_login_drops_existing_user`.
     All three must fail on main's `app/main.py`.
   - Traps:
     - There is no bind mount, so build before every run.
     - Never run `docker compose up` or `down`.
     - The OIDC env vars must be set before `client()`, because
       `create_app()` registers `oauth.oidc` only when they exist.
     - Patch `authorize_redirect` on `c.app`'s oauth client, not on a second
       app.
     - `/login` is a GET, so it needs no Origin header.

2. `app/main.py`:
   - In `current_user` (lines 45-55), right after the `if not s:` 401 and
     before `with db.connect()`, insert:
     ```python
     iat = s.get("iat")
     if not isinstance(iat, (int, float)) or time.time() - iat > SESSION_MAX_AGE:
         request.session.clear()  # the browser drops the dead cookie
         raise HTTPException(401, "session expired; log in again")
     ```
   - In `login` (lines 174-179), add `request.session.pop("user", None)  # starting a sign-in ends the old one (#145)`
     as the first line of the body.
   - `time` is already imported, at line 3.
   - Verify: the step 1 command passes, and so do
     `tests/test_auth.py::test_login_after_cutoff_works`,
     `::test_logout_revokes_replayed_cookie` and `::test_cut_sessions_is_per_user`,
     which are unchanged.
   - Traps:
     - Keep the `session_cutoffs` check exactly as it is.
     - `isinstance(True, int)` is true. That is harmless here, because only
       `/auth` writes `iat`, always from `time.time()`.

## Tests

The full suite must be green:
`docker compose build app && docker compose run --rm app timeout 900 pytest -q -p no:cacheprovider`.

Other test modules sign sessions only through `tests.test_auth.client`, whose
default `iat` is `time.time()`, so they are unaffected.

## Rollback

Revert the commit. Sessions then go back to being renewable indefinitely.
There is no schema or data change. Everyone is signed out at most once, when
the fix is deployed, and only if their session was older than 8 hours.
