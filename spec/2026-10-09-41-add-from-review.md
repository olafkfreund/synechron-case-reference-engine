---
status: approved
issue: 41
intent: intent/2026-10-09-41-add-from-review.md
---

# Spec: Add an unlisted organisation to the client registry from the review page

## Design

Approved decisions: an inline form that posts to `POST /admin/clients`, a safe
`next` that can only be `/review/<int>`, a warning about partners and vendors,
admin only, and no referenceable/logo checkboxes (both default to off).

1. **`app/review.py` `review_detail()`, lines 121-127.** Stop folding the
   unlisted names into `notes`. Compute them into their own list and pass it to
   the template as `unlisted=`. Keep the existing guard `if r[5]`: the list is
   only computed while the case is reviewable. `notes` stays
   `case.needs_attention`. No other change. The page still makes no LLM call.
2. **`app/templates/review_detail.html`, after line 6.** Render one
   `<div class="note">` per unlisted name, with the same text as today
   (`organisation not in client registry: {{ o }}`). This keeps
   `tests/test_anonymise.py:48` valid. Inside the note, only when
   `"admin" in user.roles` (`page()` already passes `user`, see
   `app/review.py:28-29`), add a plain form:
   `<form method="post" action="/admin/clients">` with hidden `name={{ o }}`,
   hidden `next=/review/{{ id }}`, a required text input
   `anonymised_label` (aria-label "label"), the button "Add as client", and
   one line of warning: "Only for clients. A partner or vendor added here is
   anonymised in every output." Jinja autoescaping covers the hidden value.
   There is no JavaScript.
3. **`app/clients.py` `create()`, lines 44-55.** Add the parameter
   `next: str = Form("")`. After the insert, redirect (303) to `next` only if
   `re.fullmatch(r"/review/\d+", next)`. Otherwise redirect to
   `/admin/clients`, as today. Validation stays `clean()` (lines 14-33), and
   the role check stays `require("admin")`. No new route.

The flow: an admin types a label and submits. `clean()` validates it and the
row is inserted. The browser returns to the case. `unlisted()`
(`app/anonymise.py:89-95`) now matches the name, so the note is gone. The case
links to the new client at approval through `resolve()`
(`app/review.py:188-191`), unchanged. A failed validation returns the same
400 `{"detail": ...}` as the registry page does today.

**Rebase with #49.** #49 changes `clean()` line 16 (split aliases by line),
the alias input in `clients.html`, and adds a delete route after `update()`.
This spec only changes the `create()` signature and redirect, and the review
page. The hunks do not overlap, and this form sends no `aliases`, so either
alias format works. Whichever branch merges second rebases onto `main` and
re-runs the full test suite. No manual merge is expected.

## Alternatives rejected

- **A link that prefills `/admin/clients?name=`:** the admin leaves the case
  and has to find it again. That is not "from the note".
- **A new route such as `POST /review/{cid}/add-client`:** it would be a second
  insert path. It would have to call the same `clean()` and the same insert,
  which duplicates `create()` for no gain.
- **Using the `Referer` header to return:** the client controls it, and it can
  be missing. A `next` field with a strict pattern is explicit and can be
  tested.
- **Dismissing notes for organisations that are not clients:** out of scope
  (intent question 2). The warning is the only mitigation here.

## Risks

- **Open redirect through `next`:** the full-match pattern allows only
  `/review/` plus digits, so there is no `//host`, scheme or `..`. A test
  covers this.
- **An admin adds a vendor by mistake:** that vendor's name is then anonymised
  in every output. The warning and the referenceable-off default limit the
  harm. The admin can fix it on `/admin/clients`. Removing the entry is #49.
- **CSRF:** the form posts from the same origin, so the existing origin check
  (`app/main.py:104-107`) already applies. Nothing changes.
- **Hosts:** the app only, with no schema change. The live dev stack picks the
  change up on the next image build.

## Verification

`docker compose build app && docker compose run --rm app pytest`. New or
extended tests, using made-up names (`Initech {reg}`, as in the existing
tests):

- On a reviewable case with an unlisted organisation, an admin's page shows
  the note and the form, including `name="next" value="/review/<id>"`. A
  reviewer without the admin role sees the note but no form.
- `POST /admin/clients` with `next=/review/<id>` returns 303 to `/review/<id>`.
  Afterwards the case page no longer shows that note.
- A `next` of `https://evil.example`, `//evil.example`, `/review/1/../x` or an
  empty value returns 303 to `/admin/clients`.
- A reviewer posting the form gets 403 (as in `test_admin_only`). A label that
  contains the name gets 400 and no row is created.
- Existing: `tests/test_anonymise.py:45-48` and the registry tests at 93-134
  still pass.
