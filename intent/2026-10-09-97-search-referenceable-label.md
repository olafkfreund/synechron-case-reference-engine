---
status: approved
issue: 97
author: olafkfreund
---

# Intent: Referenceable client shown anonymised in search results

## Problem

In the made-up demo, Woodgrove is referenceable, but its case shows as
"a Dutch bank" in search results (found in a #90 screenshot).

The data is correct. In the `refsdemo` database (read-only `select`):

- `clients`: `4|Woodgrove|{"Woodgrove Bank"}|a Dutch bank|t` (referenceable).
- `cases`: `3|4|Woodgrove|approved`, so case 3 has `client_id = 4` and
  `client_mention = Woodgrove`.
- `scripts/seed_demo.py:85` sets `client_id` with
  `anonymise.resolve(client_mention, load_clients(conn))`, and it resolved
  correctly. Every approved demo case is linked; only the `extracted` ones are
  not.

The registry link, the alias match and the case `client_id` are all fine.
The bug is in how search builds the label:

- `app/search.py:71` selects only `cl.anonymised_label`, never `cl.name` or
  `cl.referenceable`. Line 74 then uses `label or "a client"`. As a result
  every linked client, referenceable or not, shows by its anonymised label.
  `results()` (`app/search.py:131`) passes that label to `search.html:19` (top
  picks) and `:37` (other matches).
- `app/render.py:374-386` (outputs) applies the rule correctly:
  `shown = name if linked and referenceable else (label if linked else "a client")`.
  So downloaded outputs name Woodgrove, but search does not. The rule is
  written twice, and the two copies differ.

## Proposed outcome

- A linked, referenceable client is shown by name in search results (top
  picks and other matches), as it already is in outputs.
- A linked, non-referenceable client still shows its anonymised label. An
  unlinked case still shows "a client".
- A test covers both cases for search, and the existing render tests still
  pass.

## Affected users and systems

- Bid writers using `/search`. `app/search.py`, `app/templates/search.html`
  (template unchanged), `tests/test_search.py`.
- `app/render.py` only if the rule is moved into one shared place.
- No schema change, no data change, no change to the seed.

## Constraints

- Never widen naming. Only a client that is linked and has `referenceable = true`
  may be named. A non-referenceable client must stay anonymised everywhere,
  and an unlinked case stays "a client".
- Leave `anonymise.apply` and `blocked` and the title, summary and outcome
  cleaning in search exactly as they are.
- The LLM pick prompt (`app/search.py:93`) does not use the label, and must
  not start sending client names.
- Public repo: use only the made-up demo clients.

## Open questions

1. Where should the rule live?
   - a) Copy the render rule into `search.py`: select `cl.name, cl.referenceable`
     and build the label the same way. Smallest change, but leaves two copies.
   - b) Add one helper in `app/anonymise.py`, e.g.
     `shown(name, label, referenceable, linked)`, and call it from both
     `search.py` and `render.py`. The two copies drifting apart is what caused
     this bug.
   - c) Use a SQL `case` expression in the search query. Still a second copy,
     and harder to test.
   - **Lean: b.** It is a one-line helper, and with one copy of the rule
     search and outputs cannot drift apart again.
2. Should the search card show that a name is cleared for use, e.g. a "named"
   marker next to the label? **Lean: no.** It was not asked for.
