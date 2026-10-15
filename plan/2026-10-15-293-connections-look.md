---
status: approved
issue: 293
spec: spec/2026-10-15-293-connections-look.md
---

# Plan: a clean Connections section on the Settings page

Branch `feat/293-connections-look`, worktree `../refs-engine-293`, based on `main`, which already includes #288 and #291.

## Approved decisions

- **Scope:** only the look of the Connections section. Routes, form field names, the hidden `proposal` input, approval rules and storage stay the same. No JavaScript. The page never renders a secret value.
- **Layout:** `.conn-grid`, one column, becoming two columns at 900 px and up. It holds one `.conn-card` (a `div`) per connector, in `CONNECTORS` order.
- **Card header:** `<header class="conn-head"><h3>{label}</h3>`, then badges:
  - `badge ok` "Configured" when every secret of the connector is set, else `badge neutral` "Not configured"
  - for the models card, no configured badge; instead, each field's help line shows its current model
  - `badge neutral` "Two admins" on two-admin connectors
- **Fields:** inside `<form id="conn-{group}" method="post" action="/admin/settings/connections/{group}">`, one `.field` per setting.
  - **Label:** `<label for="conn-{NAME}">{LABELS[NAME]}</label>`.
  - **Input:**
    - a secret: `type="password"`, `autocomplete="new-password"`, `placeholder="Leave blank to keep it"`
    - an id: `type="text"`, holding the current value
    - a model: a `<select>` whose first option is "Unchanged"
  - **Help line:** `<span class="field-help"><code>{NAME}</code> · {last change or "Never changed"}</span>`. For a model it also shows "now {current model}".
- **Labels** go in `LABELS` in `app/settings.py`:

  | Setting | Label |
  |---|---|
  | `GRAPH_CLIENT_ID` | Client id |
  | `GRAPH_CLIENT_SECRET` | Client secret |
  | `CONFLUENCE_EMAIL` | Email |
  | `CONFLUENCE_TOKEN` | API token |
  | `BRAVE_API_KEY`, `OLLAMA_CLOUD_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY` | API key |
  | `EXTRACT_MODEL` | Extraction model |
  | `DRAFT_MODEL` | Drafting model |

- **Open proposal:**
  - **Notice:** a `<div class="conn-pending" role="status">` inside the card, outside the field form, saying "Waiting for approval: {NAME label} proposed by X, time", with ": {model}" for a model.
  - **Buttons:** Approve, Reject and Withdraw sit in one `.action-row`. Each is its own `<form class="inline">` with the hidden `proposal` input.
  - **The field for that setting stays read-only:** no input, only the help line "waiting for approval".
- **Footer:** `<div class="action-row conn-foot">`, outside the field form.
  - **Left:** `btn-sm` forms for:
    - "Restore previous", where allowed: a single-setting connector or a two-admin setting, never half a pair
    - "Clear", for secrets

    When a card has two settings, the button text names the setting, for example "Clear client secret". Otherwise it is just "Clear". Each button has `aria-label="Clear {NAME}"`.
  - **Right:** `<button class="btn primary" form="conn-{group}" aria-label="Save {label}">Save</button>`, or "Propose" on two-admin cards. It is shown only when at least one of the card's settings has no open proposal.
- **Read-only state:** when `editable` is false, the cards show the header and help lines only, with no inputs and no footer. One muted line says editing needs Secrets Manager.
- **Terraform-only settings:** after the grid, `<h3>Set in Terraform</h3>` and a `<dl class="status-list">` holding `S3_BUCKET` (its value), `OLLAMA_API_BASE` (its value) and `EXTRACT_MODEL_OPTIONS, DRAFT_MODEL_OPTIONS` ("Not shown"), each with its reason.
- **Help text** above the grid, as three short muted lines:
  - Secrets are write-only. Leave a field blank to keep its value.
  - Changes reach every container within a minute.
  - Model keys and models need a second admin. A Bedrock model also needs its ARN in `bedrock_model_arns`.
- **CSS:** outside the tokens block, using existing tokens only:

  ```
  .conn-grid{display:grid;grid-template-columns:minmax(0,1fr);gap:16px}
  @media (min-width:900px){.conn-grid{grid-template-columns:repeat(2,minmax(0,1fr))}}
  .conn-card{border:1px solid var(--border);border-radius:var(--r-card);padding:16px;display:flex;flex-direction:column;gap:12px;background:var(--surface)}
  .conn-head{display:flex;flex-wrap:wrap;align-items:center;gap:6px 8px}
  .conn-head h3{margin:0;flex:1 1 auto;font-size:var(--fs-l)}
  .conn-pending{background:var(--warn-bg);color:var(--warn-fg);border-radius:var(--r-control);padding:10px 12px}
  .conn-foot{margin-top:auto;padding-top:12px;border-top:1px solid var(--line)}
  .conn-card .field-help{overflow-wrap:anywhere}
  ```

**Who does what,** at the requester's request:
- **Claude:** step 1 (markup, labels and CSS) and step 3 (verification).
- **agy:** step 2 (the tests).
- **Codex:** the read-only review, alongside Opus and agy.

## Steps

### 1. Markup, labels and CSS (Claude)

- **`app/templates/settings.html:3-24`:** replace the page-head sentence, the Connections table and the Terraform rows with the markup above. Keep the Sources, Single sign-on, Roles and MCP sections exactly as they are.
- **`app/settings.py:17-24`:**
  - add `LABELS`
  - pass `labels=LABELS` to the template
  - pass `last` with the same shape as today
  - `proposals[name]` keeps `(actor, at, new, id)`
- **`app/static/portal.css`:** the CSS above, after `.status-list` (`:175-176`).
- **`docs/user-guide/admins.md:43-50`:**
  - "Press **Save** on a one-admin connector" stays.
  - "Press **Clear** on a secret row" becomes "Press **Clear** under a secret".
  - Bold text must stay real button labels, so check it against `tests/test_user_guide.py`.
- **Verify:** `docker compose build app && docker compose run --rm app pytest tests/test_settings.py tests/test_conn_settings.py tests/test_ui.py tests/test_user_guide.py -q`.

  Expected failures until step 2:
  - `tests/test_settings.py:35`
  - `tests/test_conn_settings.py:169`
- **Traps:**
  - No inline styles and no colour literals (`test_templates_have_no_inline_colours`, `test_css_has_no_literal_colours`).
  - No nested forms.
  - The `form=` attribute only on the primary button.
  - Never render a secret.

### 2. Tests (agy)

**Update:**
- **`tests/test_settings.py:35`:** assert the OpenAI and Gemini cards each show "Configured". Find the card by its `<h3>` text and check the badge before the next `conn-card`. No sentinel appears.
- **`tests/test_conn_settings.py:169`:** the same kind of check for the Brave card.

**New tests in `tests/test_settings.py`:**
- **`test_connections_inputs_have_labels`:** every `<input` and `<select` inside the Connections section has an `id`, and a `<label for="…">` with that id exists. Render as admin with editing on: use the `managed` and `admins` fixtures from `tests/test_conn_settings.py`, or set `SETTINGS_SECRET_IDS` and `mock_aws` the way those fixtures do.
- **`test_each_connection_card_has_one_primary_button`:** split the section on `class="conn-card"`. Each card has exactly one `btn primary`, unless every setting in it has an open proposal.
- **`test_connections_has_no_nested_forms`:** walking the Connections section, `<form` never opens while another `<form` is open.

**Traps:**
- Reuse the existing fixtures (`env`, `client`, `ADMIN`; `managed` and `admins` live in `tests/test_conn_settings.py`).
- Don't change behaviour tests.

### 3. Verify (Claude)

- **Full suite:** `docker compose build app && docker compose run --rm app pytest`.
- **Negative control:** drop one `<label for>`; `test_connections_inputs_have_labels` must fail. Then restore it.
- **Demo:**
  - Restart `web` in `refsdemo` from this worktree, with the Brave key inline, never printed.
  - Propose a model change as one demo admin.
  - Take headless Chromium screenshots of Settings, using a signed demo session and a `<base>` tag:
    - light at 1280 px, with the proposal showing
    - dark at 1280 px
    - light at 390 px
  - Show seeded demo values only.

## Tests

```bash
cd ../refs-engine-293
docker compose build app && docker compose run --rm app pytest tests/test_settings.py tests/test_conn_settings.py tests/test_ui.py tests/test_user_guide.py -q
docker compose run --rm app pytest     # full suite
```

**Reviews:** Opus (the plan plus the diff), Codex read-only, and agy in plan mode.

## Rollback

Revert the PR. The change is markup and CSS only.
