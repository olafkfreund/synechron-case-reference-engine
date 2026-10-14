---
status: draft
issue: 275
intent: intent/2026-10-14-275-brief-heading-flags.md
---

# Spec: Brief titles and theme headings are not flagged word by word

The intent's question was approved as proposed: in heading mode, ordinary
words are exempt anywhere in the heading.

## Design

**One change, in `check_prose` (`app/writeup.py`), for `heading=True`
only.**
- **The new rule.** After a capitalised run (`_TERM`) is found, every word
  of the run is dropped when all of these hold:
  - its lowercase form is ordinary: in `COMMON_STARTS`, used in lowercase
    in the quotes (`lower`), or in `common_words()`, which are #260's
    three tests
  - it isn't an acronym (all caps, two or more letters)
  - it contains no digit
- **What's left is checked as today.** "Integrating GitLab" becomes
  "GitLab", which is checked against the quotes. "Best Practices"
  becomes empty, so there is nothing to check. "OAuth 2.0
  Configuration" becomes "OAuth 2.0", which is checked.
- **What doesn't change:**
  - The existing first-word drop for headings is unchanged, and is now
    covered by the general rule.
  - Body text (`heading=False`) is unchanged, and so are #260's
    sentence-start rules.
  - The number and price checks are unchanged.
- **Callers.** `app/brief.py:86` is the only caller with `heading=True`,
  so nothing else changes.

## Alternatives rejected

- **Exempt only fully title-cased headings.** It is more code, and a
  heading with one lowercase word ("Use n8n for workflows") would lose the
  exemption.
- **Skip checking headings entirely.** An invented product in a heading
  would no longer be caught.

## Risks

- **A product named with a common word, in a heading,** is not flagged:
  "Snowflake Migration Approach" passes. This is the same accepted risk as
  #260, slightly wider here because headings have no sentence-middle to
  fall back on. It is stated in the PR.

## Verification

- **Unit tests in `tests/test_writeup.py`** with `heading=True`:
  - "Best Practices for Integrating GitLab with ServiceNow", with quotes
    naming GitLab and ServiceNow: no flags
  - "Use Zorbulate for Integration": `names 'Zorbulate'`
  - "SAP Rollout Plan" with no SAP in the quotes: `names 'SAP'`
  - "Plan9 Migration": `names 'Plan9'`
  - "OAuth 2.0 Configuration is Essential", with OAuth 2.0 quoted: no
    flags
  - The same strings with `heading=False` behave as before.

  Each is negative-controlled.
- **Measured.** Re-run `check()` over the stored briefs in the demo
  database, counting flags before and after. Briefs are public research.
  Expected: brief 1's six heading flags go, and its three "has no
  citation" flags stay; those are real.
- **Full suite:**
  `docker compose build app && docker compose run --rm app pytest`.
- **Reviews:** Opus, Codex and agy, read-only.
