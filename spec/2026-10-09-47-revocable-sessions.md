---
status: approved
issue: 47
intent: intent/2026-10-09-47-revocable-sessions.md
---

# Spec: Revocable sessions

## Design

Keep the signed Starlette cookie. Add a time to it, and add a Postgres table
that holds a per-user "sessions valid after" time. A cookie is accepted only
if it was issued after its user's cutoff time. Both logout and an operator
script move the cutoff forward. This is what the approved intent chose
(question 1 (c), question 2 (ii), question 4 out of scope).

### 1. The cookie carries an issued-at time

In `/auth` (`app/main.py:173-176`), the session's `user` dict gets
`"iat": time.time()`, a float in seconds from the app's clock. The value has
sub-second precision, so a cutoff and a login that fall in the same second
still compare correctly.

The time has to be stored explicitly. `SessionMiddleware` signs with
itsdangerous's `TimestampSigner`, which only uses its own timestamp to
enforce `max_age` and does not pass it to the app. The 8-hour `max_age`
(`app/main.py:17`, `97-98`) stays as the upper bound.

### 2. Table and schema repair

Add this to `sql/schema.sql`, after the audit tables:

```sql
-- a session cookie issued at or before valid_after is refused (#47): logout and app.revoke_sessions move it
create table if not exists session_cutoffs (
  sub text primary key,
  valid_after timestamptz not null
);
```

This follows the file's repair pattern. Every statement is idempotent
(`create table if not exists`, `alter table ... add column if not exists`),
and the file starts with `pg_advisory_xact_lock(1)` (`sql/schema.sql:2`), so
concurrent runs are safe:

- **In AWS:** the `migrate` one-off task applies it as the master user
  (`app/migrate.py`, `infra/ecs.tf:98-106`).
- **Locally:** `DB_INIT_ON_START=1` applies it (`app/db.py`
  `init_if_requested`).

No grant is needed. `sql/roles.sql:16` already gives `refs_app` select,
insert, update and delete on tables the master creates later. The table is
not append-only, so it is not added to the revokes at `roles.sql:18-19`.

The table holds at most one row per user who has ever logged out or been
revoked. It needs no cleanup job.

### 3. The check, and its cost on every request

`current_user` (`app/main.py:40-47`) keeps its current order:

1. No `user` in the session: return 401, with no database access. Anonymous
   requests and `/healthz` stay database-free (`tests/test_auth.py:199`).
2. Otherwise, run one primary-key lookup: `select valid_after from
   session_cutoffs where sub = %s`.
3. If a row exists and `s.get("iat", 0) <= valid_after.timestamp()`, call
   `request.session.clear()` and raise 401. Clearing the session means the
   browser drops the dead cookie, and the existing 401 handler sends it to
   `/login`.

Cost: there is no connection pool, so `db.connect()` (`app/db.py:40`) opens
a connection per call. The lookup adds one connection and one index lookup
per authenticated request. Most authenticated routes already open at least one
connection; `/me` is the only one that does not. That is acceptable at this
portal's traffic: an internal team, 2 web tasks. There is no cache. With no
cache, revocation takes effect on the next request on every task and needs
no invalidation logic.

If the database is down, authenticated requests fail with a 500. This is
fail-closed. Every page except `/me` needs the database anyway.

### 4. Logout

`POST /logout` (`app/main.py:179-182`) gets the `sub` from the session
before clearing it. If there is one, it upserts the cutoff:

```sql
insert into session_cutoffs (sub, valid_after) values (%s, %s)
  on conflict (sub) do update set valid_after = greatest(session_cutoffs.valid_after, excluded.valid_after)
```

The value written is `now` from the app's clock (`datetime.now(timezone.utc)`),
the same clock as `iat`. `greatest` means a cutoff never moves backwards.
Logout without a session only clears it, as today.

This ends that user's sessions on every device. The intent accepted that
cost.

### 5. The revoke script

Add a new module, `app/revoke_sessions.py`. It runs as
`python -m app.revoke_sessions <sub> [<sub> ...]`. It uses the same upsert
and prints `revoked sessions of <sub>` for each sub. The exit status is 0,
or 2 with a usage line when no sub is given.

It goes in `app/` rather than `scripts/`. One-off commands that already run
in AWS live in `app/` and run as `python -m app.<name>` (`app/migrate.py`,
`app/enqueue_crawls.py`). `scripts/` holds developer tools. This differs
from the intent's lean of "a script under `scripts/`", but that lean was
about having a command rather than a UI, and this is still a command.

How to run it:

- **Locally:**
  `docker compose run --rm app python -m app.revoke_sessions <sub>`.
- **In AWS:** `aws ecs run-task` with the `crawl` task definition
  (`infra/ecs.tf:88-97`) and a command override. That task connects as
  `refs_app`, which is enough, and needs no new task definition or IAM
  change. The README gets a "Revoke a user's sessions" section with both
  commands.

**Finding the sub.** Entra's `sub` is opaque and different for each app, so
an operator cannot guess it. `/auth` therefore prints one log line per
login, `login sub=<sub> name=<name>` (next to the existing overage line at
`app/main.py:169`). An operator can then search CloudWatch by name. The sub
also already appears in `generations.user_id`, `research.created_by` and
the `changed_by` columns.

### 6. Cookies issued before deploy

A cookie from before the deploy has no `iat`, so it is treated as `0`.
Before anyone logs out, no rows exist, so these cookies keep working until
their 8-hour expiry. Nobody is logged out by the deploy.

Once a user has a cutoff row, any of their cookies without `iat` is
refused, so old cookies are revocable too. The cookie name, the secret and
the format stay the same. Rolling back to the previous image is safe: old
code ignores `iat` and the table.

### 7. Test fixtures

Two helpers sign cookies by hand:

- `client()` in `tests/test_auth.py:27-32` (also imported by test_anonymise,
  test_hardening, test_models_admin and test_render);
- `as_user()` in `tests/test_research.py:63-67`.

The changes:

- `client()` takes an `iat=None` argument and puts `"iat": iat or
  time.time()` in the payload.
- `as_user()` calls `client()` instead of signing its own cookie (it also
  needs `sub`, so `client()` gains `sub="u1"`). One signing helper is left.
- Authenticated tests that never called `db.init()` now need the table.
  `client()` calls `db.init()` when it sets a cookie. `db.init()` is
  idempotent, and the compose `app` service already has `DATABASE_URL`
  (`docker-compose.yml:56`).

New tests go in `tests/test_auth.py` (see Verification).

## Alternatives rejected

- **Server-side sessions** (the cookie holds only a random id; a row holds
  the user). Revoking one user is the same, but it needs a row per login,
  a cleanup of expired rows, a new signing path in the test fixtures, and
  it logs everyone out at deploy. The intent chose (ii).
- **Per-user generation counter instead of a time.** The cookie would hold
  `gen` and the table `(sub, gen)`; logout and revoke would increment it. It
  is immune to clock skew, but login would have to read the counter, and the
  approved intent asked for a "valid after" time. Within one task's clock or
  Amazon Time Sync, skew is sub-millisecond against an IdP round trip of
  more than 100 ms, so the time-based check is safe.
- **Cache the cutoffs in memory (for example, reload every 30 s).** It
  saves the per-request connection, but adds staleness, a reload thread and
  an invalidation story across 2 tasks, for a load that does not need it.
  This is the fallback if Verification shows the lookup hurts.
- **The database clock (`now()`) for the cutoff.** `iat` comes from the
  app's clock, and mixing the two clocks invites skew. Both use the app's
  clock.
- **An admin route in the portal.** The intent's question 3 put a command
  first. A route can be added if admins ask for one.
- **Re-checking IdP groups mid-session.** Out of scope, per intent question
  4.

## Risks

- **Database outage turns into errors on every authenticated page.** Before
  this change `/me` worked without the database; after it, it does not.
  Pages that read data already fail today. Every host is affected.
- **Lookup latency in AWS.** Each new connection to RDS costs a TLS and
  SCRAM handshake. If page latency grows noticeably, switch to the cache
  alternative. Verification measures this.
- **Logout is global per user.** Logging out on a laptop also ends the
  phone session. This is accepted in the intent and goes in the README
  section.
- **A deploy without the migrate task.** In AWS the app role cannot create
  the table, so `current_user` raises an "undefined table" error and every
  page fails. `infra/outputs.tf:23` already says to run `migrate` after every schema
  change. The PR and the new README section state it again.
- **Clock skew between web tasks.** Within milliseconds under Amazon Time
  Sync; see the rejected alternatives above.

## Verification

Tests (`docker compose build app && docker compose run --rm app pytest`),
new in `tests/test_auth.py`:

- After `POST /logout` with a cookie, replaying the same cookie on `/me`
  gets 401 (and a 303 to `/login` with `Accept: text/html`).
- A login after the cutoff (a cookie with a larger `iat`) gets 200.
- `python -m app.revoke_sessions u1` (calling `main()` with argv) makes
  `u1`'s cookie get 401, while another sub's cookie still gets 200. With no
  argument, the exit status is 2.
- A cookie without `iat` gets 200 with no row and 401 once the sub has a
  row (the deploy case).
- The existing tests pass unchanged, including the anonymous-401 test and
  `test_healthz_needs_no_login_and_no_database`.

Manual checks, against the mock OIDC provider from README "Local login".
`docker compose up` / `down` are not run by the agent, since the dev stack
is live; the user restarts `web` after the build.

1. Log in as `admin` and copy the session cookie.
2. `POST /logout` in the browser. `curl -b session=<copied> /me` then gets
   401.
3. Log in again, then run
   `docker compose run --rm app python -m app.revoke_sessions admin`. The
   next page load goes to `/login`. A `reviewer` session in another browser
   still works.
4. `scripts/check_local_login.sh` still passes for all four users.

Cost: time `/me` before and after locally, 100 requests with
`curl -w '%{time_total}'`, and record the difference in the plan. If the
p50 grows by more than 20 ms, raise it before shipping.
