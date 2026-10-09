---
status: approved
issue: 97
spec: spec/2026-10-09-97-search-referenceable-label.md
---

# Plan: Referenceable client shown anonymised in search results

Search always shows a case's client as its anonymised label
(`app/search.py:71-75`). Generate (`app/render.py:385`) shows the client's
name when the client is linked and referenceable. The fix writes that rule
once and has both paths call it.

Approved decisions, carried over from the spec:

- **The helper:** a new `anonymise.shown(name, label, referenceable, linked)`
  goes in `app/anonymise.py`, right after `resolve()`. It is the
  `render.py:385` rule, moved unchanged.
- **`linked`:** it is passed explicitly (`cl.id is not null`), never inferred
  from `label`.
- **Search query:** it selects the same four client columns that
  `render.py:374` does.
- **Shown label in search:** `results().view` runs the fail-closed check on
  it.
- **No "named" marker** on search cards.
- **Left as they are:** templates, `pick()`, research, review and the
  clients page. No schema, data or seed change.
- **Test data:** made-up client names only (Globex, Initech, Zorp plus the
  random tag).

## Deviation from the spec (decided while planning)

**What the spec says:** call site 4 wraps the label in
`clean(c["label"], clients, "a client")`.

**Why that's wrong:**
- `clean()` runs `apply()` first, and that rewrites a protected name inside a
  referenceable name instead of rejecting it. For example, "Zorp UK" (when
  "Zorp" is protected) becomes "a retailer UK". `blocked()` then finds
  nothing, so the result is never "a client".
- That fails the spec's own verification ("shown as 'a client'") and leaves
  a half-rewritten name on the card.

**The fix:** check without rewriting.

```python
label="a client" if anonymise.blocked(c["label"], clients) else c["label"]
```

It still fails closed, and it gives the outcome the spec verifies.

**Also noted:**
- The spec says `protect()` in generate "withholds" such a name. It doesn't:
  `protect()` also applies first, so generate prints "a retailer UK".
- No protected name leaks, so this is out of scope for #97. It's worth a
  follow-up issue.

## Steps

1. **`app/anonymise.py`, after `resolve()` (ends at line 86): add `shown()`.**

   ```python
   def shown(name: str | None, label: str | None, referenceable: bool | None, linked: bool) -> str:
       """How a case's client is shown: its name only if linked and referenceable, its label if linked, else "a client"."""
       return name if linked and referenceable else (label if linked else "a client")
   ```

   - **Verify:** step 4's unit test.
   - **Traps:** none.

2. **`app/render.py:385`: replace the inline expression.**
   - Change it to `shown = anonymise.shown(name, label, referenceable, linked)`.
   - Leave lines 374, 386 (the `anonymised` flag) and 388-391 (`protect()`)
     as they are.
   - **Verify:** `pytest tests/test_render.py` stays green, unchanged.
   - **Traps:** the local variable is called `shown`. Keep it, because
     `anonymise.shown` is accessed through the module and the names don't
     clash. Don't write `from app.anonymise import shown`.

3. **`app/search.py:71-75` and `:131`.**
   - **`:71`:** change `cl.anonymised_label` to
     `cl.name, cl.anonymised_label, cl.referenceable, cl.id is not null`.
   - **`:74-75`:**

     ```python
     return [dict(id=i, case=ReferenceCase.model_validate(d), label=anonymise.shown(name, label, ref, linked), basis=b, rank=r)
             for i, d, name, label, ref, linked, b, r in rows]
     ```

   - **`:131`, in `view()`:** change `label=c["label"]` to
     `label="a client" if anonymise.blocked(c["label"], clients) else c["label"]`.
     This is the deviation above.
   - **Verify:** step 5's tests, then `pytest tests/test_search.py`.
   - **Traps:**
     - the select order must match the unpack order;
     - `order by rank` uses the alias, so the column count doesn't matter
       there;
     - `pick()` (`:84-114`) must not start reading `label`, because it is
       the LLM path.

4. **`tests/test_anonymise.py`: add `test_shown`** after
   `test_referenceable_untouched_and_blocked_scrub`. It has four asserts:
   - `("Globex","a manufacturer",True,True)` gives `"Globex"`;
   - `(…,False,True)` gives `"a manufacturer"`;
   - `(None,None,None,False)` gives `"a client"`;
   - `("Globex","a manufacturer",True,False)` gives `"a client"`.

   - **Verify:** `pytest tests/test_anonymise.py -k shown`.
   - **Traps:** none.

5. **Search tests.**
   - **5a. Move fixtures:** move the `reg` fixture and `link()` from
     `tests/test_render.py:17-37` into `tests/test_search.py`, after
     `approved`. In `tests/test_render.py:13`, extend the import to
     `from tests.test_search import approved, data, link, reg  # noqa: F401`.
   - **5b. `test_client_display_rules`** (append to `tests/test_search.py`).
     - **Setup:**
       - `reg("Globex","a manufacturer",True)`, `reg("Initech","a software firm")`;
       - three cases from `approved()`, linking a to ref and b to anon, with
         c unlinked.
     - **Assert:**
       - `{c["id"]: c["label"] for c in sr.search(ME,"onboarding",{})}`
         gives the name, `"a software firm"` and `"a client"` for a, b and c.
       - Stub with `picks_reply(monkeypatch, sr.Pick(case_id=a, …))`, then
         run `sr.results(ME,"onboarding",{})`. The labels on top and others
         must give the same three for a, b and c.
       - The Initech name (with its tag) appears in no label.
   - **5c. `test_referenceable_name_containing_protected_name_is_a_client`:**
     - **Setup:** `p,_ = reg("Zorp","a retailer")` and
       `reg(f"{p} UK" …)`.
     - **Trap:** `reg` appends the tag to the name. Insert the referenceable
       row so its name is `"Zorp<tag> UK"`. Either give `reg` a
       `tagged=True` keyword, or insert that one row directly in the test and
       clean it up through `reg`'s `made` list. Pick the smaller one: an
       optional `suffix=""` argument on `reg`, appended after the tag.
     - **Assert:** link a case to the referenceable client;
       `sr.results(...)` shows `"a client"` for it.
   - **Verify:** `pytest tests/test_search.py tests/test_render.py tests/test_industry.py`.
   - **Traps:**
     - **Circular import:** `test_render` imports from `test_search`, so
       `test_search` must never import from `test_render`. That's why the
       fixtures move.
     - **Different fixture:** `tests/test_anonymise.py` has its own,
       different `reg` fixture. Leave it alone.
     - **Teardown:** `reg` clears `client_id` before deleting clients, and
       that must stay.
     - **Search results include other cases:** the persistent test database
       may hold approved cases from other tests, so assert on your own ids.
       Don't assert on result counts.

## Tests

- `docker compose build app && docker compose run --rm app timeout 900 pytest`
  must be all green. The count should be the main-branch count plus 3.
- `tests/test_render.py::test_client_display_rules` passes unchanged.

## Rollback

Revert the commits. It touches no schema, data or config.

## Deviations during implementation

- Step 5: `reg` appends a random tag to every name, so the 5c setup is
  `reg("Zorp", "a retailer")` plus `reg("Zorp", "a retailer UK", True, suffix=" UK")`,
  giving `Zorp<tag>` and `Zorp<tag> UK`. 5b compares labels with the tagged name `reg` returns.
