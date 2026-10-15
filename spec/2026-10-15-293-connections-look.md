---
status: approved
issue: 293
intent: intent/2026-10-15-293-connections-look.md
---

# Spec: a clean Connections section on the Settings page

## Design

### 1. Markup: one card per connector

`app/templates/settings.html`, Connections section only. The other sections (Sources, Single sign-on, Roles, MCP) don't change.

**Above the cards:** a `section-card` with the `card-head` "Connections" and three short muted lines:
- Secrets are write-only. Leave a field blank to keep its value.
- Changes reach every container within a minute.
- Model keys and models need a second admin.

  A Bedrock model also needs its ARN in `bedrock_model_arns`.

**Without Secrets Manager:** when editing is off, a single muted line says so, and the cards show status only, with no inputs.

**The cards:** a `<div class="conn-grid">` holds one `<div class="conn-card">` per connector. Inside each card, the fields sit in one `<form id="conn-{group}">`. Each small action (Approve, Reject, Withdraw, Clear, Restore previous) is its own tiny form outside the field form. So a typed secret is only ever posted to the save route. Each card:

- **Header:** `<header class="conn-head"><h3>OpenAI</h3>`, then badges:
  - `badge ok` "Configured" or `badge neutral` "Not configured"
  - for models: the current model id in a `badge neutral`
  - for two-admin connectors: `badge neutral` "Two admins"
- **Fields:** one `.field` per setting, using the existing class.
  - **Label:** `<label for="conn-OPENAI_API_KEY">API key</label>`.
  - **Input:**
    - a secret: a password input with `autocomplete="new-password"` and the placeholder "Leave blank to keep it"
    - an id: a text input with the current value
    - a model: a `<select>` whose first option is "Unchanged"
  - **Help line:** `<span class="field-help">`, holding the environment name in `<code>` and then "applied by admin-a, 2026-10-15 16:19", or "Never changed".
  - **The labels:**

    | Setting | Label |
    |---|---|
    | `GRAPH_CLIENT_ID` | Client id |
    | `GRAPH_CLIENT_SECRET` | Client secret |
    | `CONFLUENCE_EMAIL` | Email |
    | `CONFLUENCE_TOKEN` | API token |
    | `BRAVE_API_KEY`, `OLLAMA_CLOUD_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY` | API key |
    | `EXTRACT_MODEL` | Extraction model |
    | `DRAFT_MODEL` | Drafting model |

    They live in a `LABELS` dict in `app/settings.py`, next to `CONNECTORS`.
- **Open proposal:** `<div class="conn-pending" role="status">` shows "Waiting for approval: proposed by X, time", plus the new model id for a model. Approve, Reject and Withdraw are `btn-sm` buttons in one `.action-row`.
  - Each of these buttons is its own `<form class="inline">` with a hidden `proposal` input, as in #288.
  - The routes don't change.
- **Footer:** `<div class="action-row conn-foot">`, outside the field form:
  - **On the left:** `btn-sm` secondary buttons, each labelled with its setting when a card has two settings, for example "Clear client secret":
    - Restore previous, where allowed (the #288 rule: not for half a pair)
    - Clear, for secrets
  - **On the right:** `<button class="btn primary" form="conn-{group}">`, which submits the field form, with the text "Save" (one-admin) or "Propose" (two-admin) and `aria-label="Save SharePoint"` or similar.

**Terraform-only settings:** a short `<dl class="status-list">` after the grid, with the heading "Set in Terraform", holding `S3_BUCKET`, `OLLAMA_API_BASE` and the model options, each with its reason. This is the existing `status-list` style.

### 2. CSS (`app/static/portal.css`, outside the tokens block)

```
.conn-grid{display:grid;grid-template-columns:minmax(0,1fr);gap:16px}
@media (min-width:900px){.conn-grid{grid-template-columns:repeat(2,minmax(0,1fr))}}
.conn-card{border:1px solid var(--border);border-radius:var(--r-card);padding:16px;display:flex;flex-direction:column;gap:12px;background:var(--surface)}
.conn-head{display:flex;flex-wrap:wrap;align-items:center;gap:6px 8px}
.conn-head h3{margin:0;flex:1 1 auto;font-size:var(--fs-l)}
.conn-pending{background:var(--warn-bg);color:var(--warn-fg);border-radius:var(--r-control);padding:10px 12px}
.conn-foot{margin-top:auto;padding-top:12px;border-top:1px solid var(--line)}
```

These use existing tokens only. The `warn` pair already passes the contrast test in both themes. The footer stays at the bottom, so cards in the same row line up.

### 3. Behaviour: unchanged

- **Routes:** the same, under `/admin/settings/connections/...`.
- **Form fields:** the same names (`GRAPH_CLIENT_ID`, …), plus `proposal` for decisions.
- **Approvals:** the same rules.
- **Data:** the page still never renders a secret value.

### 4. Tests

**Updated to the new markup, same assertions:**
- `tests/test_settings.py:test_settings_redacts_secrets_and_urls`:
  - `OPENAI_API_KEY</td><td>Configured` is replaced by a check that the OpenAI card has the "Configured" badge
  - no sentinel appears
- `tests/test_conn_settings.py`: any checks of markup, such as "Waiting for approval".

**New:**
- **Labels:** every input on Settings has a `<label for>` that matches its id.
- **One primary button per card:** each connector card has exactly one `btn primary`.
- **No nested forms:** the Connections section contains no `<form` inside a `<form`.

**User guide:** `docs/user-guide/admins.md` is updated if its bold labels change. **Save** and **Propose** stay; the button text "Save SharePoint" becomes "Save".

## Alternatives rejected

- **Keep the table and only restyle the buttons.** The table still repeats the connector name, and the save scope stays unclear.
- **`<details>` accordions per connector.** They hide the status. Admins open the page to see what's configured.
- **A separate edit page per connector.** It's an extra click and more routes, for seven small forms.
- **JavaScript to show or hide inputs.** The intent rules it out.

## Risks

- **A typed secret must reach only the save route.**
  - The field form holds the inputs, and only the primary button submits it, through `form=`.
  - Clear, Restore, Approve, Reject and Withdraw are separate forms with no inputs except `proposal`.
  - A rejected alternative: `formaction` buttons inside the field form would post a half-typed secret to the decision routes.
- **Test churn.** Tests that match exact markup change; their assertions don't.
- **Narrow screens.** At 390 px the cards stack. Long model ids wrap inside the badge (`overflow-wrap:anywhere`).

## Verification

- **Tests:**
  - `docker compose build app && docker compose run --rm app pytest tests/test_settings.py tests/test_conn_settings.py tests/test_ui.py tests/test_user_guide.py -q`
  - then the full suite
- **Negative control:** remove one `<label for>`; the new label test fails.
- **Demo screenshots of Settings,** headless Chromium, with seeded demo values only:
  - light and dark at 1280 px
  - light at 390 px
  - one with an open model proposal
