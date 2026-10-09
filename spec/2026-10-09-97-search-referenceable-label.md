---
status: draft
issue: 97
intent: intent/2026-10-09-97-search-referenceable-label.md
---

# Spec: Referenceable client shown anonymised in search results

## Design

Write the display rule once, in `app/anonymise.py`, and have search and render
both call it (intent question 1, answer b). Cards get no "named" marker
(question 2, answer no).

### The helper

New function in `app/anonymise.py`, placed after `resolve()`:

```python
def shown(name: str | None, label: str | None, referenceable: bool | None, linked: bool) -> str:
    """How a case's client is shown: its name only if linked and referenceable, its label if linked, else "a client"."""
    return name if linked and referenceable else (label if linked else "a client")
```

This is the rule from `app/render.py:385`, moved without changes. `linked` is
passed explicitly (`cl.id is not null`) and not inferred from `name`/`label`
being null, so a left join that finds no client always ends in "a client".

### Call sites

1. `app/render.py:385` (generate, all formats):
   `shown = anonymise.shown(name, label, referenceable, linked)`. The query at
   `:374`, the `anonymised` flag at `:386` and the `protect()` pass at
   `:388-391` stay as they are. The name still goes through `protect()` as
   `section.client`.
2. `app/search.py:71` (the `search()` query): select
   `cl.name, cl.anonymised_label, cl.referenceable, cl.id is not null` and not
   just `cl.anonymised_label`. This uses the same columns as `render.py:374`.
3. `app/search.py:74-75`: `label=anonymise.shown(name, label, ref, linked)`,
   and unpack the extra columns in the comprehension. The `label` key and
   everything that reads it stay the same.
4. `app/search.py:131` (`results().view`): `label=clean(c["label"], clients, "a client")`.
   Search has no `protect()`. This gives the shown client the same apply and
   blocked check that the title and summary already get. So a referenceable
   name that contains a protected client's name falls back to "a client"
   instead of being shown.

`app/templates/search.html:19,37` already render `label`, so they stay as
they are.

### Other places checked, no change

- `app/search.py:93` (`pick()` LLM listing): built from `facts()` and
  `summary`, never `label` or `client_mention`, so no client name is sent to
  the model. It stays that way.
- `app/research.py:123-145` (`seed_question` / research from-case): it sends
  only capabilities, technology and engagement type, and shows no client.
- `app/review.py` and `review_detail.html`: the reviewer page shows the
  extracted `client_mention` that is being reviewed, not a display label.
  `review.py:237` only sets `client_id`.
- `app/clients.py` and `clients.html`: the registry admin page shows name and
  label on purpose.
- `app/render.py:81-97` (`section`) and its templates (`:195`, `:286`): they
  receive the value that was already chosen at call site 1.

## Alternatives rejected

- **Copy the rule into `search.py`** (intent 1a). Two copies of the rule are
  the cause of this bug.
- **SQL `case` expression in the search query** (1c). It is still a second
  copy, in a second language, and harder to unit test.
- **Infer `linked` from `label is not None`**. It saves one argument, but it
  depends on `anonymised_label` being `not null`. If that assumption ever
  breaks, a linked client would silently be treated as unlinked. The explicit
  flag matches render.
- **"Named" marker on search cards** (question 2). Nobody asked for it.

## Risks

- **Widening naming.** This is the main risk. The only new way a name can
  appear is `linked and referenceable` in `shown()`, which is the same
  condition render already uses. A non-referenceable linked client still gets
  its label, and an unlinked case still gets "a client". The tests below pin
  all three outcomes in both paths.
- **Protected name inside a referenceable name** (for example, referenceable
  "Acme Bank UK" next to protected "Acme Bank"). In generate, `protect()` still
  runs on `section.client` and withholds the output. In search, call site 4's
  `clean()` turns the label into "a client". Neither path shows the name.
- **LLM exposure.** None. `pick()` does not read `label` (see above).
- **Audit flag.** `generations.anonymised` (`render.py:386`) does not change.
- No schema, data or seed change. It is the same on every host. Nothing
  touches `refsdemo` or `refsdev`.

## Verification

Use only made-up client names (for example "Globex", "Initech" or "Zorp" with
the fixture's random tag). Never use a real organisation.

- `tests/test_anonymise.py`: a unit test of `shown()`:
  - `("Globex", "a manufacturer", True, True)` gives `"Globex"`.
  - `(…, False, True)` gives `"a manufacturer"`.
  - `(None, None, None, False)` gives `"a client"`.
  - `("Globex", "a manufacturer", True, False)` gives `"a client"`. An
    unlinked case is never named, whatever the other arguments are.
- `tests/test_search.py`, new `test_client_display_rules`: three approved
  cases, linked to a referenceable client, linked to a non-referenceable
  client, and unlinked (registry rows and links as in `tests/test_render.py:18-37`).
  - `sr.search(...)` labels are the name, the label and `"a client"`.
  - `sr.results(...)` (LLM stubbed with the existing `picks_reply` helper)
    shows the same three on top picks and other matches.
  - A non-referenceable client's name appears in no result.
- `tests/test_search.py`: a referenceable client whose name contains a
  protected client's name is shown as "a client" by `results()`.
- `tests/test_render.py::test_client_display_rules` (existing, `:105-117`)
  already covers generate: name, label and "a client", plus the `anonymised`
  audit flag. It must pass unchanged. The other render tests must also pass
  unchanged.
- Full suite: `pytest` is green.
