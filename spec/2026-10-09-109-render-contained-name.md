---
status: approved
issue: 109
intent: intent/2026-10-09-109-render-contained-name.md
---

# Spec: A referenceable name that contains a protected name is half rewritten

## Design

The approved answers:
- **(a)** The whole referenceable name is replaced by that client's own label. This happens in `anonymise`, so every caller gets it, and search's client line shows that label too.
- **Yes:** referenceable aliases that contain a protected name are covered as well.

The examples use made-up clients: `Zorp`, which is protected, with the label "a retailer", and `Zorp Logistics`, which is referenceable, with the label "a logistics firm".

### 1. `anonymise.apply()` also rewrites "containing" referenceable names (`app/anonymise.py:49-58`)

Today `apply()` builds its pairs only from the names and aliases of non-referenceable clients. The change adds one more pair, `(n, own label)`, for every referenceable name or alias `n` that contains a protected name or alias. Both are matched as whole words on folded text, the same way `blocked()` matches:

```python
protected = [p for c in clients if not c["referenceable"] for p in _names(c)]
pairs = [(n, c["anonymised_label"]) for c in clients if not c["referenceable"] for n in _names(c)]
pairs += [(n, c["anonymised_label"]) for c in clients if c["referenceable"] for n in _names(c)
          if any(_pattern(fold(p)).search(fold(n)) for p in protected)]
```

The loop does not change. `_by_length` already sorts longest first, so "Zorp Logistics" (14 characters) is replaced before "Zorp" (4) gets a chance to match inside it. "Zorp Logistics won" becomes "a logistics firm won", "Zorp won" still becomes "a retailer won", and "Northwind won" is left alone, because Northwind is referenceable and contains no protected name.

The docstring changes from "names/aliases of non-referenceable clients" to say that it also covers referenceable names that contain one.

All callers route through `apply()`, so all of them get the fix with no per-caller change:
- `render.protect()` (`app/render.py:123-129`): every string of every generated section;
- research quotes (`app/render.py:57`);
- `search.clean()` (`app/search.py:81-84`): titles and outcomes;
- the pick prompt (`app/search.py:96`).

### 2. The generated client line (`app/render.py:385-386`)

For a linked, referenceable case, `shown()` still returns the name "Zorp Logistics". `protect()` then rewrites it to "a logistics firm" through step 1, so the client line agrees with the case text without any change here.

The audit flag is the one exception, and it gets a one-line change. `anonymised |= not (linked and referenceable)` would record `False` even though the client's name was replaced. The line becomes:

```python
anonymised |= not (linked and referenceable) or anonymise.apply(shown, clients) != shown
```

### 3. The search client line (`app/search.py:131`)

The check `"a client" if anonymise.blocked(c["label"], clients) else c["label"]` becomes `clean(c["label"], clients, "a client")`. That is apply, then the fail-closed check:

| Case | Shown |
|---|---|
| Linked, referenceable, containing a protected name ("Zorp Logistics") | "a logistics firm" |
| Other referenceable names | the name, unchanged |
| Non-referenceable labels | the label, unchanged (labels can't contain a protected name, `app/clients.py:27`) |
| Unlinked | "a client" |
| Anything still blocked after apply | "a client" |

The `search()` docstring note from #97 still holds: the label is unchecked until `view()`.

### Files

- `app/anonymise.py`: `apply()` and its docstring.
- `app/render.py`: line 386.
- `app/search.py`: line 131.
- Tests: `tests/test_anonymise.py`, `tests/test_render.py`, `tests/test_search.py`.

## Alternatives rejected

- **"a client" as the replacement** (intent option b). Not approved. It says less than the label, which already exists for every client (`not null`).
- **Mask the name and restore it afterwards** (option c). `blocked()` would find `Zorp` inside the restored name and withhold the whole output, unless `blocked()` learned to skip it. That weakens the fail-closed check.
- **Fix only the client line in `render.generate`**, as #97 did for search. The case text (challenge, solution, outcomes), the research quotes and the search titles would stay half rewritten. The intent says one place.
- **Have `blocked()` treat containing referenceable names as hits.** Every output that names such a client would then be withheld (409) instead of rewritten. That fails closed, but it makes the client unusable.
- **Precompute the containing pairs in the registry** (a column, or a cached list). `apply()` takes a list of only a few hundred clients. The extra check is `O(referenceable names × protected names)` regex searches per call. That's cheap at this size, so it needs no cache until a profile says otherwise.

## Risks

- **Fail closed is unchanged.** Step 1 only adds replacements, and each replacement is a label. `app/clients.py:27` refuses a label that contains another client's protected name, and `blocked()` still runs after `apply()` on every path (`protect()`, `clean()`, research). A wrong pair can therefore over-rewrite text, but it cannot let a protected name through.
- **Over-rewriting.** A referenceable client whose name contains a protected name is never shown by name, even in its own approved case. This is the approved trade-off: showing the name would reveal the protected one.
- **Case-sensitivity mismatch.** The containment check folds both names. Replacement uses `_pattern()` on NFKC text, as protected names do today: case-insensitive, except short all-caps names. If a containing name is written with accents that NFKC keeps ("Zörp Logistics"), apply misses it. "Zörp" then stays in the text, so `blocked()` finds it and withholds. That is the same behaviour, and the same fail-closed fallback, as for protected names today.
- **Short all-caps protected aliases** ("ZRP") are matched case-sensitively inside referenceable names, as `blocked()` matches them. A referenceable "Zrp Freight" therefore doesn't count as containing "ZRP". That's consistent with `blocked()`, so nothing leaks.
- **Test fallout.** The #97 test `test_referenceable_name_containing_protected_name_is_a_client` (`tests/test_search.py:236-243`) asserts "a client". It changes to the referenceable client's label, as approved answer 1 says.
- **Runtime.** No schema, data or host change. `refsdemo` gets the change on the next `demo.sh up`. `refsdev` is never touched.

## Verification

These tests use invented names only. `reg()` adds a random tag, so the protected name is `Zorp<tag>` and the referenceable one is `Zorp<tag> Logistics` (via `suffix=" Logistics"`).

1. **`tests/test_anonymise.py`, a new `test_referenceable_name_containing_protected_name`**, with registry `[C("Zorp", label="a retailer"), C("Zorp Logistics", ["ZL Freight", "Zorp Freight"], label="a logistics firm", ref=True), C("Northwind", label="a wholesaler", ref=True)]`:
   - `apply("Zorp Logistics won", reg) == "a logistics firm won"`. On main this gives "a retailer Logistics won", so the test fails there.
   - `apply("Zorp Freight shipped", reg) == "a logistics firm shipped"`: the alias case.
   - `apply("ZL Freight shipped", reg) == "ZL Freight shipped"`: an alias that doesn't contain a protected name is untouched.
   - `apply("Zorp and Northwind", reg) == "a retailer and Northwind"`.
   - `blocked(apply(...), reg) == []` for every string above.
2. **`tests/test_render.py`, a new test.** A case linked to the referenceable `Zorp<tag> Logistics`, with a challenge that names it, is generated as md. Checks:
   - the client line and the challenge both show "a logistics firm";
   - the text contains neither `Zorp<tag>` nor "a retailer Logistics";
   - the `generations` row has `anonymised = True`.

   On main, the client line is "a retailer Logistics" and the row says `False`.
3. **`tests/test_search.py`**: the #97 test 5c now expects the referenceable client's label ("a retailer UK" in its setup). A title naming the client comes out as that label, not half rewritten. Both fail on main.
4. **Full suite:** `docker compose build app && docker compose run --rm app timeout 900 pytest` passes, at main's count plus the new tests. Existing anonymisation tests, such as `test_referenceable_untouched_and_blocked_scrub` and `test_protected_name_replaced_or_withheld`, pass unchanged.
