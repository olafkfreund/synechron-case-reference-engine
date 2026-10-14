---
status: draft
issue: 275
author: olafkfreund
---

# Intent: Brief titles and theme headings are not flagged word by word

## Problem

A brief's title and theme headings are checked with `check_prose(...,
heading=True)` (`app/brief.py:80`). Headings are in title case: "Best
Practices for Integrating GitLab with ServiceNow", "OAuth 2.0
Configuration is Essential". `heading=True` exempts only the first word,
so every later capitalised word is treated as a name the quotes must
support:
- names 'Practices'
- names 'Integrating GitLab'
- names '2.0 Configuration'
- names 'Essential'

On the demo brief this produced 6 false flags. Flags block the brief's
download, so a sound brief can't be downloaded.

#260 fixed the same problem for case prose, with the common-English word
list (SCOWL size 40), but only for the first word of a sentence.
Headings were left out.

## Proposed outcome

- **Ordinary words pass.** In a heading, an ordinary English word is not
  treated as a name wherever it appears, because title case capitalises
  every word: Practices, Integrating, Essential, Configuration.
- **Real names are still checked:**
  - a word that is not in the common-word list (GitLab, ServiceNow)
  - an acronym
  - a word containing a digit

  All three must still be supported by the quotes, so an invented
  product in a heading is still caught.
- **Body text unchanged.** Brief body sentences and case prose behave as
  today; #260's rules are unchanged.
- **Measured.** Re-run the check over the stored briefs in the demo
  database, counting flags before and after (briefs are public research,
  so this is not confidential). Expected: the six false flags on brief 1
  go; real ones stay.

## Affected users and systems

- Anyone writing or downloading briefs, through the portal and MCP.
- `app/writeup.py` (`check_prose` with `heading=True`), `app/brief.py`,
  and the tests.

## Constraints

- **No new dependency.** It reuses `common_words()` from #260.
- **Accepted risk, as in #260.** A product named with a common word
  (Snowflake, Spark) in a heading is not flagged. A heading has no
  sentence-middle to fall back on, so this risk is slightly wider than in
  prose, and is stated in the PR.

## Open questions

1. **Every word, or only title-cased runs?** Proposed: in `heading=True`
   mode, every word that is an ordinary English word is exempt.
   Alternative: exempt only when the whole heading is title-cased.
   Simpler wins, and a heading is short.
