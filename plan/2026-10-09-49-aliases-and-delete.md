---
status: draft
issue: 49
spec: spec/2026-10-09-49-aliases-and-delete.md
---

# Plan: Client registry aliases that contain commas, and removing clients

Branch `fix/49-aliases-and-delete`, rebased on `origin/main` after #74 merged.
The line numbers below are from that base.

## Approved decisions

- Aliases are entered in a `<textarea>`, one alias per line. `clean()` splits
  them with `splitlines()`, so commas are part of an alias. Strip, drop-empty,
  de-duplicate and all the other checks in `clean()` stay the same.
- Stored `clients.aliases text[]` values are unchanged. They are shown as
  `aliases | join('\n')` and come back as the same array. No migration.
  `app/anonymise.py` reads the array elements and is unchanged.
- There is a new `POST /admin/clients/{cid}/delete` (admin only) with a hidden
  `v`, checked against the same `VERSION` as `update()`.
- **A client can be deleted only if it is referenceable and no case links to
  it.** This is the stricter rule approved at spec review. A non-referenceable
  client can never be deleted: the server returns 400 with the reason, because
  its names must stay hidden in outputs.
- The responses are: 404 for an unknown id, 400 for a non-referenceable
  client, 400 "N case(s) use this client" for a linked one, 409 for a stale
  `v`, and 303 to `/admin/clients` on success. A `ForeignKeyViolation` from an
  approval racing the delete returns the linked-case 400.
- No JS confirm. The guard is the rule above plus the version check. The
  Delete button is shown only on referenceable rows, and the server enforces
  the rule anyway.
- No soft "retired" flag, no `on delete set null`, no schema change.
- Rebase with #41: #41 adds a `next` parameter and a redirect in `create()`,
  and an inline form on the review page that sends no aliases. Both branches
  edit `app/clients.py`, but in different hunks. Whichever merges second
  rebases and re-runs the full test suite.

## Deviation from the spec (approved at spec review)

The spec allowed deleting any client with no linked case. This plan
additionally refuses non-referenceable clients. As a result:

- The delete SQL gains `and referenceable`.
- A new 400 reason is added.
- The linked-case message no longer suggests "make it non-referenceable".
- The spec's risk "an unlinked client's name stops being anonymised" no longer
  applies. The help text says instead which clients can be deleted.

What remains: a deleted referenceable name is no longer removed from outgoing
web research queries by `anonymise.scrub()` (`app/research.py:74`). That is
acceptable, because referenceable names are public by definition.

## Coder handoff

Yes. Four steps edit files, and three files are touched (`app/clients.py`,
`app/templates/clients.html`, `tests/test_anonymise.py`). Start one `coder`
with this plan and step 1, and send it steps 2-4 with SendMessage.

## Steps

1. **`app/clients.py` line 16 (`clean()`).** Replace `aliases.split(",")` with
   `aliases.splitlines()`. Change nothing else on the line.
   → verify by `docker compose build app && docker compose run --rm app pytest tests/test_anonymise.py`.
   This is expected to fail at line 100 until step 4 changes that line. Every
   other test must pass.
   Traps: compose has no bind mount, so build before each test run. Browsers
   submit textarea line breaks as `\r\n`. `splitlines()` handles that; a
   `split("\n")` would leave a `\r` on the end.

2. **`app/clients.py` line 3 and after line 76: the delete route.**
   - Change the import to
     `from psycopg.errors import ForeignKeyViolation, UniqueViolation`.
   - Append:
   ```python
   @router.post("/admin/clients/{cid}/delete")
   def delete(cid: int, v: str = Form(), user: User = Depends(require("admin"))):
       # only referenceable, unlinked clients: a protected name must stay hidden, a linked case keeps its label
       try:
           with db.connect() as conn:
               n = conn.execute(f"delete from clients cl where id=%s and {VERSION}=%s and referenceable "
                                "and not exists (select 1 from cases where client_id = cl.id)", (cid, v)).rowcount
               if not n:
                   row = conn.execute("select referenceable, (select count(*) from cases where client_id=%s) "
                                      "from clients where id=%s", (cid, cid)).fetchone()
                   if not row:
                       raise HTTPException(404, "no such client")
                   if not row[0]:
                       raise HTTPException(400, "a non-referenceable client cannot be deleted: its names must stay hidden")
                   if row[1]:
                       raise HTTPException(400, f"{row[1]} case(s) use this client; it cannot be deleted")
                   raise HTTPException(409, "client changed; reload the page")
       except ForeignKeyViolation:
           raise HTTPException(400, "a case uses this client; it cannot be deleted") from None
       return RedirectResponse("/admin/clients", status_code=303)
   ```
   → verify by the step 1 command (the existing tests still pass).
   Traps: the route path ends in `/delete`, so it does not clash with
   `POST /admin/clients/{cid}` (`update()`). The unqualified columns in
   `VERSION` resolve against `cl`.

3. **`app/templates/clients.html` lines 5-8, 11, 15, 17 and 22.**
   - Lines 5-7, help text: add "Enter one alias per line; commas are part of
     the alias. Only referenceable clients that no case uses can be deleted."
   - Line 8: change the header to `Aliases (one per line)`.
   - Line 11: replace the input with
     `<textarea form="{{ f }}" name="aliases" rows="2" aria-label="aliases">{{ aliases | join('\n') }}</textarea>`.
   - Line 15: after Save, add `{% if ref %} <button form="d{{ id }}">Delete</button>{% endif %}`.
   - Line 17: replace it with
     `<textarea form="new" name="aliases" rows="2" aria-label="new aliases"></textarea>`.
   - Line 22: in the same loop, also emit
     `<form id="d{{ id }}" method="post" action="/admin/clients/{{ id }}/delete"><input type="hidden" name="v" value="{{ v }}"></form>`.

   → verify by `docker compose build app && docker compose run --rm app pytest tests/test_anonymise.py`.
   Traps: no JavaScript (no `confirm()`). Keep the textarea on one line with no
   whitespace inside the tags: browsers drop only one leading newline, and any
   other whitespace would become part of the first or last alias (strip
   removes it, but keep the output clean).

4. **`tests/test_anonymise.py` line 100, plus new tests after line 116.**
   - Line 100: change the aliases to `" Acm\n\nAcm\nAcmeCo "`. The assert on
     line 102 stays `["Acm", "AcmeCo"]`.
   - New `test_alias_with_comma_is_one_alias(reg)`:
     - Post `name=f"SJ {reg}"`,
       `aliases=f"Smith, Jones Partners {reg}\r\nSJC"`,
       `anonymised_label="a law firm"`.
     - `find(reg)` gives aliases `[f"Smith, Jones Partners {reg}", "SJC"]`.
     - Post an update of that row with `"\n".join(aliases)` and its `v`. The
       response is 303 and the aliases are unchanged.
     - `an.apply(f"met Smith, Jones Partners {reg} today", [row])` equals
       `"met a law firm today"`.
     - `an.apply(f"Jones Partners {reg}", [row])` is unchanged.
     - Here `row` is a dict with keys `name`, `aliases`, `anonymised_label`
       and `referenceable=False`.
   - New `test_delete_only_referenceable_unlinked(reg)`:
     - Create `Pub {reg}` (referenceable) and `Priv {reg}` (not
       referenceable). Index `find(reg)` by name.
     - `client([REV])` delete returns 403.
     - Deleting `Priv` returns 400 with "non-referenceable" in the text, and
       the row still exists.
     - Deleting `Pub` with `v="stale"` returns 409.
     - `/admin/clients/999999999/delete` returns 404.
     - Deleting `Pub` with its `v` returns 303, and only `Priv` remains.
     - The admin page shows `action="/admin/clients/{pub_id}/delete"` before
       the delete, and never shows `action="/admin/clients/{priv_id}/delete"`.
   - New `test_delete_refused_for_linked_client(make, reg)`:
     - Insert a referenceable `Hooli {reg}` client.
     - `cid = make()`, then `update cases set client_id=<id> where id=cid`.
     - Delete returns 400 with "case(s) use this client". The client and the
       case's `client_id` are unchanged.
     - In a `finally`, set `client_id=null`, as in
       `tests/test_review.py:189-191`.

   → verify by `docker compose build app && docker compose run --rm app pytest`.
   Traps: names must be made up, with the `reg` suffix. The `reg` teardown
   deletes clients by name, so unlink the case first or the foreign key fails
   the teardown. The hidden `v` in the rendered page is the md5 version, so
   take it from `find(reg)`.

## Tests

`docker compose build app && docker compose run --rm app pytest`. The full
suite must be green, including the three new tests and the changed line 100.
Do not run `docker compose up` or `down`: the dev stack is live.

## Rollback

`git revert` the merge commit. There is no schema change, and the stored
aliases keep their format. Aliases that contain a comma, saved while this was
live, would display as a comma list again, and the next save would split
them. Fix such rows by hand. A client that was deleted is not restored by a
revert; re-add it on `/admin/clients`. Only referenceable, unlinked clients
could be deleted, so no protection is lost.
