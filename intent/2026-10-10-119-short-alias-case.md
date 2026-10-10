---
status: draft
issue: 119
author: olafkfreund
---

# Intent: Short all-caps alias: apply() and blocked() disagree on case

## Problem

`_pattern` (`app/anonymise.py:35-36`) matches an alias of up to five capitals
case-sensitively. This was a deliberate choice in the first anonymiser commit,
pinned by `test_short_caps_alias_is_case_sensitive`, so that an alias like
"ACB" doesn't rewrite ordinary words.

`blocked()` (`:65-69`) and `has_name()` (`:44-46`) match the folded name
against folded text. Folding lowercases the alias, so the case rule is lost and
they match "acb", "Acb" and "ops@acb.example". The two halves of the
anonymiser disagree:

- **Outputs:** `apply()` leaves "Acb team" alone, `blocked()` finds it, and the
  output is withheld (`app/render.py:127`, 409).
- **Search:** the summary falls back to the placeholder
  (`app/search.py`, `clean()`).
- **Registry:** `clients.clean` rejects any label that contains the word.

Nothing leaks, because it fails closed. But when a protected client's short
alias is also a word (made-up example: "ZEST"), every case that says "zest" can
no longer be downloaded, and nothing tells the user why.

## Proposed outcome

`apply()` and `blocked()` agree. Any text `blocked()` would refuse, `apply()`
has already rewritten. A short alias then no longer turns ordinary cases into
refused downloads, and protected names never pass `blocked()`.

## Affected users and systems

- `app/anonymise.py` (`_pattern`, `apply`, `blocked`, `has_name`), and through
  it render, search, research and the client registry.
- Tests: `tests/test_anonymise.py`, where the existing case-sensitive test
  changes or is removed.

## Constraints

- Fail closed. A lower or mixed case form of a protected alias must never
  reach an output.
- One rule shared by all four functions, not a per-caller patch.
- Use only made-up names.

## Open questions

1. **Which way should they agree?**
   - **A. `apply()` becomes case-insensitive for short aliases too.** "acb" is
     rewritten to the label, so outputs are always produced. The cost: an alias
     that is a common word rewrites that word ("zest" becomes "a bank") in
     outputs about that client's peers. The admin controls this by not
     registering word-like aliases.
   - **B. `blocked()` becomes case-sensitive for short aliases.** Outputs are
     never garbled, but "Acb" and "acb@…" pass through, so a protected alias
     leaks in lower case. This breaks fail-closed.
   - **C. Keep both as they are, but say why.** The 409 and the search fallback
     name the alias, so the admin can remove or change it.

   **Recommendation: A.** It is the only option that keeps fail-closed and
   makes outputs usable. B leaks, and C keeps the refusals.
2. **Should the registry warn when an alias of five or fewer letters is
   added?** **Recommendation: no.** Revisit if word-like aliases turn up in
   real data.
