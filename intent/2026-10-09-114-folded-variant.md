---
status: approved
issue: 114
author: olafkfreund
---

# Intent: a folded spelling variant of a containing name is still half rewritten

## Problem

Since #109, `anonymise.apply()` (`app/anonymise.py:49-62`) replaces a referenceable name that
contains a protected name with that client's own label. It decides which names contain a
protected one on **folded** text, using `fold()` (case, accents, width, `ß` → `ss`). But the
replacement itself uses `_pattern(name)` on the name as written, with only NFKC applied and
`re.I` matching. `re.I` does not equate `ß` with `ss`, or `ö` with `o`.

So when the text spells the referenceable name differently from the registry, only the
protected part is rewritten. The result is the half-rewritten text #109 set out to remove.

Two made-up examples, traced from the code:

| Registry | Text | `apply()` gives |
| --- | --- | --- |
| protected "Strasse" ("a city firm"), referenceable "Straße Bank" | "STRASSE BANK won" | "a city firm BANK won" |
| protected "Zorp" ("a retailer"), referenceable "Zörp Logistics" | "Zorp Logistics won" | "a retailer Logistics won" |

`blocked()` (`:65-69`) folds the text, finds no protected name left, and lets the output
through.

No protected name leaks. The output simply reads oddly in generated sections, research quotes,
search titles, outcomes and labels, and the pick prompt. Every one of these goes through
`apply()`.

## Proposed outcome

- A referenceable name that contains a protected name is replaced by its own label for every
  spelling that `blocked()` treats as the same name: case, accents, width and `ß`/`ss`.
- Both examples above give "a city firm won" and "a logistics firm won" (with the labels as set).
- Text with no containing name, and protected names on their own, behave exactly as today.

## Affected users and systems

- `app/anonymise.py` `apply()` only. Its callers don't change: `render.protect()`, research
  quotes, `search.clean()` and the pick prompt.
- `tests/test_anonymise.py`.
- Bid users see cleaner text. There is no schema, infra or UI change.

## Constraints

- **Fail-closed holds.** `blocked()` still runs after `apply()` on every path, unchanged. The
  change may only add replacements with labels, never remove one.
- **Speed.** `apply()` must stay fast. The #109 review measured about 30 ms per call with
  200 + 200 clients, and search and generate call it dozens of times. No per-pair regex work.
- **Made-up names only** in code, tests and docs. The repo is public.
- Labels stay literal text, as they have since the #109 review fix.

## Open questions

1. **How should `apply()` find the folded variant?**
   - **(a) Match containing names on folded text and map the match back.** Fold the text one
     character at a time, keeping a map from folded positions to original positions. Search the
     containing names' folded patterns there, then replace the mapped spans in the original. It
     costs about 15 lines and runs only for the few containing names, so it stays fast.
     **Recommended.**
   - **(b) Widen each containing name's pattern** with character classes for every fold variant
     (`ß|ss`, `ö|o`, and so on). The patterns get fragile, and the variants would need a list
     kept by hand.
   - **(c) Won't fix.** Nothing leaks, and spelling variants of a containing name are rare. Close
     the issue with a note. This is the cheapest option, and the odd text stays.
2. **Should protected names get the same folded matching?** Today, a folded variant of a
   protected name on its own, such as "ZÖRP" for "Zorp", is not rewritten. `blocked()` then
   withholds that output, which is the correct fail-closed result. With (a), these could be
   rewritten instead, so more outputs would go out. **Recommended: no, not in this issue.** It
   changes what is withheld, so it deserves its own decision.

## Approved answers

1. (c) Won't fix. Odd wording, not a leak, and rare; position-mapping code in the anonymiser is a risk not worth taking now. If it shows up in real use, option (a) is the fix.
2. No: protected names keep today's matching; changing what is withheld needs its own decision.
