---
status: draft
issue: 164
intent: intent/2026-10-10-164-client-rename-keeps-names.md
---

# Spec: Saving a protected client keeps every name it had

Approved decision: **A**. A name or alias removed from a non-referenceable
client is kept as an alias automatically, and the page says so.

## Design

`app/clients.py`, `update()`:

- The existing 404 check reads the old row instead of `select 1`:
  `select name, aliases, referenceable from clients where id=%s`.
- If the client was non-referenceable before **and** is saved non-referenceable,
  compute the dropped names: every old name or alias whose `anonymise._key()`
  is not among the keys of the submitted name and aliases. Append them to the
  submitted `aliases` text before `clean()` runs, so they get the same
  validation as typed aliases (label must not contain them, not taken by
  another client). A rename that only changes case or spacing drops nothing,
  because `_key` folds those.
- The update keeps its `VERSION` check, so if the row changed between the read
  and the write, the save is a 409 as today and nothing is merged into a
  version the admin did not see.
- After a save that kept names, redirect to `/admin/clients?kept=<n>`.
  `clients_page()` takes `kept: int = 0` and `clients.html` shows a
  `role="status"` note: "Kept {n} former name(s) as aliases, so they stay
  hidden." The kept names are visible in the client's alias list on the same
  page.

Referenceable clients are unchanged, and so is a save that makes a protected
client referenceable: that is the admin's explicit choice to stop hiding its
names.

## Alternatives rejected

- **B, refuse the save with a 400 listing the names:** same protection, one
  more round trip on every rename, which is the common case.
- **C, A plus a "stop hiding these names" checkbox:** adds UI and a way to drop
  protection; can follow as its own change if removing a wrong alias proves a
  real need.

## Risks

- A mistaken alias on a protected client (one that over-replaces an ordinary
  word) can no longer be removed while the client is protected. Workaround
  today: tick referenceable, save without it, untick. Accepted per the
  decision.
- A kept name can make `clean()` refuse the save (for example the new label
  contains the old name). That is correct, and the 400 message already names
  the problem.
- `delete` is unchanged; it already refuses non-referenceable clients.

## Verification

New tests in `tests/test_anonymise.py`, failing on main:

- `test_renaming_a_protected_client_keeps_the_old_name`: create protected
  "Zorp Bank {reg}" with alias "ZB{reg}"; update to name "Zorp Financial {reg}"
  with no aliases. The row's aliases are `["Zorp Bank {reg}", "ZB{reg}"]`, the
  redirect goes to `/admin/clients?kept=2`, the page shows "Kept 2 former
  name(s)", and `an.apply("Zorp Bank {reg} said", [row])` returns the label.
- `test_renaming_a_referenceable_client_keeps_nothing`: the same rename on a
  referenceable client stores no aliases and redirects to `/admin/clients`.
- `test_case_only_rename_keeps_nothing`: "zorp bank {reg}" to "Zorp Bank {reg}"
  on a protected client adds no alias.

Existing `test_create_update_and_validation` is unaffected: its client was
referenceable before the save. Full suite green.
