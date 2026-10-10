---
status: approved
issue: 115
author: olafkfreund
---

# Intent: Client labels are not re-checked when a protected client is added later

## Problem

A client's label is shown in outputs in place of a name. The label must never
contain a protected client's name. `app/clients.py:23-28` (`clean()`) enforces
this, but in one direction only: it checks the label of the client being
saved against the names of the other protected clients.

It never runs the other way. When a client is saved as protected, by being
created non-referenceable, edited to add an alias, or edited to clear
**Referenceable**, its names are not checked against the labels of the other
clients. A label that was valid when it was saved can then contain a
protected name.

With made-up clients:
- referenceable "Zorp Logistics" has the label "a Quill partner";
- protected "Quill" is added later, with the label "a publisher".

`anonymise.apply()` (`app/anonymise.py:53-62`) inserts labels while it
rewrites names, in longest-first order. What an output then shows depends on
the name lengths:

- **The protected name is shorter than the name whose label holds it.** The
  label is inserted first, and the shorter name is rewritten inside it.
  Outputs read "a a publisher partner". Nothing leaks, but the text is
  garbled. This is the case in the issue.
- **The protected name is longer.** It is rewritten before the label is
  inserted, so the inserted label still contains "Quill". `blocked()` then
  fails closed:
  - generating any output that includes the case is withheld
    (`app/render.py:127`);
  - search shows "[withheld]" or "a client" for it (`app/search.py`,
    `clean()`).

Either way, nothing leaks. Every affected case silently degrades until an
admin finds the label and edits it, and nothing tells them which label it is.

The same gap applies to a protected client's own label, which is inserted
every time one of its names is rewritten. It also applies to referenceable
labels, which have been used in outputs since #109.

## Proposed outcome

- An admin cannot create a state where a client's label contains another
  client's protected name, whichever client is saved first.
- When a save would create that state, the admin sees which clients'
  labels conflict, so they know what to edit.
- Outputs never show a garbled label like "a a publisher partner" because of
  the order the clients were saved in.

## Affected users and systems

- Admins, on the client registry: `app/clients.py` (`clean()`, `create`,
  `update`) and `app/templates/clients.html`.
- Bid team and reviewers, indirectly: through generated output and search
  results.
- `app/anonymise.py` (`has_name`) is reused, not changed.
- Tests: `tests/test_clients.py`, or wherever the client registry tests live.
- Not affected: the extraction, crawlers and infra.

## Constraints

- Anonymisation stays fail-closed. This change only prevents a bad registry
  state; `blocked()` stays the last check on every output.
- The rule lives in one place that every save goes through (`clean()`), not in
  each route.
- Server-rendered forms only, with no JavaScript. Errors use the existing
  400 response.
- Only made-up client names in tests, docs and the issue, because the repo is
  public.
- The admin-only page can name the conflicting clients. These are admin
  users, who already see every name on the registry.
- `scripts/seed_demo.py` inserts clients directly and bypasses `clean()`. Its
  made-up labels must not conflict with each other. A test can pin this.

## Open questions

1. **What happens when a save would put a protected name inside another
   client's label?**
   - **A. Refuse the save.** It returns 400 and names the clients whose
     labels contain the new protected name. This is the same rule
     `clean()` already applies, run the other way. The admin edits those
     labels first, then saves again. It is the smallest change, and the bad
     state can never exist.
   - **B. Save, then warn.** It redirects back to the registry with a notice
     listing the conflicting labels. The bad state exists until the admin
     acts, and outputs degrade meanwhile.
   - **C. Show conflicts on the registry page.** Each row whose label
     contains a protected name gets a badge, worked out when the page loads.
     This also catches rows written outside `clean()`, such as seed data or
     direct SQL, but it prevents nothing on its own.

   **Recommendation: A.** C is a follow-up only if rows written outside
   `clean()` turn out to matter. Production isn't deployed, and the demo
   seed can be pinned by a test.

2. **Which labels are checked?** Every other client's label, referenceable
   or not, against the names and aliases of the client being saved, if that
   client is protected after the save. **Recommendation: all labels.** A
   referenceable label is used in outputs since #109, and checking all of
   them is simpler and fails closed.

## Approved answers

1. A: refuse the save with 400, naming the clients whose labels would contain the new protected name. Showing conflicts on the registry page (C) is a possible follow-up, not part of this.
2. Every other client's label, referenceable or not.
