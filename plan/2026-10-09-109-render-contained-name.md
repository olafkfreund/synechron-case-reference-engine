---
status: approved
issue: 109
spec: spec/2026-10-09-109-render-contained-name.md
---

# Plan: A referenceable name that contains a protected name is half rewritten

## Decisions from the approved spec

All examples use made-up clients only (the repo is public):
- `Zorp`: protected, label "a retailer";
- `Zorp Logistics`: referenceable, label "a logistics firm";
- `Northwind`: referenceable, contains no protected name.

**The fix lives in one place, `anonymise.apply()`.**
- **What it adds:** besides the protected names and aliases, `apply()` also replaces every referenceable name or alias that contains a protected name or alias, with that client's own label.
- **How containment is tested:** whole words on folded text, the same way `blocked()` matches. `_pattern(fold(p)).search(fold(n))`.
- **Order:** `_by_length` already sorts longest first. "Zorp Logistics" is therefore replaced before "Zorp" can match inside it.
- **What stays the same:** a referenceable name that contains no protected name ("Northwind") is untouched, and so is an alias that contains none ("ZL Freight").

**Every caller gets the fix with no change of its own:**
- `render.protect()`;
- research quotes;
- `search.clean()`, for titles and outcomes;
- the pick prompt.

**Two call sites change:**
- **`app/render.py:386`, the audit flag.** A generation where the shown name was rewritten is recorded as anonymised:
  `anonymised |= not (linked and referenceable) or anonymise.apply(shown, clients) != shown`.
  The generated client line itself needs no change: `protect()` rewrites it through `apply()`.
- **`app/search.py:131`, the search client line.** The check `"a client" if anonymise.blocked(c["label"], clients) else c["label"]` becomes `clean(c["label"], clients, "a client")`.
  - A containing referenceable name shows its label ("a logistics firm").
  - Anything still blocked after `apply()` shows "a client".
  - Other names and labels are unchanged.

**What doesn't change:**
- **Fail closed.**
  - The change only adds replacements, and each one is a label.
  - `app/clients.py:27` refuses a label that contains a protected name.
  - `blocked()` still runs after `apply()` on every path.
- **Rejected options.** No masking and restoring, no "a client" as the replacement, no client-line-only fix, no change to `blocked()`, and no cache. The extra check costs O(referenceable names × protected names) regex searches per call, for a few hundred clients.

**The accepted trade-off:** a referenceable client whose name contains a protected name is never shown by name, even in its own case.

## Steps

1. **`app/anonymise.py:49-58`, `apply()`.** Replace the `pairs = ...` line with:
   ```python
   protected = [p for c in clients if not c["referenceable"] for p in _names(c)]
   pairs = [(n, c["anonymised_label"]) for c in clients if not c["referenceable"] for n in _names(c)]
   pairs += [(n, c["anonymised_label"]) for c in clients if c["referenceable"] for n in _names(c)
             if any(_pattern(fold(p)).search(fold(n)) for p in protected)]
   ```
   - Keep the loop as it is.
   - Change the docstring's first line to: "Replace names/aliases of non-referenceable clients, and referenceable names/aliases that contain one, with the client's anonymised label."

   → Verify: `docker compose build app && docker compose run --rm app pytest -q tests/test_anonymise.py`. The existing tests pass unchanged. `test_referenceable_untouched_and_blocked_scrub` still gives "Globex won", because Globex contains no protected name.

   Traps:
   - Don't touch `blocked()`, `scrub()` or `_pattern()`.
   - `_pattern()` keeps short all-caps names case-sensitive. Use `_pattern(fold(p))` exactly as `blocked()` does, so the containment check and `blocked()` agree.
   - There's no bind mount, so rebuild before every run.

2. **`tests/test_anonymise.py`.** After `test_referenceable_untouched_and_blocked_scrub` (ends at about line 29), add `test_referenceable_name_containing_protected_name`, using the file's `C()` helper:
   ```python
   reg = [C("Zorp", label="a retailer"),
          C("Zorp Logistics", ["ZL Freight", "Zorp Freight"], label="a logistics firm", ref=True),
          C("Northwind", label="a wholesaler", ref=True)]
   ```
   - Assert:
     - `an.apply("Zorp Logistics won", reg) == "a logistics firm won"`
     - `an.apply("Zorp Freight shipped", reg) == "a logistics firm shipped"`
     - `an.apply("ZL Freight shipped", reg) == "ZL Freight shipped"`
     - `an.apply("Zorp and Northwind", reg) == "a retailer and Northwind"`
   - Then, for each of those four inputs, assert `an.blocked(an.apply(s, reg), reg) == []`.

   → Verify: the same command as step 1. The new test passes with step 1. It fails on main, where the first assert gives "a retailer Logistics won".

   Traps: only made-up names. Don't put "a retailer" in the referenceable label, or the test can't tell the fix apart from the half rewrite.

3. **`app/render.py:386`.** Replace `anonymised |= not (linked and referenceable)` with
   `anonymised |= not (linked and referenceable) or anonymise.apply(shown, clients) != shown`.

   → Verify: `pytest -q tests/test_render.py`. The existing `test_client_display_rules` still records `False` for `gen([a])`: Globex<tag> contains no protected name.

   Traps:
   - `clients` is already loaded at line 381, before the loop. Don't load it again.
   - `apply()` normalises to NFKC, so a name that changes under NFKC also sets the flag. That errs towards `True`, which is fine.

4. **`tests/test_render.py`.** After `test_protected_name_replaced_or_withheld` (about line 97), add `test_referenceable_name_containing_protected_name_uses_its_label(approved, reg)`. `reg` and `link` are imported from `tests.test_search`.
   - Setup:
     ```python
     p, _ = reg("Zorp", "a retailer")
     n, ref = reg("Zorp", "a logistics firm", True, suffix=" Logistics")
     cid = approved(full().model_copy(update={"challenge": Sourced[str](value=f"Built for {n} in 2024", source_quote="q")}))
     link(cid, ref)
     t = gen([cid]).text
     ```
   - Assert:
     - `"*a logistics firm*" in t`
     - `"Built for a logistics firm in 2024" in t`
     - `p not in t`
     - `"a retailer Logistics" not in t`
   - Then read the latest `generations` row (as at line 90) and assert `anonymised is True`.

   → Verify: `pytest -q tests/test_render.py`. The test passes with steps 1 and 3. On main, the client line is "a retailer Logistics" and the flag is `False`.

   Traps:
   - `reg` appends a random tag after the name and before the suffix, so `p` is `Zorp<tag>` and `n` is `Zorp<tag> Logistics`.
   - `p` is a substring of `n`, so `p not in t` is also what proves `n` is gone.

5. **`app/search.py:131`, in `view()`.** Replace `label="a client" if anonymise.blocked(c["label"], clients) else c["label"]` with `label=clean(c["label"], clients, "a client")`.

   → Verify: `pytest -q tests/test_search.py`. All tests pass except the old 5c expectation, which step 6 changes.

   Traps:
   - Keep the #97 docstring note on `search()`: the label is unchecked until `view()`.
   - `clean()` is defined in the same module (line 81). No import is needed.

6. **`tests/test_search.py:236-243`.** Rename `test_referenceable_name_containing_protected_name_is_a_client` to `..._shows_its_label`.
   - Setup:
     ```python
     p, _ = reg("Zorp", "a retailer")
     n, ref = reg("Zorp", "a logistics firm", True, suffix=" Logistics")
     a = approved(data(title=f"Faster onboarding for {n}"))
     link(a, ref)
     ```
     Keep the `picks_reply` / `sr.results` lines as they are.
   - Assert, for the row `x` with id `a` in `top + others`:
     - `x["label"] == "a logistics firm"`
     - `x["title"] == "Faster onboarding for a logistics firm"`
     - `p not in x["title"] + x["label"]`

   → Verify: `pytest -q tests/test_search.py`. It passes with steps 1 and 5. On main the label is "a client" and the title is "Faster onboarding for a retailer Logistics".

   Traps:
   - This is the #97 test whose expectation changes, as approved answer 1 says.
   - Don't use "a retailer UK" as the label, for the reason given in step 2.
   - `data(title=...)` exists in this file, and the title must still match the bid "onboarding".

7. **Full suite.** Run `docker compose build app && docker compose run --rm app timeout 900 pytest`. All tests pass, at main's count (552) plus 2: the step 2 and step 4 tests. The step 6 test is renamed, not added.

   Traps: use only the worktree's own compose project. Never `up` or `down`, and never touch `refsdemo` or `refsdev`.

## Tests

```
docker compose build app && docker compose run --rm app timeout 900 pytest -q tests/test_anonymise.py tests/test_render.py tests/test_search.py
docker compose build app && docker compose run --rm app timeout 900 pytest
```

Expected:
- **The three files:** all pass.
- **Full suite:** 554 passed (552 + 2), and the existing anonymisation tests pass unchanged. `test_protected_name_replaced_or_withheld` still gets a 409 for the accented evasion.

## Rollback

Revert the merge commit. There's no schema, data or config change. `refsdemo` gets the old behaviour back on its next `demo.sh up`.

## Deviations during implementation (review of the finished diff)

- Step 1: the containment check ran one regex search per (referenceable, protected) name pair on
  every `apply()` call: about 1.2 s per call at 200 + 200 clients, and search and generate call
  `apply()` dozens of times. It now builds one alternation of all protected names per call
  (folded text is lowercase, so `re.I` matches `blocked()` exactly), about 30 ms.
- Step 1: `.sub()` takes a function returning the label, so a `\` in a label is literal text.
  A template would crash every search and render on a label like `a \d firm`, and with
  referenceable labels now going through `.sub()`, that crash would hit more admin entries.
- Tests: `test_label_backslash_is_text_and_many_clients_stay_fast` in `tests/test_anonymise.py`.
