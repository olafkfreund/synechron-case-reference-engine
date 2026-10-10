---
status: approved
issue: 119
intent: intent/2026-10-10-119-short-alias-case.md
---

# Spec: Short all-caps alias: apply() and blocked() disagree on case

## Design

The approved answer is A: `apply()` matches short all-caps aliases regardless
of case, the same way `blocked()` does. There is no registry warning.

The fix deletes the special case. `_pattern` (`app/anonymise.py:35-37`)
always compiles with `re.I`:

```python
    return re.compile(rf"(?<!\w){body}(?!\w)", re.I)
```

This one deletion brings `apply`, `blocked`, `has_name` and `scrub` into
agreement, because all four build through `_pattern`:

- `blocked()` and `has_name()` already matched these aliases regardless of
  case, through `fold()`.
- `apply()` and `scrub()` now do too.

`test_short_caps_alias_is_case_sensitive` (`tests/test_anonymise.py:132-134`)
pins the old behaviour. It becomes
`test_short_caps_alias_matches_any_case`: `apply("ACB and acb")` gives
"a bank and a bank", and `blocked()` of that output is `[]`.

## Alternatives rejected

- **B: make `blocked()` case-sensitive for short aliases.** "Acb" and
  "acb@…" would then reach outputs. That breaks fail-closed, and answer 1
  rejected it.
- **C: keep both functions and name the alias in the 409.** The refusals
  would continue, and answer 1 rejected it.
- **Match case-insensitively only inside emails and URLs.** That is a second
  rule that `blocked()` would also have to copy. It is more code, and the
  rule would still disagree about prose such as "the Acb team".

## Risks

- **Ordinary words get rewritten.** A protected alias that is also a word, for
  example "ZEST", now rewrites "zest" to the label in every output. That was
  accepted in answer 1, and the admin controls it by choosing aliases. Today
  those outputs are refused (409), so the change goes from refused to
  produced.
- **`scrub()` removes the word from outgoing research queries.** It removes
  text and never adds it, so nothing leaks. A query that becomes empty is
  already refused at `research.py:85`.
- **#118 edits `_pattern` two lines above.** The rebase conflict is trivial.
- **No host impact.**

## Verification

- `test_short_caps_alias_matches_any_case`, as above. It fails on main.
- A render test: a protected client with alias "UBZ" and case text
  "Contact ops@ubz.example and the Ubz team". `render.protect` returns text
  with no "ubz" in any case, and there is no 409. It fails on main.
- The full suite passes. Any test that relied on the case-sensitive rule
  shows up here, and is reported.
