---
status: draft
issue: 163
intent: intent/2026-10-10-163-punctuation-in-names.md
---

# Spec: Protected names match their common punctuation variants

Approved decision: **A**, a short list. Apostrophe variants match each other;
`.` and `,` are optional; nothing else changes.

## Design

All in `app/anonymise.py`, in the two places every matcher shares, so
`apply()`, `blocked()`, `scrub()` and `has_name()` change together:

- `_SEP` gains `,`: `r"[\s,\-‐‑–—]"`. A comma in a registry name splits words
  like a space, and the join `_JOIN` (`_SEP + "*"`) lets a comma, a space, a
  dash or nothing stand between words in the text. "Zorp Inc" matches
  "Zorp, Inc", and "Zorp, Inc." matches "Zorp Inc".
- `_pattern()`: after `re.escape(w)` for each word, a `.` becomes optional
  (`\.` → `\.?`) and any apostrophe variant in the name (`'`, `’`, `‘`, `ʼ`,
  `′`) becomes the class `['’‘ʼ′]`. "Zorp Inc." matches "Zorp Inc"; "Zorpo's
  Bank" matches "Zorpo’s Bank" and the reverse.
- `fold()` is unchanged: the class in the pattern works on folded text too,
  since folding leaves these characters alone.
- Other punctuation stays literal, so "C++ Ltd" still does not match "C Ltd".

**Merge order:** after #165 (which touches only `_visible` and `apply()`'s first
line) and before #166 (which rewrites `apply()`'s loop but keeps calling
`_pattern`). This change is confined to `_SEP` and `_pattern`, so it rebases on
#165 without conflict, and #166 inherits it through `_pattern`.

## Alternatives rejected

- **B, any non-word character optional** (as `_key` does): "C++ Ltd" would
  match "C Ltd", and short names would over-replace.
- **Fold apostrophes in `fold()` only:** `apply()` matches the text as written,
  so it would still miss "Zorpo’s"; the pattern class covers both sides.

## Risks

- A dotted short name gets wider: "A.B.C" now also matches "ABC". Names are
  registry entries chosen by admins, and over-replacing a protected name's
  spelling fails safe.
- A comma between a name's words in running text ("Zorp, Inc" in a list) now
  matches; that is the intended variant.

## Verification

New `tests/test_anonymise.py::test_punctuation_variants_of_a_name`, which
fails on main:

- With `[C("Zorpo's Bank", label="a bank")]`: `apply("We helped Zorpo’s Bank", reg)
  == "We helped a bank"`, `blocked("Zorpo’s Bank", reg) == ["Zorpo's Bank"]`,
  and `"zorpo" not in scrub("core banking for Zorpo’s Bank", reg)`.
- With `[C("Zorp Inc.", label="a firm")]`: `apply("Zorp Inc said", reg) == "a firm said"`.
- With `[C("Zorp Inc", label="a firm")]`: `apply("Zorp, Inc. said", reg) == "a firm. said"`
  (the trailing dot is outside the name and stays).
- `C("C++ Ltd")` still does not match "C Ltd" (`blocked("C Ltd", reg) == []`).

Existing anonymise tests (possessives, boundaries, dashes, #125) still pass,
and the full suite is green.
