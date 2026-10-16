---
status: approved
issue: 119
spec: spec/2026-10-10-119-short-alias-case.md
---

# Plan: Short all-caps alias: apply() and blocked() disagree on case

These decisions are copied from the spec:

- `_pattern` always compiles with `re.I`, and the short all-caps exception is
  deleted.
- So `apply`, `blocked`, `has_name` and `scrub` all match aliases regardless of
  case.
- The accepted cost: a word-like alias also rewrites that word.
- No registry warning.

## Steps

1. `app/anonymise.py:35-37`: delete the comment and the `flags = ...` line, and
   make the return `return re.compile(rf"(?<!\w){body}(?!\w)", re.I)`.

   Verify by `grep -n "isupper" app/anonymise.py`, which finds nothing.

   Traps:
   - The comment at `apply()`, "folded text is lowercase, so re.I matches
     blocked() exactly", stays true. Leave it.
   - If #118 merged first, the `_pattern` body lines differ. Keep its `words`
     and `body` lines.

2. `tests/test_anonymise.py:132-134`: replace the test with
   `test_short_caps_alias_matches_any_case`, which checks that:
   - `an.apply("ACB and acb", reg) == "a bank and a bank"`;
   - `an.blocked(that, reg) == []`.

3. A render test, in the file that tests `render.protect`. `grep -ln "protect(" tests/`
   finds it. Name it `test_lowercase_short_alias_is_rewritten_not_withheld`. It
   uses a client dict
   `{"id": 1, "name": "Union Bank of Zeta", "aliases": ["UBZ"], "anonymised_label": "a Swiss bank", "referenceable": False}`.
   It checks that `render.protect([{"text": "Contact ops@ubz.example and the Ubz team"}], cl)`
   returns text where `"ubz" not in out[0]["text"].lower()`, with no exception.

   Verify that the tests from steps 2 and 3 fail on main.

## Tests

The full suite must pass. Any other test that relied on case-sensitive short
aliases will show up here. Fix such a test only if it pinned the old rule, and
record it as a deviation in this plan.

## Rollback

Revert the commit.

## Deviations

- **Step 3:** the test passes `[cl]`. `render.protect` takes a list of
  clients, and the plan's `cl` was a typo.
- **Review fix (should-fix):** the help text in `app/templates/clients.html:8`
  described the removed case-sensitive rule. It now says that aliases match in
  any case, and that this costs a word-like alias.
