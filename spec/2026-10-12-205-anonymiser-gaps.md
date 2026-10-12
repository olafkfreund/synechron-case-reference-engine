---
status: draft
issue: 205
intent: intent/2026-10-12-205-anonymiser-gaps.md
---

# Spec: Two spellings get a protected client name past the anonymiser

## Correction to the intent decision (needs your confirmation)

The intent asked whether a space before the dot should be allowed "only where
the name itself has a dot after that word". You approved that, but I framed the
question wrongly. #188 already makes a dot optional after **any** word of a
name, and the registry name `St James's Bank` (no dot) matches
`St. James's Bank` (`tests/test_anonymise.py:161`). Under the approved rule,
that registry name would match `St. James's Bank` but still leak
`St . James's Bank`.

**Recommendation:** allow spaces before the optional dot wherever #188 already
allows the dot, that is, between any two words. This adds no new match beyond
what #188 accepted. It only lets spaces sit before the dot as well as after it.
Approving this spec approves the correction.

## Design

Both fixes are in `app/anonymise.py`.

**1. Space before the dot: `_pattern()` (line 63).** The joiner goes from
`\.?` + `_JOIN` (optional dot, then separators) to
`_JOIN + r"(?:\." + _JOIN + ")?"` (separators, then optionally a dot and more
separators). Written this way the regex is unambiguous: two adjacent `*` runs
would backtrack on long whitespace. `apply()`, `blocked()`, `has_name()` and
`scrub()` all use `_pattern()`, so one change fixes all four. The last word's
dot is unchanged ("Zorp Co. They" keeps its full stop). Names without a word
character still use the plain `_JOIN`.

**2. Enclosing mark inside a word: `scrub()` (lines 127-135).** `scrub()` reads
an enclosing mark only as a space. It will read the mark both ways, in two
passes:

- **Pass 1, the mark as nothing:**
  `_replace_once(_no_format(unicodedata.normalize("NFC", text)), [(n, " ") for every name])`.
  This uses the per-character mapping `apply()` already uses, so it removes
  `Z⃝orpale Bank` from the original text and leaves the other marks in place.
- **Pass 2, the mark as a space:** the existing `fold(t, " ")` and `.sub()` loop
  on pass 1's result. This catches `A⃝Zorpale Bank` and `Zorpale Bank⃝x`, as
  it does today.

`scrub()` still returns folded text. Its docstring and the `fold()` docstring
(line 42) drop the "missed" note.

## Alternatives rejected

- **Allow the space only where the registry name has a dot (the approved
  intent wording):** it leaks `St . James's Bank` for a registry name without a
  dot, which #188 already treats as matching. See the correction above.
- **Run `scrub()` on both readings and keep the shorter output:** a guess.
  Text with marks in both positions defeats it.
- **Remove every enclosing mark before matching:** this joins
  `A⃝Zorpale Bank` into `azorpale bank`, which leaks that spelling instead.
- **Make `scrub()` call `apply()`:** `apply()` replaces only protected names
  and inserts labels. `scrub()` removes every name, including referenceable
  ones.

## Risks

- **Wider matching:** "Zorp . Co" now hides "Zorp Co". The #188 decision already
  hides "Zorp. Co" as a sentence break, so this is the same accepted trade-off.
- **Pass 1 merges text:** replacing a name with a space can join its neighbours
  into another name. Pass 2 runs afterwards and catches that. A name re-formed
  by pass 2 itself is the existing single-pass behaviour, which is unchanged.
- **Performance:** one more linear pass per research query. Queries are capped
  at 200 characters (`MAX_QUERY`).

## Verification

New tests in `tests/test_anonymise.py`, all with made-up names:

- For `St . James's Bank`, `ST . JAMES'S BANK` and `St .James's Bank` with the
  registry name `St James's Bank`, and `St . James's Trust` with the alias
  `St. James's Trust`:
  - `apply()` replaces the name;
  - `blocked()` finds it, and `blocked(apply(t))` returns `[]`;
  - the name is not in `scrub()`.
- `"james" not in scrub("Z⃝orpale Bank, St . James's Trust")`, and
  `"zorpale" not in scrub("Z⃝orpale Bank results")`, which keeps "results".
- The existing #163/#188 guards still pass: `U.S.` doesn't match `us`, and
  "Zorp Co. They" keeps its full stop.
- Before the fix, on main, the new tests fail. Afterwards the full suite passes.
