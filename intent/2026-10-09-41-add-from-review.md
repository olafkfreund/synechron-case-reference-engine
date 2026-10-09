---
status: draft
issue: 41
author: olafkfreund
---

# Intent: Add an unlisted organisation to the client registry from the review page

## Problem

When a case is open for review, `review_detail()` (`app/review.py` lines
122-124) adds a note `organisation not in client registry: <name>` for each
organisation that `anonymise.unlisted()` (`app/anonymise.py` lines 89-95)
cannot match exactly to a registry name or alias. The template shows the note
as plain text (`app/templates/review_detail.html` line 6), so the note has no
action.

To act on it, an admin has to leave the case, open `/admin/clients`
(`app/clients.py` lines 36-41), retype or paste the name into the "new" row
(`app/templates/clients.html` lines 16-21), make up a label, and then find the
case again. This costs time, and the copy can introduce typos. A typo matters:
`resolve()` (`app/anonymise.py` lines 83-86) links a case to its client at
approval (`app/review.py` lines 188-191) only on an exact folded match.

The stakes: a non-referenceable client that is not in the registry is not
anonymised by `apply()`/`blocked()`. When an admin skips this step, a real
client name can reach generated output.

"One-click" cannot be done literally. `create()` (`app/clients.py` lines
44-55) needs an `anonymised_label`, and `clean()` (lines 14-33) rejects a
label that contains the client's name, contains another protected client's
name, or reuses a name or alias. Someone has to type the label.

## Proposed outcome

- An admin who is reviewing a case sees an add control beside each
  "organisation not in client registry" note. The control has the name filled
  in and asks only for the label (referenceable stays off by default).
- Submitting it creates the client through the same validation as
  `/admin/clients`. The admin lands back on the same case, where that note is
  gone.
- If validation fails (duplicate, label contains the name, and so on), the
  admin gets the same 400 message as on the registry page, and nothing is
  created.
- Reviewers without the `admin` role see the note as they do today, with no
  control. The endpoint still returns 403 for them.

## Affected users and systems

- Users: reviewers who also hold the `admin` role.
- `app/review.py` `review_detail()` (lines 110-127): pass the unlisted names
  separately from `needs_attention`, plus whether the user is an admin.
- `app/templates/review_detail.html` line 6: the notes loop.
- `app/clients.py` `create()` (lines 44-55): today it always redirects to
  `/admin/clients`.
- Tests: `tests/test_review.py`, `tests/test_anonymise.py` (registry tests at
  lines 93-134).
- No schema change (`sql/schema.sql` `clients`, lines 16-23).

## Constraints

- Must reuse `clean()` and `create()`. No second insert path with weaker
  validation.
- Must require the `admin` role on the server side. Hiding the control is not
  enough.
- A redirect back must go only to `/review/<int>`, never to a user-supplied
  URL (no open redirect).
- No JavaScript in templates: plain HTML forms only.
- No new dependencies.
- Test data must be public or made up (for example `Acme`/`Globex` with the
  test's random suffix, as in the existing tests).
- Tests run with `docker compose build app && docker compose run --rm app pytest`.
- Overlap with #49: both edit `app/clients.py` and the registry form contract.
  #49 may change how `aliases` is submitted. This task should send no aliases
  (name and label only) so it does not depend on #49's alias format. The two
  branches will touch the same file. Whichever merges second rebases.

## Open questions

1. **Inline form or prefilled link?**
   - (a) An inline form on the note (hidden name, label input, Add button) that
     posts to the existing `POST /admin/clients` with a `next=/review/<id>`
     field.
   - (b) A link to `/admin/clients?name=<org>` that prefills the new row. The
     admin finishes there and navigates back.

   Lean: (a). It keeps the reviewer on the case and needs one validated field.
   (b) is a few lines smaller but is not "from the note".
2. **Organisations that are not clients.** The `organisations` list also
   contains partners, vendors and Synechron itself. Adding "Microsoft" as a
   non-referenceable client would anonymise it everywhere. Should the control
   say "add as client" with a short warning, or do we also need a way to
   dismiss a note ("not a client")?

   Lean: warning text only. Dismissal is a separate issue if it is wanted.
3. **Referenceable and logo checkboxes on the inline form?**

   Lean: no. Default to off (the safe side) and edit on `/admin/clients`.
