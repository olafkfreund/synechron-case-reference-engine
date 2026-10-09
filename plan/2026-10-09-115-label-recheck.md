---
status: draft
issue: 115
spec: spec/2026-10-09-115-label-recheck.md
---

# Plan: Client labels are not re-checked when a protected client is added later

`clean()` in `app/clients.py` already refuses a label that contains another client's protected name. It
never checks the reverse: whether the client being saved is protected, and its name or an alias appears in
another client's label. Then every output for that other client is garbled, or withheld by `blocked()`, and
nothing tells the admin why.

Approved decisions, copied from the spec:

- **Refuse the save.** Return 400 naming the clients whose labels contain the saved client's name or an
  alias. Don't save and warn, and don't add a badge on the registry page; the badge is a possible follow-up
  only.
- **Check every other client's label**, referenceable or not. The query has no `referenceable` filter.
- **When it runs:** on every save of a protected client, i.e. `not referenceable`. That covers creating a
  protected client, renaming it, adding an alias, and unticking **Referenceable**. A referenceable client's
  names aren't protected, so its save skips the check.
- **Matching:** `anonymise.has_name()` (`app/anonymise.py:44-46`), the same folded whole-word match that
  the forward check (`app/clients.py:27`) and `blocked()` use.
- **Message:** client *names* only, never labels. It's a plain `HTTPException(400, ...)`, so FastAPI
  returns JSON `{"detail": ...}`, like the four existing `clean()` errors, and nothing is put into HTML.
- **Scope:** `clean()` and its two callers. No other app file, no schema or infrastructure change, and no
  change to how registry errors are shown.

## Steps

1. `app/clients.py`:
   - **`:16`:** change the signature to `def clean(name, aliases, label, referenceable, cid=None):`.
   - **After `:28`** (the forward check), still before the `taken` check at `:29`, add the reverse check:

     ```python
     if not referenceable:  # this client's names are protected: no other label may contain them (#115)
         with db.connect() as conn:
             labels = conn.execute("select name, anonymised_label from clients where id is distinct from %s",
                                   (cid,)).fetchall()
         hit = sorted(nm for nm, lbl in labels if anonymise.has_name(lbl, [name, *aliases]))
         if hit:
             raise HTTPException(400, "these clients' labels contain this client's name or an alias; "
                                      f"change them first: {', '.join(hit)}")
     ```

   - **`:50`** (`create`): `clean(name, aliases, anonymised_label, referenceable)`.
   - **`:68`** (`update`): `clean(name, aliases, anonymised_label, referenceable, cid)`.

   Verify by `grep -n "clean(" app/clients.py`, which should show exactly these three lines, then
   `docker compose build app && docker compose run --rm app pytest -q tests/test_anonymise.py`. The
   existing tests all pass.

   Traps:
   - `aliases` is already the cleaned list by this point (`:18`), so pass `[name, *aliases]`, not the raw
     form string.
   - `cid=None` on create, so `id is distinct from %s` matches every row. Keep `is distinct from`, not
     `<>`, which is NULL for a None cid.
   - The review page's "add unlisted client" form posts to the same `create` route. Nothing else calls
     `clean()`, which the grep in the verify step confirms.

2. `tests/test_anonymise.py`: after `test_create_update_and_validation` (`:158-176`), add four tests using
   the `reg` fixture (`:137-143`), `find()` (`:146-150`), `client([ADMIN])` and made-up names. Every
   client's name must contain `{reg}` so the fixture deletes it.
   - `test_new_protected_name_in_another_label_refused`:
     1. Create referenceable `f"Zorp Logistics {reg}"` with label `f"a Quill{reg} partner"`; expect 303.
     2. Create protected `f"Quill{reg}"` with label "a publisher"; expect 400.
     3. Check that `detail` contains `f"Zorp Logistics {reg}"` and that no `Quill{reg}` row exists in
        `find(reg)`.
   - `test_unticking_referenceable_refused_when_name_in_a_label`:
     1. Create referenceable `f"Quill{reg}"`, then the logistics client as above; both return 303.
     2. Post an update of the Quill row without `referenceable`, using its `v` from `find`; expect 400.
     3. Check that the row is still `referenceable is True`.
   - `test_new_alias_in_another_label_refused`:
     1. Create protected `f"Pine{reg}"` with no aliases, and referenceable `f"Cedar {reg}"` with label
        `f"a Fir{reg} firm"`; both return 303.
     2. Update Pine with `aliases=f"Fir{reg}"`; expect 400.
     3. Check that Pine's aliases are still `[]`.
   - `test_label_recheck_leaves_other_saves_alone`:
     - A protected `f"Oak {reg}"` whose names are in no label saves with 303, on create and on an update.
     - A referenceable `f"Elm {reg}"` whose name is in another client's label (`f"an Elm {reg} unit"`)
       saves with 303.

   Use `follow_redirects=False` so a 303 is a 303.

   Verify by `docker compose build app && docker compose run --rm app pytest -q tests/test_anonymise.py -k
   "refused or leaves_other"` (all pass). Then confirm that the first three fail on main's code:
   1. `git checkout origin/main -- app/clients.py`;
   2. rebuild and run the same `-k`; expect 3 failed and 1 passed;
   3. `git checkout HEAD -- app/clients.py` and rebuild.

   Traps:
   - The test DB persists between runs and holds other tests' clients. Keep every name and alias unique
     with `{reg}`. A generic alias such as "Fir" could match a leftover label from another test.
   - In the third test, Cedar's label "a Fir{reg} firm" must pass Cedar's own forward check. It does,
     because Pine has no alias "Fir{reg}" yet when Cedar is created.
   - Order matters in the first test: create the logistics client before Quill exists, or its own forward
     check refuses the label.
   - No bind mount: rebuild the image before every run. Use only the `refs-engine-115` compose project,
     and never `up` or `down`. Never touch `refsdemo` or `refsdev`.

## Tests

```
docker compose build app && docker compose run --rm app pytest -q tests/test_anonymise.py
docker compose build app && docker compose run --rm app timeout 900 pytest -q
```

Expected:
- **`tests/test_anonymise.py`:** passes, including the four new tests, and `test_create_update_and_validation`
  is unchanged.
- **Full suite:** passes with main's 560 + 4 = 564.
- **On main's `app/clients.py`:** the first three new tests fail.

## Rollback

Revert the commits. The change is two lines of signature and callers plus one block in `clean()`, and the
tests. There is no data or schema change, and no stored row is altered.
