---
status: draft
issue: 205
spec: spec/2026-10-12-205-anonymiser-gaps.md
---

# Plan: Two spellings get a protected client name past the anonymiser

Approved decisions, carried over from the spec:

- **Space before the dot.** Between any two words of a name, spaces may sit
  before the optional dot as well as after it. #188 already makes the dot
  optional after any word, so `St . James's Bank` now matches like
  `St. James's Bank` and adds no new words to the match. Inner dots stay
  strict, so `U.S.` never matches `us` (#163). The last word's dot stays text.
- **Enclosing marks in `scrub()`.** `scrub()` reads an enclosing mark both ways:
  - pass 1 reads the mark as nothing. It runs `_replace_once` on the original
    text, with every name replaced by `" "`;
  - pass 2 reads the mark as a space. It is the existing `fold(t, " ")` and
    `.sub()` loop.
  `scrub()` still returns folded text.
- Fail closed: `blocked()` finds at least what `apply()` replaces. No new
  dependency. Tests use made-up names only.

There are 2 implementation steps on 2 files, below the coder threshold, so I
implement this myself.

## Steps

1. `app/anonymise.py`:
   - **Line 63:** `join = r"\.?" + _JOIN` becomes
     `join = _JOIN + r"(?:\." + _JOIN + ")?"`. Keep the `else _JOIN` branch for
     names without a word character, and update the comment to "spaces may sit
     around the dot (#205)".
   - **Lines 127-135, `scrub()`:** before the `fold` line, add
     `text = _replace_once(_no_format(unicodedata.normalize("NFC", text)), [(n, " ") for c in clients for n in _names(c)])`,
     with the comment "pass 1: a mark read as nothing, Z⃝orpale (#205)". Then
     the existing `t = fold(text, " ")`, with its comment changed to "pass 2: a
     mark read as a space, A⃝Zorpale".
   - **Docstrings:** the `fold()` docstring (line 42) keeps the
     `blocked()`/`has_name()`/`scrub()` sentence. `scrub()`'s docstring gets one
     line: both readings of an enclosing mark.

   Verify with the probe from the issue: no name in any `scrub()` output, and
   `blocked()` non-empty for `St . James's Trust`.

   Traps:
   - `_replace_once` takes `(name, label)` pairs and sorts them by length itself.
   - Two adjacent `*` runs (`_JOIN + r"\.?" + _JOIN`) would backtrack, so use
     the grouped form.
   - Don't apply NFKC to the text passed to `_replace_once` (#165).

2. `tests/test_anonymise.py`:
   - **After line 171 (the end of `test_dot_after_a_word_in_text`):** add
     `test_space_before_a_dot_in_text`, parametrised over `St . James's Bank`,
     `ST . JAMES'S BANK` and `St .James's Bank` with `C("St James's Bank", label="a bank")`,
     plus `St . James's Trust` with `C("Zorpale Bank", ["St. James's Trust"], label="a bank")`.
     For each:
     - `apply(t)` contains no "james";
     - `blocked(t)` is non-empty;
     - `blocked(apply(t)) == []`;
     - "james" is not in `scrub(t)`.
   - **After `test_a_sign_or_mark_between_the_name_and_another_word`:** add
     `test_scrub_reads_a_mark_inside_a_word`:
     - `scrub("Z⃝orpale Bank results", reg) == "results"`;
     - "zorpale" and "james" are not in
       `scrub("Z⃝orpale Bank, St . James's Trust", reg)`.

   Verify: the new tests fail on main's `app/` and pass on the branch
   (`check.sh`).

   Traps: made-up names only. Never run a test while another run uses the same
   compose project.

## Tests

- `check.sh ../refs-engine-205 "tests/test_anonymise.py"`: the new tests fail on
  main's `app/`, then the full suite passes. On main today that is 766, plus
  the new tests.
- The existing #163, #187 and #188 tests in `tests/test_anonymise.py` pass
  unchanged.

## Rollback

Revert the merge commit. There is no schema or data change.
