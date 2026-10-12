---
status: approved
issue: 187
intent: intent/2026-10-12-187-enclosing-mark-names.md
---

# Spec: A protected name with an enclosing mark or a trademark sign leaks

## Design

Only the trademark signs and enclosing marks are fixed. The user decided on
2026-10-12 that '·', '/', '_' and '.' between words do not become separators;
those spellings still need an alias.

**One change, in `fold` (`app/anonymise.py:38-41`).** Before NFKC, `fold` drops
two kinds of character:

- the signs `™ ℠ ®`, in a new module constant `_SIGNS = "™℠®"` next to `_APOS`
  (`:16`);
- every enclosing mark, `unicodedata.category(ch) == "Me"`.

```python
def fold(s: str) -> str:
    s = "".join(ch for ch in s if ch not in _SIGNS and unicodedata.category(ch) != "Me")
    s = unicodedata.normalize("NFKD", _visible(s).casefold())
    return "".join(ch for ch in s if not unicodedata.combining(ch))
```

`fold` is the one place that decides what matching sees. It must drop the signs
before NFKC, because NFKC rewrites ™ as "TM" and ℠ as "SM", and those letters
then stick to the name. Every matcher goes through `fold`: `blocked` (`:115`),
`has_name` (`:67`), `scrub` (`:122`), `_key`/`resolve` (`:133-140`) and
`_replace_once` (`:72`). So `apply` and `blocked` keep agreeing, which the
fail-closed rule needs. Nothing outside `app/anonymise.py` calls `fold` or
`_visible`; I checked with grep.

`_replace_once` needs no change:

- It folds the text one character at a time. A dropped character folds to "",
  so it simply adds nothing to `at`.
- A match that runs across a sign or mark maps back to the original span, sign
  included.
- A trailing sign or mark folds to "", so the existing loop at `:85-86` that
  extends `e` over characters folding to "" takes it too. "Zorpale Bank™"
  becomes "a regional bank".
- Text outside the replaced spans is never rewritten, so "Acme™ Cloud" keeps
  its ™.

I prototyped this on main. With the protected client "Zorpale Bank", all nine
leaking spellings from the intent became the label, and `blocked()` returned
`[]` for each. "Acme™ Cloud and Zorpale Bank® win" became "Acme™ Cloud and X
win". "Zorpale/Bank" stays unmatched, as decided.

## Alternatives rejected

- **Add the signs and marks to `_SEP`.** This would cover a sign between words
  ("Zorpale™ Bank"). It would not cover one inside a word ("Z⃝orpale"), and not
  ™ after NFKC has made it "tm". It also lets the sign be a separator in names
  that contain it.
- **Strip the signs in `apply()` before matching.** This changes the visible
  text everywhere, so "Acme™ Cloud" would lose its ™. It also leaves
  `blocked()` blind to the same spellings, which breaks the fail-closed rule.
- **Drop all symbols (category So).** Too wide. It would also drop "°" and
  arrows from matching, so "10°C" or "A→B" would fold differently.
  The leak here comes from three signs and the enclosing marks.
- **Count enclosing marks as combining.** `unicodedata.combining()` gives
  class 0 for Me, so the existing filter can't see them. Checking the category
  directly is the same one test.

## Risks

- **Wider `_key`.** A client "Zorpale™" now has the same key as "Zorpale".
  The registry would refuse a second client with that spelling as a duplicate,
  and `resolve` would link either one. That's the right result for a registry
  of names.
- **A trailing sign after a name that wasn't replaced before** is now part of
  the replaced span. "Zorpale Bank®" used to give "X®" and now gives "X". This
  is the outcome the intent asks for.
- **A name that contains a sign in the registry** ("Zorpale™") now matches
  "Zorpale" in text. That is broader, and it is the safe direction.
- **`scrub` output loses the signs.** This only affects outgoing search
  queries.
- **No host risk.** This is pure Python with no schema or infra change.

## Verification

New tests in `tests/test_anonymise.py`. Each fails on main and passes after the
change:

1. `test_trademark_signs_and_enclosing_marks_in_a_name` is parametrized over
   "Zorpale™ Bank", "Zorpale® Bank", "Zorpale Bank™", "Zorpale Bank℠",
   "ZorpaleBank™", "Z⃝orpale Bank", "Zorpale⃝ Bank",
   "Zorpale⃣ Bank" and "Zorpale҈ Bank". For each, with the protected
   client "Zorpale Bank":
   - `blocked(t, reg) == ["Zorpale Bank"]`;
   - `apply(f"{t} won", reg) == "a bank won"`, which shows the trailing sign
     goes with the name;
   - `blocked` of that output is `[]`.

   On main, `blocked` returns `[]` and `apply` leaves the text unchanged.
2. `test_signs_outside_a_name_stay`:
   - `apply("Acme™ Cloud and Zorpale Bank® win", reg) == "Acme™ Cloud and a bank win"`;
   - "Zorpale/Bank" is unchanged and `blocked` returns `[]`, which pins the
     decision.

   On main the first assert gives "...a bank® win", so the test fails.

The existing tests stay green. In particular, `test_apply_keeps_superscripts_and_fractions`
and `test_apply_then_blocked_is_empty` show that 10⁶ and the #166 spellings are
untouched. The full suite must pass locally:
`docker compose build -q app && docker compose run --rm app timeout 900 pytest -q -p no:cacheprovider`.
