---
status: draft
issue: 293
author: olafkfreund
---

# Intent: a clean Connections section on the Settings page

## Problem

The Connections section that #288 added works, but it is hard to read and
looks unfinished:

- **One long table.** Seven connectors and ten settings share it, and the
  connector name repeats on every row.
- **Stacked buttons.** In each row, Clear, Restore previous and the connector's
  Save button sit one under another, so rows have uneven heights and the
  table looks ragged.
- **Unlabelled inputs.** The inputs have no visible label: only the
  setting's environment name in another column says what each one is for.
- **The save scope is unclear.** The connector's Save button sits in the
  row of its last setting, not with the connector.
- **The help text is one dense paragraph** above the table.
- **The badges don't line up.** "Two admins" and the last change share a
  cell and wrap unevenly.

## Proposed outcome

**One card per connector,** in a two-column grid on wide screens and one
column on phones, using the portal's existing card style:

```
┌─ OpenAI ───────────────────── [Configured] [Two admins] ┐
│ API key                                                 │
│ [•••••••••••••••••••••••••]  leave blank to keep it     │
│ OPENAI_API_KEY · applied by admin-a, 2026-10-15 16:19   │
│                                                          │
│ ⚠ Waiting for approval: proposed by admin-a, 16:22      │
│   [Approve] [Reject] [Withdraw]                          │
│                                                          │
│ Clear · Restore previous                       [Propose] │
└──────────────────────────────────────────────────────────┘
```

**Each card:**

- **Header:** the connector name, its status (Configured or Not configured)
  and a "Two admins" badge where it applies.
- **Fields:** each has a visible label ("Client id", "Client secret",
  "Email", "API token", "API key", "Extraction model", "Drafting model").
  Under it, a short muted line gives the environment name and the last
  change.
- **Footer:**
  - Restore previous and Clear are small secondary actions on the left.
  - The card's one Save or Propose button is on the right.
- **Open proposal:** shown inside the card as a notice, with Approve,
  Reject and Withdraw on one line.

**Around the cards:**

- **The help text** becomes three short lines above the cards.
- **The Terraform-only settings** (`S3_BUCKET`, `OLLAMA_API_BASE`, the
  model options) move to a small read-only list under the cards, each with
  its reason.

## What stays the same

- The same routes, form fields, CSRF and approval rules.
- Nothing about how values are stored, cached or audited (#288).
- No JavaScript.

## Affected users and systems

- **Admins:** they use the Settings page.
- **Code:**
  - `app/templates/settings.html`: the Connections section only
  - `app/settings.py`: field labels, if the template needs them
  - `app/static/portal.css`: a few rules, outside the tokens block
- **Tests:** `tests/test_settings.py`, `tests/test_conn_settings.py` and
  `tests/test_user_guide.py` (`docs/user-guide/admins.md` bold labels) check
  markup that changes, such as `OPENAI_API_KEY</td><td>Configured`. They are
  updated to the new markup, with the same checks: no secret is shown, and
  the status is right.

## Constraints

- **Secrets stay write-only.** No value or part of one is ever rendered.
- **Theme:** works in light and dark, with the #291 brand tokens. No colour
  literals outside the tokens block, and no inline styles.
- **Accessibility:**
  - every input has a visible `<label for>`
  - buttons say which connector they act on, through their text or an
    `aria-label`
  - touch targets stay at least 44 px on coarse pointers
- **Phones:** at 390 px the cards stack, with no horizontal scrolling.

## Open questions

1. **Two-column grid on wide screens, or one column?**
   Suggested: two columns at 900 px and up, using the existing `admin-grid`.
2. **Should the environment names (`OPENAI_API_KEY`) stay visible?**
   Suggested: yes, as small muted text under each label, because ops reads
   them.
