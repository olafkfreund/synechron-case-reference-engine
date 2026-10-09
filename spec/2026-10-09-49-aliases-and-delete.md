---
status: approved
issue: 49
intent: intent/2026-10-09-49-aliases-and-delete.md
---

# Spec: Client registry aliases that contain commas, and removing clients

## Design

Approved decisions: a textarea with one alias per line. Hard delete only when
no case links to the client, with the same 409 version check as `update()`.
No soft "retired" flag, no `on delete set null`, and no schema change. One
task, not split.

### Aliases, one per line

1. **`app/clients.py` `clean()`, line 16.** Change `aliases.split(",")` to
   `aliases.splitlines()`. The strip, the drop-empty step and the de-duplicate
   step stay the same, and so do all later checks (lines 17-32). Each line is
   one alias, and commas are kept.
2. **`app/templates/clients.html`.**
   - Line 8: change the header to "Aliases (one per line)".
   - Line 11: replace the text input with
     `<textarea form="{{ f }}" name="aliases" rows="2" aria-label="aliases">{{ aliases | join('\n') }}</textarea>`.
   - Line 17: the same textarea for the new row (aria-label "new aliases").
   - Lines 5-7, help text: add "one per line; commas are part of the alias".

   The `form=` attribute works on `<textarea>`, so the existing pattern of one
   hidden form per row (line 22) stays.
3. **Existing stored aliases.** `clients.aliases` is `text[]`
   (`sql/schema.sql:19`) and is not touched. Today each element was produced by
   splitting on commas, so none contains a comma or a newline. Joining with
   `\n` and splitting by line gives back the identical array. No migration is
   needed. `load_clients()` and every reader in `app/anonymise.py` work on
   array elements and never split strings, so they are unchanged.

### Delete

4. **`app/clients.py`: new `POST /admin/clients/{cid}/delete`,** with form
   field `v` and `require("admin")`. In one connection:
   ```sql
   delete from clients cl where id=%s and {VERSION}=%s
     and not exists (select 1 from cases where client_id = cl.id)
   ```
   - rowcount 1: 303 to `/admin/clients`.
   - rowcount 0: work out why, in this order:
     - the row is missing: 404 "no such client";
     - `select count(*) from cases where client_id=%s` is greater than 0:
       400 "N case(s) use this client; make it non-referenceable instead of
       deleting it";
     - otherwise: 409 "client changed; reload the page".
   - Catch `psycopg.errors.ForeignKeyViolation` and return the same 400. This
     covers an approval that links a case between the check and the commit.
     The foreign key at `sql/schema.sql:51` is the final guard, because Postgres
     enforces it.
5. **`app/templates/clients.html`.** In the last cell of each row, add
   `<button form="d{{ id }}">Delete</button>` next to Save. In the hidden forms
   block (line 22), add
   `<form id="d{{ id }}" method="post" action="/admin/clients/{{ id }}/delete">`
   with the same hidden `v`. There is no JavaScript and no confirm page. The
   "unlinked only" rule plus the version check are the guard against accidents
   (intent constraint).

**Rebase with #41.** #41 changes `create()` (lines 44-55: a `next` parameter
and the redirect) and the review page. This spec changes `clean()` line 16,
the alias cells in `clients.html`, and adds a route after `update()`.
The hunks are separate. #41's inline form sends no `aliases`, so it does not
depend on the new format. Whichever branch merges second rebases onto `main`
and re-runs the full test suite. No manual merge is expected.

## Alternatives rejected

- **Escaping commas with `\,`:** admins would have to learn an escape rule, it
  needs a parser, and a stray backslash breaks it quietly.
- **One input per alias:** it needs a variable number of fields. Without JS
  that means an extra blank row and indexed field names. More code, no gain.
- **Accepting both commas and newlines:** a comma would still split
  `Smith, Jones & Co`, and that is the bug.
- **A soft `retired` flag:** a schema change plus filtering in every reader,
  only to hide linked clients. Not needed (intent question 2).
- **`on delete set null` on `cases.client_id`:** linked cases would lose
  their label, and their client's names would stop being anonymised.

## Risks

- **An unlinked client's name stops being anonymised.** "Unlinked" only means
  that no approved case resolved to this client. Its name or aliases can still
  appear in the text of other cases (as a partner, or in a mention that did not
  resolve). After a delete, `apply()`/`blocked()` (`app/anonymise.py:49-65`) no
  longer protect them. This follows from the approved rule. It is safe for the
  main need (removing a mistaken or duplicate entry) and is the admin's choice
  otherwise. The help text says "deleting stops hiding this name everywhere".
  If the approver wants it stricter, refuse delete for non-referenceable
  clients unless they were created recently. Not designed here.
- **Pasted comma lists:** an admin who pastes `Acm, AcmeCo` now gets one
  alias. The header and the help text say "one per line". `clean()` still
  rejects duplicates and labels that contain a name.
- **The row height of the textarea** changes the table layout slightly. This
  is cosmetic only.
- **Hosts:** the app only, with no schema change. The live dev stack picks the
  change up on the next image build.

## Verification

`docker compose build app && docker compose run --rm app pytest`. The tests
use made-up names with the `reg` suffix:

- `tests/test_anonymise.py:100`: change the aliases to
  `" Acm\n\nAcm\nAcmeCo "`. The assert on line 102 stays
  `["Acm", "AcmeCo"]`.
- New: an alias `Smith, Jones & Co {reg}` saves as one element, appears in the
  textarea, and survives a second save unchanged (round trip through `v`).
  `an.apply` replaces it whole, and the text `Jones & Co` on its own is no
  longer touched.
- New: delete an unlinked client gives 303 and the row is gone. A stale `v`
  gives 409. An unknown id gives 404. A reviewer gets 403.
- New: delete a client linked to a case (set up as in
  `tests/test_review.py:178-191`) gives 400, and the client and the case's
  `client_id` are unchanged.
- The existing registry tests (`tests/test_anonymise.py:93-134`) pass.
