---
status: draft
issue: 166
intent: intent/2026-10-10-166-accentless-names.md
---

# Spec: apply() replaces whatever blocked() finds

Approved decision: **B**. Match on the folded text and map each hit back to
the original characters.

## Design

`app/anonymise.py`, `apply()`:

1. `text = _no_format(text)` (from #165: Cf characters only, no NFKC).
2. Build the folded text and an index map in one pass:
   `for i, ch in enumerate(text): f = fold(ch)`, append `f`, and record `i`
   once per character of `f`. Characters that fold to nothing (combining marks)
   get no entry.
3. The name list is unchanged: protected names and aliases, plus referenceable
   names that contain one (the current `contains` rule), longest first
   (`_by_length`).
4. For each name, `_pattern(fold(name)).finditer(folded)`, the same pattern
   `blocked()` uses. Map each hit to original indexes:
   start = `at[m.start()]`, end = `at[m.end() - 1] + 1`, then extend the end
   over following characters whose fold is empty, so a trailing combining
   accent goes with the name. Keep a hit only if it overlaps no hit already
   kept (longer names come first).
5. Replace the kept spans in the original text right to left with the
   label, via slicing, so a `\` in a label is text.

Because the match runs on exactly the folded text `blocked()` uses, with the
same patterns, every protected name `blocked()` would find in the input is
replaced. Text outside the spans is copied as written, so case, accents,
superscripts and fractions survive. This does not reintroduce #165's NFKC bug:
NFKC only ever reaches the folded copy used for matching.

**Merge order:** last of the three `anonymise.py` changes, after #165 (it uses
`_no_format`) and #163 (its `_pattern` rules come along unchanged, so
apostrophe and dot variants are replaced too).

## Alternatives rejected

- **A, also try each name's folded form on the raw text:** misses spellings
  that only fold equal ("Sociéte", decomposed accents, "STRASSE" for "Straße"),
  so some downloads would still be refused.
- **Replace on the folded text and return it:** loses the text's own case and
  accents everywhere, and brings NFKC back into the output.

## Risks

- **Per-character folding must equal whole-text folding.** `fold` is NFKC,
  casefold, NFKD, then drops combining marks; NFKD(NFKC(x)) equals NFKD(x), and
  casefold is per character, so they agree. A random 5,000-character sample and
  hand-picked cases (`e` + combining acute, `ﬁ`, `ß`, `İ`, `ǅ`, `½`, `Å`)
  agree. If some sequence ever did not, `blocked()` still runs after `apply()`
  in every caller and refuses the output (fails closed).
- Cost: one `fold()` call per character. Case texts are small; render and
  search already fold whole texts.
- Width variants ("Ｚｏｒｐ Bank") are replaced again, closing the gap #165
  accepts.

## Verification

New tests in `tests/test_anonymise.py`, failing on main:

- `test_apply_replaces_folded_spellings`: with
  `[C("Société Zorpale", label="a bank"), C("Straße AG", label="a firm")]`,
  `"Societe Zorpale"`, `"SOCIETE ZORPALE"`, `"Sociéte Zorpale"` and
  `"STRASSE AG"` are each replaced by the label, and the rest of
  `"… 10⁶ m² ½"` is unchanged.
- `test_apply_then_blocked_is_empty`: for a list of spellings (accents,
  case, ß/ss, width variants, zero-width inside the name, decomposed accents,
  a combining mark after the name), `blocked(apply(t, reg), reg) == []`.
  This is the property the intent asks for.

`test_invisible_characters_inside_a_name` (#125) and #165's
`test_apply_keeps_superscripts_and_fractions` still pass. Full suite green,
including render's 409 tests, which now see fewer refusals only for names that
were previously left in place.
