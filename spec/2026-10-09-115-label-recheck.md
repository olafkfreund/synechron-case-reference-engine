---
status: draft
issue: 115
intent: intent/2026-10-09-115-label-recheck.md
---

# Spec: Client labels are not re-checked when a protected client is added later

## Design

The approved answers are:
- refuse the save with a 400 that names the clients whose labels would contain the new protected name;
- check every other client's label, whether that client is referenceable or not.

The rule goes in `clean()` (`app/clients.py:16-35`), which both save paths call:
- `create`, at `:50`, which also covers the review page's "add unlisted client" form, since that posts to the same route;
- `update`, at `:68`.

1. **New parameter.** `clean()` gains `referenceable` as a parameter:
   `clean(name, aliases, label, referenceable, cid=None)`. Both callers already have it as a form field and
   pass it through (`:50`, `:68`).
2. **The reverse check.** It runs after the existing label checks, and only when `not referenceable`. A
   referenceable client's names are not protected, so its save can't create this conflict. The query sits
   in the same `with db.connect()` block as the existing `others` query (`:24-26`):

   ```python
   if not referenceable:  # this client's names are protected: no other label may contain them (#115)
       labels = conn.execute("select name, anonymised_label from clients where id is distinct from %s",
                             (cid,)).fetchall()
       hit = sorted(nm for nm, lbl in labels if anonymise.has_name(lbl, [name, *aliases]))
       if hit:
           raise HTTPException(400, "these clients' labels contain this client's name or an alias; "
                                    f"change them first: {', '.join(hit)}")
   ```

   - **Every save:** it runs on every save of a protected client, not only when a client becomes
     protected. So all the ways a client can become protected are covered: creating a protected client,
     adding an alias, renaming it, and unticking **Referenceable**.
   - **Matching:** `anonymise.has_name()` (`app/anonymise.py:44-46`) is the folded, whole-word match that
     the existing forward check (`:27`) and `blocked()` use. So "contains" means the same in every check.
   - **Every label:** every other client's label is checked, referenceable or not (answer 2), so there is
     no `referenceable` filter in the query.
3. **The message names client names, not labels.** The admin who saves can already see every client name
   on the registry page. The 400 is the same kind of error as the four existing `clean()` errors. Like
   them, it is returned by FastAPI's default handler as JSON `{"detail": ...}`, so the names are never
   placed into HTML.

The change touches no other file in `app/`. The tests go in `tests/test_anonymise.py`, next to
`test_create_update_and_validation` (`:158`), and use its `reg` and `find` helpers (`:138-150`).

## Alternatives rejected

- **B: save, then warn with a notice.** The bad state would be stored, and every render for the affected
  case would be garbled or withheld until someone acts. Answer 1 chose refusal.
- **C: conflict badges on the registry page.** Approved as a possible follow-up only. It would also catch
  rows written by direct SQL or `scripts/seed_demo.py`, but on its own it prevents nothing.
- **Run the check only when `referenceable` changes from true to false.** That would miss a new alias or a
  rename on a client that is already protected, which are the same conflict. Checking on every save of a
  protected client is simpler and covers all of these.
- **Check only referenceable clients' labels.** A non-referenceable client's label is printed in place of
  its names, so a protected name inside it leaks or gets withheld in the same way. Answer 2 says to check
  all of them.
- **A database constraint or trigger.** A folded, whole-word name match can't be written as a constraint,
  and a trigger would copy `anonymise`'s matching rules into SQL.

## Risks

- **Existing conflicts block unrelated edits.** If a conflict already exists, for example from seed data or
  from before this fix, every later save of that protected client returns 400 until the conflicting
  label is changed. That is the intended effect: the 400 tells the admin which client to fix. The demo seed
  (`scripts/seed_demo.py:58`, data in `demo/cases.json`) writes clients directly. I checked it: the only
  protected seed clients are Fabrikam and Litware, and no seed label contains either name or their aliases.
- **Error display.** As with the existing `clean()` errors, the browser shows the JSON 400 on a plain page.
  That is not great UX, but it is consistent with the current registry. Moving the registry's errors to
  notices, as Sources does since #96, is separate work.
- **Cost.** One query and one `has_name()` per other client on each save of a protected client. That is
  milliseconds at hundreds of clients, and it runs only on admin saves, never on renders.
- **No host impact.** This is app code only; there are no schema or infrastructure changes.

## Verification

New tests in `tests/test_anonymise.py`, using made-up names with the `reg` tag. On main each of these
saves returns 303, so each test fails there:

1. **Create:** create referenceable "Zorp Logistics {tag}" with label "a Quill{tag} partner". Then create
   protected "Quill{tag}" (label "a publisher"). The second save returns 400, its `detail` names
   "Zorp Logistics {tag}", and no "Quill{tag}" row exists.
2. **Untick Referenceable:** create referenceable "Quill{tag}" first, then the logistics client as in test 1.
   Updating "Quill{tag}" with **Referenceable** unticked returns 400, and the row is still referenceable.
3. **New alias:** create protected "Pine{tag}" with no aliases, and another client with label
   "a Fir{tag} firm". Updating "Pine{tag}" to add the alias "Fir{tag}" returns 400.
4. **Unchanged cases:**
   - A protected client whose names appear in no other label saves with 303.
   - A referenceable client whose name appears in another label saves with 303, because its names are not
     protected.

Then run the full suite: `docker compose build app && docker compose run --rm app timeout 900 pytest`.
All tests should pass, with 560 on main plus the new ones.
