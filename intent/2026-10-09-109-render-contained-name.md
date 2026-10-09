---
status: approved
issue: 109
author: olafkfreund
---

# Intent: A referenceable name that contains a protected name is half rewritten

## Problem

Two clients are registered with made-up names:

| Client | Status | Label |
|---|---|---|
| `Zorp` | not referenceable | "a retailer" |
| `Zorp Logistics` | referenceable | "a logistics firm" |

`anonymise.apply()` (`app/anonymise.py:49-58`) rewrites only the names and aliases of non-referenceable clients. So in any text that says "Zorp Logistics", it finds `Zorp` as a whole word and replaces it, which gives "a retailer Logistics". `blocked()` then finds nothing, so the text passes the fail-closed check and goes out like that.

**Where it shows up.** `apply()` runs on every string that leaves the system:

- `render.protect()` (`app/render.py:123-129`) walks every string of every generated section: the client line, title, challenge, solution, outcomes and summary. All formats are affected (docx, pptx, pdf, md).
- Research quotes, per claim (`app/render.py:57`).
- `search.clean()` (`app/search.py:81-84`): titles and outcomes in search results.
- The LLM pick prompt (`app/search.py:96`).

Since #97, search shows a case's client line as "a client" when the label contains a protected name (`app/search.py:131`). But case text in search, and everything in generated output, is still half rewritten. Nothing leaks: the protected name is replaced every time. The output is just wrong and confusing, and it hints at both clients.

## Proposed outcome

Text that names a referenceable client whose name contains a protected name never comes out half rewritten, in any output. The whole referenceable name is replaced in one go, and the protected name inside it is never shown. The client line, the case text, search and generated output all agree.

## Affected users and systems

- **Bid teams:** generated documents, and search results for those clients.
- **Code:** `app/anonymise.py`, and through it `app/render.py` and `app/search.py`, plus their tests.
- **Data:** none. No schema or data change.

## Constraints

- **Fail closed.** `blocked()` must still find nothing in any output, and `protect()` still withholds on any hit. No change may let a protected name or alias through, including via a referenceable name or alias.
- **Other referenceable names are untouched.** A referenceable name that contains no protected name still appears as written.
- **One place.** The fix lives in `anonymise`, so every caller of `apply()` gets it. No per-caller patch.
- **Made-up names only.** The repo is public, so tests and docs use invented names, never real clients.

## Open questions

1. **What replaces the whole referenceable name?**
   - **(a) The referenceable client's own label** ("a logistics firm"). It says the most of the three, and labels already exist for every client (the column is `not null`).
   - **(b) "a client"**, which matches what search's client line shows since #97.
   - **(c) Mask the name and restore it**, so "Zorp Logistics" stays visible. `blocked()` would then find `Zorp` inside it and withhold the whole output, unless `blocked()` were taught to skip it. That weakens the fail-closed check, so I don't suggest it.

   **Recommendation: (a).** Then decide whether search's client line (`app/search.py:131`) should switch from "a client" to the label, so that both outputs agree.
2. **Aliases.** Should the same rule cover a referenceable *alias* that contains a protected name or alias, not just the main name? Recommendation: yes, because `_names()` already treats names and aliases the same way.

## Approved answers

1. (a) Replace the whole referenceable name with that client's own label, in anonymise, for every caller. Search's client line shows that label too, not "a client".
2. Yes: referenceable aliases that contain a protected name get the same rule.
