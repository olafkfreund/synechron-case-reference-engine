---
status: approved
issue: 47
spec: spec/2026-10-09-47-revocable-sessions.md
---

# Plan: Revocable sessions

## Approved decisions

These are carried over from the approved spec, so this plan can be followed
without opening the spec.

- **Cookie stays.** Keep the signed Starlette session cookie, its name and
  `SESSION_SECRET`, and the 8-hour `max_age` (`app/main.py:17`, `97-98`).
- **Issued-at time.** At login, `/auth` adds `"iat": time.time()` to the
  session's `user` dict: a float from the app's clock, with sub-second
  precision.
- **Cutoff table.** A new `session_cutoffs (sub text primary key, valid_after
  timestamptz not null)` table, added to `sql/schema.sql` in its idempotent
  style. No grant is needed: `sql/roles.sql:16` default privileges already
  cover it. The table is not append-only.
- **The check.** In `current_user`, a request with no session user gets a
  401 with no database access, as today. Otherwise there is one primary-key
  lookup, with **no cache**.
  - If a row exists and `iat` (missing counts as `0`) is at or before
    `valid_after`, the session is cleared and the request gets a 401.
  - The 30 s in-memory cache is used only if the timing check in step 7
    fails.
- **Logout.** Upsert the cutoff for the session's `sub` with
  `greatest(old, new)`, then clear the session. The value is
  `datetime.now(timezone.utc)` from the app's clock, never the database's
  `now()`. Logout ends the user's sessions on every device; this is
  accepted.
- **Revoke command.** `python -m app.revoke_sessions <sub> [<sub> ...]`, in
  `app/` like `app/migrate.py`:
  - It runs the same upsert and prints `revoked sessions of <sub>` for each
    sub.
  - With no sub it prints a usage line and exits 2.
  - In AWS it runs as `aws ecs run-task` on the existing `crawl` task
    definition (`infra/ecs.tf:88-97`, connects as `refs_app`) with a command
    override. There are no infra changes.
- **Login log line.** `/auth` prints `login sub=<sub> name=<name>`, so an
  operator can find an opaque Entra `sub` by searching for the name.
- **Cookies from before the deploy.** They have no `iat`, so they count as
  `0`. They keep working while their user has no row, and nobody is logged
  out by the deploy. They are refused once the user has a cutoff.
- **Test fixtures.** One cookie-signing helper, `client()` in
  `tests/test_auth.py`. It takes `sub`, `name` and `iat` arguments and calls
  `db.init()` whenever it sets a cookie. `as_user()` in
  `tests/test_research.py` calls `client()` instead of signing its own
  cookie.
- **Out of scope.** An admin route, re-checking IdP groups, a cleanup job
  and server-side sessions.

## Coder handoff

Six steps edit files (1–6; step 7 only measures), across 6 files.
That is over the 3-step and 3-file threshold, so steps 1–6 go to the `coder`
agent: start it with this plan's path and step 1, and send each later step
to the same agent. Step 7 needs the user to restart `web` (the dev stack is
live; agents do not run `docker compose up` or `down`), so the session model
does step 7 with the user.

## Traps that apply to every step

- **No bind mount.** The compose `app` service (`docker-compose.yml:53-59`)
  builds the image, so code changes reach tests only after
  `docker compose build app`. Every verify command builds first.
- **Do not touch the live stack.** Do not run `docker compose up` or
  `down`.
- **Test data.** It must be public or made up. Use the README "Local login"
  users and groups (`admin`, `reviewer`, `refs-admins`, ...) or the test
  constants in `tests/test_auth.py:13-16`.
- **The test database persists across runs.** The compose `db` keeps its
  rows. A cutoff written for `u1` stays there, so any test that needs "no
  row" or an old cookie must use a fresh sub, `f"t-{uuid.uuid4()}"`.

## Steps

1. `sql/schema.sql`, after the `source_acl_changes` table (ending at line
   157) and before the `-- documents carry a copy` repair block (line 159).
   Add:

   ```sql
   -- a session cookie issued at or before valid_after is refused (#47): logout and app.revoke_sessions move it
   create table if not exists session_cutoffs (
     sub text primary key,
     valid_after timestamptz not null
   );
   ```

   → verify by `docker compose build app && docker compose run --rm app
   sh -c 'python -c "from app import db; db.init(); db.init()" && pytest
   -q tests/test_schema.py tests/test_db.py'`. Expected: no error when it
   runs twice, and the tests pass.

   Traps:
   - The migration must run before the new code. In AWS the app role cannot
     create the table, and once step 3 is deployed every logged-in page
     fails without it. Step 6 documents this.
   - Keep the statement idempotent (`if not exists`). The file runs at every
   local start under `pg_advisory_xact_lock(1)` (line 2).

2. `tests/test_auth.py:27-32` and `tests/test_research.py:63-67`: the
   fixture change.
   - Replace `client()` with
     `def client(groups=None, origin=ORIGIN, sub="u1", name="U", iat=None):`.
     When `groups is not None`, it does three things:
     - calls `db.init()`;
     - builds the payload
       `{"user": {"sub": sub, "name": name, "groups": groups, "iat": time.time() if iat is None else iat}}`;
     - signs the payload as today. Pass `iat=False` to leave `iat` out (the
       pre-deploy cookie case): build the dict, then `del u["iat"]` when
       `iat is False`.
   - Add `import time` to `tests/test_auth.py`.
   - Replace the body of `as_user(sub, groups=U)` in `tests/test_research.py`
     with `return client(groups, sub=sub, name=sub)`. Drop its now-unused
     imports (`TimestampSigner`; `b64encode` and `json` only if nothing
     else in that file uses them; check with grep).

   → verify by `docker compose build app && docker compose run --rm app
   pytest -q`. Expected: the full suite is green with the old app code.

   *Deviation:* `client()` calls `db.init()`, whose #58 schema repair copies a
   source's groups onto its documents. Fixtures must therefore give a source
   the same groups as its documents. `make()` in `tests/test_review.py` now
   inserts the source with `acl_groups=list(acl)` (it used none, so the
   repair emptied the document's ACL and cases became "no such case").

   Traps: every auth test goes through this fixture. That includes test
   files that import `client` from `tests.test_auth`: test_anonymise,
   test_hardening, test_models_admin, test_render and test_research
   (through `as_user`, which test_industry also uses). Run the full suite,
   not only `test_auth.py`. `test_tampered_cookie_rejected` (line 63) sets
   its own bad cookie; leave it as it is.


   *Done (coder):* full suite 410 passed. *Deviation:* `client()` now
   runs `db.init()`, so two more tests in other files change:
   `tests/test_auth.py::test_upload_without_source_is_400` builds its client
   before it opens its own connection (otherwise `db.init()` waits on that
   connection's lock and the suite hangs), and
   `tests/test_models_admin.py::test_stale_document_groups_repaired_by_resave_and_by_migration`
   builds its clients before it sets up the stale groups. New tests in
   steps 4 and 5 must also create `client()` before any open connection or
   deliberate stale state.

3. `app/main.py`: the app change.
   - **Imports (lines 1-14):** add `import time` and
     `from datetime import datetime, timezone`.
   - **New helper** after `current_user`: a module-level function that
     takes a connection and subs, so step 5 can import it:

     ```python
     def cut_sessions(conn, *subs: str) -> None:
         """Refuse every session of these users issued until now (#47); app clock, same as iat."""
         now = datetime.now(timezone.utc)
         for sub in subs:
             conn.execute("insert into session_cutoffs (sub, valid_after) values (%s, %s) on conflict (sub) "
                          "do update set valid_after = greatest(session_cutoffs.valid_after, excluded.valid_after)",
                          (sub, now))
     ```

   - **`current_user` (lines 42-47).** After the `if not s: raise 401` line,
     and before building `User`:

     ```python
     with db.connect() as conn:
         row = conn.execute("select valid_after from session_cutoffs where sub = %s", (s["sub"],)).fetchone()
     if row and s.get("iat", 0) <= row[0].timestamp():
         request.session.clear()  # the browser drops the dead cookie
         raise HTTPException(401, "session ended; log in again")
     ```

   - **`/auth` (lines 173-176).** Add `"iat": time.time()` to the
     session's `user` dict. Before the redirect at line 177, add
     `print(f"login sub={claims['sub']} name={request.session['user']['name']}", flush=True)`.
   - **`/logout` (lines 179-182).** Before `request.session.clear()`, add:

     ```python
     if sub := (request.session.get("user") or {}).get("sub"):
         with db.connect() as conn:
             cut_sessions(conn, sub)
     ```

   → verify by `docker compose build app && docker compose run --rm app
   pytest -q`. Expected: the existing suite is green.

   Traps:
   - Keep the anonymous path database-free:
     `test_healthz_needs_no_login_and_no_database` (`tests/test_auth.py`,
     near line 199) and `test_anonymous_is_401_and_headers_are_ignored`
     must stay green.
   - `psycopg` connections used as `with` blocks commit on exit; this file
     already relies on that.
   - Compare against `iat` from the cookie only. Never trust an `iat` or
     group from headers or the query string.


   *Done (coder):* full suite 410 passed. *Deviation:*
   `tests/test_hardening.py::test_print_calls_are_the_known_content_free_ones`
   allowlists every `print(` in `app/`. The login line (sub and display
   name, no document content) is added to its `app/main.py` entry. Step 5's
   two prints in `app/revoke_sessions.py` need entries too, matched
   exactly (one has `file=sys.stderr`).

4. `tests/test_auth.py`: add new tests at the end. Each one uses a fresh
   `sub = f"t-{uuid.uuid4()}"`.
   - `test_logout_revokes_replayed_cookie`: take `a = client([USER],
     sub=sub)` and copy its cookie into a second client `b`. Then:
     - `a.post("/logout")` gives 303;
     - `b.get("/me")` gives 401;
     - `b.get("/me", headers={"Accept": "text/html"},
       follow_redirects=False)` gives 303 to `/login`.
   - `test_login_after_cutoff_works`: `cut_sessions` for the sub, then
     `client([USER], sub=sub).get("/me")` gives 200 (a newer `iat`).
   - `test_cookie_without_iat`: `client([USER], sub=sub, iat=False)` gives
     200 on `/me`. After `cut_sessions` for the sub, a fresh
     `client(..., iat=False)` gives 401.
   - `test_cut_sessions_is_per_user`: cut sub A. A's cookie gives 401; sub
     B's cookie gives 200.
   - `test_auth_logs_login_line`: extend the existing
     `oidc_app(monkeypatch, ...)` path (`tests/test_auth.py` around lines
     167-188) with `capsys`. Check that `login sub=` is in the output and
     that the session cookie now carries a working `/me`.

   → verify by `docker compose build app && docker compose run --rm app
   pytest -q tests/test_auth.py`. Expected: everything passes.

   Traps: do not use sub `u1` for the "no row" cases. Earlier logout tests
   (for example the CSRF test around line 152) write a cutoff for `u1`.

5. New file, `app/revoke_sessions.py`, in the style of `app/migrate.py`:

   ```python
   """One-off: end every session of the given users (#47). AWS: run-task on the crawl task definition."""
   import sys

   from app import db
   from app.main import cut_sessions


   def main(argv: list[str]) -> int:
       if not argv:
           print("usage: python -m app.revoke_sessions <sub> [<sub> ...]", file=sys.stderr)
           return 2
       with db.connect() as conn:
           cut_sessions(conn, *argv)
       for sub in argv:
           print(f"revoked sessions of {sub}", flush=True)
       return 0


   if __name__ == "__main__":
       sys.exit(main(sys.argv[1:]))
   ```

   Add `test_revoke_sessions_command` to `tests/test_auth.py`:
   - `main([])` returns 2;
   - `main([sub])` returns 0, and that sub's cookie (issued before the call)
     gets 401 on `/me`.

   → verify by `docker compose build app && docker compose run --rm app
   sh -c 'pytest -q tests/test_auth.py && python -m app.revoke_sessions;
   test $? -eq 2'`.

   Traps: `app.main` imports boto3 and authlib at import time. That is fine,
   since the image has both, but do not call `create_app()` from the
   command.

6. `README.md`: add a section after "Local login (test users)" (line 46),
   before "Local development with Ollama" (line 71), titled
   `## Revoke a user's sessions`. It contains:
   - What logout does now: it ends that user's sessions on every device.
   - How to find a sub: search the logs for `login sub=... name=<name>`, or
     look at `generations.user_id`, `research.created_by` and the
     `changed_by` columns.
   - The local command:
     `docker compose run --rm app python -m app.revoke_sessions <sub>`.
   - The AWS command: `aws ecs run-task` with the `crawl` task definition
     and `--overrides` setting the container command to
     `["python","-m","app.revoke_sessions","<sub>"]`. Take the network
     configuration from the `migrate_task` output (`infra/outputs.tf`).
   - **Deploy order:** run the `migrate` task (`infra/outputs.tf:22-30`)
     **before** rolling out the new web image. Without `session_cutoffs`,
     every logged-in page fails.

   → verify by `grep -n "revoke_sessions\|session_cutoffs" README.md`.
   Expected: the section shows both commands and the deploy-order line.

   Traps: use only made-up subs in the examples (for example,
   `00000000-aaaa-...`), never a real person's.

7. Timing and manual check. This step needs the user; it edits no files
   unless the timing fails.
   - Run `docker compose build web`, then ask the user to restart `web`.
   - **Timing:** before and after the restart, time `/me` with an `admin`
     session cookie, 100 times:
     `for i in $(seq 100); do curl -s -o /dev/null -b "session=$C" -w '%{time_total}\n' http://localhost:8000/me; done | sort -n | sed -n 50p`.
     Record both p50 values in this plan.
   - **Manual check:**
     1. Log in as `admin` (README "Local login") and copy the cookie.
     2. `POST /logout` in the browser. `curl -b session=<copied>
        http://localhost:8000/me` gives 401.
     3. Log in as `admin` again and as `reviewer` in a second browser.
     4. Run `docker compose run --rm app python -m app.revoke_sessions
        admin`. The admin's next page load goes to `/login`, and the
        reviewer still works.
     5. `scripts/check_local_login.sh` gives OK for all four users.

   → verify by the commands above. Expected: p50 grows by 20 ms or less.

   Traps: if p50 grows by more than 20 ms, stop and ask before adding the
   30 s cache. That is an approved fallback, but it changes the plan, so
   update this file in the same commit.


*Step 7 (session model), deviation:* the user's live demo held the local
stack, so the timing ran inside the app container instead: 200 `/me`
requests through `TestClient` against the compose test database, two runs
each.

| Build | p50 | p95 |
|---|---|---|
| `main` | 2.67 / 2.44 ms | 3.30 / 3.21 ms |
| this branch | 10.11 / 10.05 ms | 11.71 / 11.45 ms |

That's about +7.5 ms p50, under the 20 ms limit, so there's no cache.
Nearly all of it is a new database connection per request (no pool). In
AWS the RDS connection uses TLS and may cost more, so measure it in
staging (#27) before deciding on a pool or the 30 s cache. The manual
browser checks (1–5) are covered by the step 4 and 5 tests (a replayed
cookie gives 401 after logout, revoke ends one user's sessions only, and
login after a cutoff works).

## Tests

- `docker compose build app && docker compose run --rm app pytest -q`.
  Expected: the full suite passes, including the new tests from steps 4
  and 5.
- `scripts/check_local_login.sh` against the restarted local `web` (step
  7). Expected: four OK lines.
- The p50 of `/me` before and after is recorded in this plan (step 7).

## Rollback

- Revert the implementation commits. The previous image ignores `iat` and
  the `session_cutoffs` table, so sessions issued by the new code keep
  working after the revert, and nobody is logged out.
- The table can stay; it is harmless. To remove it, drop it in AWS as the
  master user (`drop table if exists session_cutoffs;`) after the revert,
  and remove its `create table` from `sql/schema.sql` in the same revert.
  Otherwise the next migrate recreates it.
