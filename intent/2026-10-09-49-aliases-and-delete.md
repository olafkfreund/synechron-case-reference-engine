---
status: draft
issue: 49
author: olafkfreund
---

# Intent: Client registry aliases that contain commas, and removing clients

## Problem

**Aliases cannot contain a comma.** On `/admin/clients`, aliases are one text
input labelled "comma-separated" (`app/templates/clients.html` lines 8, 11
and 17). `clean()` splits on `,` (`app/clients.py` line 16), so an alias such
as `Acme Holdings, Inc.` or `Smith, Jones & Co` is stored as two aliases. The
fragments then become patterns in `anonymise._pattern()`
(`app/anonymise.py` lines 33-37). The result: the real alias is never matched
as a whole by `resolve()`/`unlisted()` (lines 83-95), and the fragments
(`Inc.`, `Jones & Co`) are anonymised wherever they appear. The database
column already handles commas (`aliases text[]`, `sql/schema.sql` line 19).
Only the form round-trip breaks them: the template joins with `', '`, and the
next save splits the alias again.

**Clients cannot be removed.** `app/clients.py` has create (lines 44-55) and
update (lines 58-76), but no delete. A wrong or duplicate entry stays forever.
Its only workaround is renaming it to something else. A direct
`delete from clients` would fail for any client a case links to:
`cases.client_id references clients(id)` (`sql/schema.sql` line 51) has no
`on delete` action.

Removing a client is not harmless:

- The client's name and aliases stop being anonymised by `apply()`/`blocked()`
  (`app/anonymise.py` lines 49-65). Approved cases about a non-referenceable
  client would then show its real name in generated output.
- Linked cases lose their label. `app/render.py` lines 374-385 and
  `app/search.py` lines 71-75 fall back to "a client" when the join finds
  nothing.

## Proposed outcome

- An admin can enter an alias that contains a comma, save, reload, and see it
  unchanged as a single alias. Anonymisation, `resolve()` and `unlisted()`
  treat it as one name.
- An admin can remove a registry entry from `/admin/clients` with a plain
  form. A concurrent edit returns 409, as `update()` does today.
- After a removal, no linked case breaks (no 500, no lost approval), and no
  name that was protected before becomes visible in outputs.

## Affected users and systems

- Users: admins on `/admin/clients`. Indirectly, everyone who reads generated
  references.
- `app/clients.py` (`clean()` line 16, new delete route),
  `app/templates/clients.html` (alias input and a delete button per row).
- Possibly `sql/schema.sql` (a new column or an FK `on delete` change; the
  schema file runs at every start, so changes must be idempotent
  `alter ... if not exists` style).
- Possibly `app/anonymise.py` `load_clients()` (line 15), if retired clients
  must stay protected but be treated differently.
- Tests: `tests/test_anonymise.py` registry tests (lines 93-134). Line 100
  posts `" Acm, ,Acm,AcmeCo "` and depends on the comma split today.

## Constraints

- Must not weaken anonymisation. The names of a removed non-referenceable
  client must stay protected wherever cases about that client exist.
- Must keep `clean()`'s checks (blank, label contains a name, duplicate key)
  for every alias.
- Existing data needs no migration: the stored aliases cannot contain commas
  today.
- No JavaScript in templates: plain forms, so there is no `confirm()` dialog.
  An accidental delete must be prevented some other way (for example, the
  delete is only allowed when it is safe, or it goes through a confirm page).
- No new dependencies.
- Test data must be public or made up.
- Tests run with `docker compose build app && docker compose run --rm app pytest`.
- Overlap with #41: #41 adds an "add to registry" control on the review page
  that posts to `POST /admin/clients`. #41 plans to send name and label only,
  so a new alias format here does not affect it. Both branches edit
  `app/clients.py`; whichever merges second rebases. #41 also makes mistaken
  entries more likely, which raises the need for removal.

## Open questions

1. **Alias input format.**
   - (a) A `<textarea>`, one alias per line, split on newlines.
   - (b) Keep the comma list and allow `\,` as an escape.
   - (c) One input per alias plus a blank extra input.

   Lean: (a). It is one changed line in `clean()` plus the template, has no
   escaping rules for admins to learn, and needs no JS. A side effect: a pasted
   `a, b` becomes one alias. The help text must say so.
2. **What does "retire" mean?**
   - (a) Hard delete, allowed only when no case links to the client.
     Otherwise refuse with "N cases use this client; make it non-referenceable
     instead".
   - (b) A soft `retired` flag: hidden from the normal list, still loaded by
     `load_clients()` so its names stay protected, and linked cases keep their
     label.
   - (c) Delete with `on delete set null` on `cases.client_id`.

   Lean: (a). It fixes mistaken entries, the real need, with no schema change.
   I reject (c): it drops protection of real names. (b) is only worth it if
   admins need linked clients out of the list, so please confirm whether they
   do.
3. **Split into two issues?** The alias format and deletion share only the
   page. Lean: keep them as one task, as the issue asks. Both are small.
