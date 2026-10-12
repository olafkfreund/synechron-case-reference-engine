---
status: draft
issue: 187
author: olafkfreund
---

# Intent: A protected name with an enclosing mark or a trademark sign leaks

## Problem

`apply()` and `blocked()` (`app/anonymise.py`) both match protected names on
`fold(text)`. `fold` drops format characters (Cf) and the combining marks that
`unicodedata.combining()` reports. It keeps two kinds of character that sit
inside or right after a name:

- **Enclosing marks** (category Me, such as U+20DD and U+20E3, or U+0488). They
  have combining class 0, so `fold` keeps them, and they break the word.
- **Trademark-style signs.** NFKC turns "™" into "TM" and "℠" into "SM". These
  become word characters stuck to the name. "®" stays a symbol between the words.

With the protected client "Zorpale Bank", I reproduced each of these on main.
`apply()` left the text unchanged and `blocked()` returned nothing:

- "Zorpale™ Bank", "Zorpale® Bank", "Zorpale Bank™", "Zorpale Bank℠", "ZorpaleBank™"
- "Z⃝orpale Bank", "Zorpale⃝ Bank", "Zorpale⃣ Bank", "Zorpale҈ Bank"

"Zorpale Bank®" works, because the sign is after the last word and is not a word
character.

This is a leak, not a refusal. The protected name goes into downloads, search
summaries and the text sent to DRAFT_MODEL, and the fail-closed check does not
stop it. Trademark signs are common in marketing copy, so case documents will
contain them.

## Proposed outcome

- A protected name followed or broken by an enclosing mark, or followed by ™, ℠
  or ®, is replaced by `apply()`. If `apply()` misses it, `blocked()` finds it.
- `blocked(apply(text))` stays empty for these spellings. Wave 4 made this the
  rule for every spelling `blocked()` can see (#166).
- When a name is replaced, the sign after it goes too. "Zorpale™ Bank" becomes
  "a regional bank", not "a regional bank™".
- Text that isn't a protected name keeps its marks and signs, so "Acme™ Cloud"
  for a referenceable product reads as before.

## Affected users and systems

- `app/anonymise.py`: `fold`, `_pattern`, `_replace_once`.
- Callers through `apply`, `blocked`, `has_name` and `scrub`: render (downloads),
  search (summaries and `pick()`), research claims, industry context.
- Bid team and reviewers: names that leaked are hidden, and downloads that used
  to show them now show the label.

## Constraints

- Fail closed. `blocked()` must find at least everything `apply()` replaces.
- Don't widen matching in a way that makes ordinary words match a short
  protected name.
- No new dependency. The stdlib `unicodedata` is enough.
- Don't change the visible text outside a replaced name. Wave 4 kept "10⁶" from
  becoming "106" (#165), and that must stay true.
- Only made-up names in tests and docs, because the repo is public.

## Open questions

- I found more joiners that also break a match on main: "Zorpale·Bank",
  "Zorpale/Bank", "Zorpale_Bank" and "Zorpale.Bank". Should #187 also treat
  these as separators, or file them separately? If they're in, a short name
  could match more widely. For example, "Acme" would then match "acme.io".

**Decided by the user, 2026-10-12:** only the trademark signs and enclosing marks are fixed. '·', '/', '_' and '.' between words do not become separators; those spellings need an alias.
