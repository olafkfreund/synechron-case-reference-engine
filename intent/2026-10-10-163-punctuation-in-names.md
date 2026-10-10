---
status: draft
issue: 163
author: olafkfreund
---

# Intent: A protected client name with a curly apostrophe or other punctuation is neither replaced nor blocked

## Problem

`_pattern()` (`app/anonymise.py:40-43`) treats only spacing and dashes as
flexible between a name's words. Every other character is matched literally,
and `fold()` (`:30-33`) does not map apostrophe variants. `apply()`,
`blocked()` and `scrub()` all build on these, so all three miss.

I reproduced these on main:

| Registry name | Text | Result |
|---|---|---|
| `Zorpo's Bank` | `Zorpo’s Bank` (Word's curly apostrophe) | Unchanged, not blocked, not scrubbed |
| `Zorp Inc` | `Zorp, Inc` | Unchanged, not blocked |
| `Zorp Inc.` | `Zorp Inc` | Unchanged, not blocked |

So a protected name with ordinary punctuation differences:

- shows in downloads, search titles and summaries, and the research "ours"
  text;
- goes to Brave in a research question pasted from Word.

`resolve()` strips punctuation in `_key`, so the case is still linked to the
client and shown under its label, while the body names the client. This is a
confidentiality leak, not just an annoyance.

## Proposed outcome

- A protected name matches its common punctuation variants in `apply()`,
  `blocked()` and `scrub()` alike:
  - apostrophe variants (`'`, `’`, `‘`, `ʼ`, `′`) match each other;
  - a `.` or `,` within or at the end of a name is optional, and a comma may
    stand where the registry has a space.
- The three test rows above are replaced, blocked and scrubbed.
- Names with other punctuation keep their exact match, so "C++ Ltd" does not
  start matching "C Ltd".

## Affected users and systems

- `app/anonymise.py` (`_pattern`, `fold`), and through them every render,
  search display, research query and `clients.clean` check (`has_name`).
- Bid writers, and anyone reading research.

## Constraints

- Must not weaken any existing match. It only widens matching.
- Wider matching can over-replace. Keep the set of flexible characters small
  and listed.
- `clients.clean` uses `has_name` and `_key`. A label that now matches a name
  through punctuation will be refused when it is saved; that is intended.
- **Overlap with #165 and #166, which also edit `app/anonymise.py`:**
  - Keep this change inside `_pattern()` and `fold()`. #165 edits the first
    line of `apply()`, and #166 rewrites `apply()`'s matching loop.
  - If #166 matches `apply()` on `fold()`'s output, the apostrophe mapping
    belongs in `fold()`, so that `apply()` and `blocked()` get it together.

## Open questions

1. **How wide is "common punctuation"?**
   - **A. A short list:** apostrophe variants fold to `'`, and `.` and `,` are
     optional between and after words.
   - **B. Any non-word character in a name is optional**, the way `_key`
     treats it. This is wider, but "C++ Ltd" would then also match "C Ltd",
     and short names over-replace.

   **Recommendation: A.** It covers the reported cases and keeps the
   existing "C++ Ltd" rule.
