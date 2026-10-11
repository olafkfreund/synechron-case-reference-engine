---
status: approved
issue: 164
spec: spec/2026-10-10-164-client-rename-keeps-names.md
---

# Plan: Saving a protected client keeps every name it had

Approved decisions (spec, decision **A**):

- When a non-referenceable client is saved as non-referenceable, every old name or alias that the submitted name and
  aliases no longer cover is appended to the aliases automatically. "Covered" is judged by `anonymise._key()`, so a
  rename that only changes case or spacing keeps nothing.
- The kept names are added before `clean()` runs, so they are validated like typed aliases: no label may contain
  them, and they must not be taken by another client.
- The `VERSION` check on the update stays. A row that changed between the read and the write is a 409, as it is
  today.
- After a save that kept names, the page redirects to `/admin/clients?kept=<n>` and shows a `role="status"` note:
  "Kept {n} former name(s) as aliases, so they stay hidden."
- Unchanged:
  - referenceable clients;
  - a save that makes a protected client referenceable, which is the admin's explicit choice;
  - `delete`.

## Steps

1. `app/clients.py:69-87` (`update`):
   - Replace the `select 1` existence check (`:74`) with
     `old = conn.execute("select name, aliases, referenceable from clients where id=%s", (cid,)).fetchone()`.
     Keep the 404 when it returns nothing.
   - Before `clean()`:
     ```python
     kept = []
     if not old[2] and not referenceable:  # a protected client keeps every name it had (#164)
         have = {anonymise._key(n) for n in [name, *aliases.splitlines()] if n.strip()}
         for n in [old[0], *old[1]]:
             if anonymise._key(n) not in have:
                 have.add(anonymise._key(n))
                 kept.append(n)
         aliases = "\n".join([aliases, *kept])
     ```
   - The final line becomes
     `return RedirectResponse(f"/admin/clients?kept={len(kept)}" if kept else "/admin/clients", status_code=303)`.

   → verify: `docker compose build -q app && docker compose run --rm app timeout 900 pytest -q -p no:cacheprovider tests/test_anonymise.py`

   Traps:
   - `clean()` splits aliases with `splitlines()`. The kept names must be joined with `"\n"`, never `","`, because a
     comma is part of an alias (see `test_alias_with_comma_is_one_alias`).
   - Use `_key()` both for the submitted names and for the old ones. `fold()` and `has_name()` are the wrong
     equality here: they match substrings and boundaries, not the name as a whole.
   - Leave the read-then-update without a row lock. The `VERSION` equality in the `update ... where` is the guard.
   - `test_create_update_and_validation` saves a client that was referenceable, so it keeps nothing. Its later stale
     save, on a now-protected client with alias `A1` and no submitted aliases, re-adds `A1` and must still return
     409, which happens at the version check. Its unknown-id call must still return 404, before any validation.

2. `app/clients.py:46-51` (`clients_page`) and `app/templates/clients.html`:
   - `clients_page(request, kept: int = 0, user=...)` passes `kept=kept` to `page(...)`.
   - In `clients.html`, between the closing `page-head` div (`:21`) and `<div class="section-card client-list">`,
     add the following, matching the notice pattern in `sources.html:32`:
     ```html
     {% if kept %}<p class="badge ok" role="status">Kept {{ kept }} former name(s) as aliases, so they stay hidden.</p>{% endif %}
     ```
   - In the help text at `:7`, add one sentence: "Saving a non-referenceable client keeps any name you remove as an
     alias, so it stays hidden."

   → verify: the same command as step 1.

   Traps: no JavaScript. `kept` comes from the query string and is typed `int`, so a bad value is a 422 and
   nothing unescaped is rendered.

3. `tests/test_anonymise.py`: add these tests after `test_label_recheck_leaves_other_saves_alone`, using the
   existing `reg`, `find`, `_add` and `_row` helpers:
   - `test_renaming_a_protected_client_keeps_the_old_name`:
     - Create protected `Zorp Bank {reg}` with alias `ZB{reg}`, then post an update with name
       `Zorp Financial {reg}`, no aliases and `follow_redirects=False`.
     - Expect a 303 with `location == "/admin/clients?kept=2"`.
     - The row's aliases are `[f"Zorp Bank {reg}", f"ZB{reg}"]`.
     - `"Kept 2 former name(s)" in c.get("/admin/clients?kept=2").text`.
     - Building `C(name, aliases, label)` from the row, `an.apply(f"Zorp Bank {reg} said", [row])` returns the label
       followed by `" said"`.
   - `test_renaming_a_referenceable_client_keeps_nothing`: the same rename with `referenceable: "on"` on a
     referenceable client stores `[]` and redirects to `/admin/clients`.
   - `test_case_only_rename_keeps_nothing`: protected `zorp bank {reg}` renamed to `Zorp Bank {reg}` stores `[]`
     and redirects to `/admin/clients`.

   → verify: the same command. The three tests fail on main, except the referenceable one, which guards the
   unchanged path and passes on both.

   Traps: `reg` is a lowercase hex tag, so `_key` of the case-only rename is equal by construction. Names must
   contain `{reg}` so the fixture cleans them up.

## Tests

- `docker compose build -q app && docker compose run --rm app timeout 900 pytest -q -p no:cacheprovider tests/test_anonymise.py`
  is all green.
- The full suite is green.
- The new keep tests fail on main.
- Manual check (optional): rename a protected client on `/admin/clients`. The note appears and the old name is
  listed under its aliases.

## Rollback

Revert the commits. Aliases that were kept stay in the data, which fails safe. An admin can remove them after
reverting, or by ticking referenceable.

## Deviations

None yet.
- Step 3: the three tests share a small `_rename` helper (create + post the rename), placed after the `_add`/`_row` helpers it uses.
