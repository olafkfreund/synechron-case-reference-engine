---
status: approved
issue: 41
spec: spec/2026-10-09-41-add-from-review.md
---

# Plan: Add an unlisted organisation to the client registry from the review page

Branch `feat/41-add-from-review`, rebased on `origin/main` after #74 merged.
The line numbers below are from that base.

## Approved decisions

- Each "organisation not in client registry: X" note on `/review/<id>` gets
  an inline plain HTML form. It posts to the existing `POST /admin/clients`
  with a hidden `name=X`, a required `anonymised_label` input, and a hidden
  `next=/review/<id>`.
- The form is shown only to users with the `admin` role. The server still
  enforces the role with `require("admin")` on `create()`. Reviewers who are
  not admins see the note as it is today.
- The form shows a warning: "Only for clients. A partner or vendor added here
  is anonymised in every output."
- There are no referenceable or logo checkboxes on the form, so both default
  to false. The form sends no `aliases`.
- `create()` redirects to `next` only when `re.fullmatch(r"/review/\d+", next)`
  matches. In every other case it redirects to `/admin/clients`, as it does
  today.
- Validation stays in `clean()`. There is no new route and no schema change.
  A failed validation returns the same 400 as the registry page does.
- The note text stays exactly `organisation not in client registry: X`
  (`tests/test_anonymise.py:48` asserts it).
- The unlisted list is still computed only while the case is reviewable (the
  `if r[5]` guard), and there is still no LLM call on page view.
- Rebase with #49: #49 changes `clean()` line 16, the alias cells in
  `clients.html`, and adds a delete route after `update()`. This plan only
  touches `create()` in `app/clients.py`. Whichever branch merges second
  rebases and re-runs the full test suite.

## Coder handoff

Yes. Four steps edit files, and four files are touched (`app/clients.py`,
`app/review.py`, `app/templates/review_detail.html`,
`tests/test_anonymise.py`). Start one `coder` with this plan and step 1, and
send it steps 2-4 with SendMessage.

## Steps

1. **`app/clients.py` lines 1-3 and 45-55 (`create()`).**
   - Add `import re` at the top.
   - Add the parameter `next: str = Form("")` to `create()`.
   - Replace the final line with:
     `return RedirectResponse(next if re.fullmatch(r"/review/\d+", next) else "/admin/clients", status_code=303)`.
   - Add a one-line comment: `# only back to a review page: never an open redirect`.

   → verify by `docker compose build app && docker compose run --rm app pytest tests/test_anonymise.py`
   (the existing registry tests still pass).
   Traps: compose has no bind mount, so build before each test run. Use
   `fullmatch`, not `match`: `/review/1/../x` and `/review/1x` must fail.

2. **`app/review.py` lines 125-131 (`review_detail()`).**
   - Replace the `notes += [...]` block with
     `unlisted = anonymise.unlisted([*case.organisations, case.client_mention.value or ""], registry) if r[5] else []`.
     Keep the comment about no LLM call.
   - `notes = list(case.needs_attention)` stays.
   - Add `unlisted=unlisted` to the `page(...)` call.

   → verify by the step 1 command.
   Traps: do not drop the `r[5]` guard.

3. **`app/templates/review_detail.html` line 6.** Keep the `notes` loop as it
   is. After it, add:
   ```
   {% for o in unlisted %}<div class="note">organisation not in client registry: {{ o }}
   {% if "admin" in user.roles %}<form method="post" action="/admin/clients">
   <input type="hidden" name="name" value="{{ o }}"><input type="hidden" name="next" value="/review/{{ id }}">
   <input type="text" name="anonymised_label" aria-label="label for {{ o }}" placeholder="label, e.g. a retail bank" required>
   <button>Add as client</button> <small>Only for clients. A partner or vendor added here is anonymised in every output.</small>
   </form>{% endif %}</div>{% endfor %}
   ```
   Keep `organisation not in client registry: {{ o }}` on one line, so the
   existing assert still finds the text.

   → verify by `docker compose build app && docker compose run --rm app pytest tests/test_anonymise.py tests/test_review.py`.
   Traps: no JavaScript (no `onclick` or `confirm()`). Rely on Jinja
   autoescaping and do not add `|safe`.


   *Done (coder, steps 2 and 3 in one commit):* `tests/test_anonymise.py`
   and `tests/test_review.py` give 35 passed. *Deviation:* step 2 alone
   fails `test_review_page_shows_unlisted_organisations_without_llm`,
   because the note text moves out of `notes` and only the step 3 template
   renders it again. So steps 2 and 3 are committed together.

4. **`tests/test_anonymise.py`, after line 48.**
   - Import `DOCS` from `tests.test_review` (line 7).
   - Add a test using the `make` and `reg` fixtures:
     - Create the case with `cid = make(data=case_data(organisations=[f"Initech {reg}"]))`.
     - The admin page `client([ADMIN, DOCS]).get(f"/review/{cid}")` contains
       `name="next" value="/review/{cid}"`.
     - The reviewer page `client(R)` contains the note but not
       `action="/admin/clients"`.
     - The admin posts `name=f"Initech {reg}"`,
       `anonymised_label="a software firm"`, `next=f"/review/{cid}"` with
       `follow_redirects=False`. The response is 303 with location
       `/review/{cid}`. The admin page no longer shows the note.
   - Add a parametrized test over `next` values `"https://evil.example"`,
     `"//evil.example"`, `"/review/1/../x"`, `"/review/"` and `""`. Each post
     (with a fresh `Globex {reg} {i}` name) returns a 303 with location
     `/admin/clients`.
   - Add a test: a reviewer posting the same form gets 403. An admin whose
     label contains the name (`f"the Initech {reg} people"`) gets 400, and
     `find(reg)` is empty.

   → verify by `docker compose build app && docker compose run --rm app pytest`.
   Traps: names must be made up (`Initech`/`Globex` plus the `reg` suffix),
   because the `reg` teardown deletes clients by that suffix. Clients created
   here are never linked to a case, so the teardown cannot hit the foreign
   key.

## Tests

`docker compose build app && docker compose run --rm app pytest`. The full
suite must be green, including the new tests in step 4 and the existing
`test_review_page_shows_unlisted_organisations_without_llm`,
`test_admin_only` and `test_create_update_and_validation`. Do not run
`docker compose up` or `down`: the dev stack is live.

## Rollback

`git revert` the merge commit. There is no schema or data change. Clients that
admins added through the form stay as ordinary registry rows and can be edited
on `/admin/clients`.
