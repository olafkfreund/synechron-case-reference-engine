# UI review

The approved palette and typography are already in place. The biggest improvement is to give each page a clear first action, move operational detail behind disclosures, and make empty states look intentional. These changes fit server-rendered Jinja2 and CSS; they do not need new routes or JavaScript.

## Home

[landing.html](app/templates/landing.html:3)

**Problems:** Seven identical full-width cards give routine admin links the same weight as “Find reference cases”; the repeated signed-in line adds little beside the header identity; the page has no visual cue for the main bid workflow.

**Redesign:** Make **Find reference cases** the lead card, with Research beside it. Put Review queue in a smaller “Needs review” area, using a count only if the page already receives one. Group the four admin links in a compact grid below. Give each link a short title, one-line description and small inline SVG; keep the whole card as the link.

## Search and results

[search.html](app/templates/search.html:3)

**Problems:** Industry, region and technology rely on placeholders or `aria-label` instead of visible labels (lines 7–9); result cards have five equally prominent download buttons that wrap into a second row (lines 20–23); “suggested wording” looks like part of the approved case even though downloads exclude it (lines 19, 28); “Other matches” are plain text blocks with no clear next action (lines 29–30).

**Redesign:** Keep the bid context as the main input and place the three labelled filters in a compact row. In each result, lead with title, client label, basis chip and **Why it fits**; show outcomes as a small result list. Give suggested wording a clearly titled inset and put the download distinction beside it. Keep one visible download action and place the five existing format submit buttons inside `<details>` titled “Download formats”; retain their current form names and values. Use a bordered empty state with “Try a broader bid context or remove a filter” when `ran and not top`.

## Review list

[review_list.html](app/templates/review_list.html:3)

**Problems:** The five-column table makes every case look equally urgent; zeroes dominate the attention columns (lines 5–8); “new” does not tell the reviewer what to do; the re-review section occupies space even when empty (lines 10–14).

**Redesign:** Put compact tiles for **Awaiting review**, **Needs attention** and **Due soon** above the list, calculated from the existing `cases` and `due_soon` collections. In each row, make the case title and “Review case” the clear action; combine nonzero attention and unsourced counts into labelled chips. Keep source document as secondary text. Show due-soon items in a separate short list; when empty, use a single calm line rather than another prominent section.

## Review detail

[review_detail.html](app/templates/review_detail.html:3)

**Problems:** “Case 10” hides the case title; document, source, external ID and status are crammed into one sentence (lines 3–9). Repeated “Save” buttons make the long field table hard to scan (lines 18–27). Source quotes and editable values have little visual separation. The consequence of approval appears only inside a long button label near the bottom (lines 30–34).

**Redesign:** Use the existing title row as the heading, with “Case 10”, document and source in a metadata card underneath. Put status, basis and counts of unsourced or attention fields beside the heading. Split fields visually into “Needs attention” and remaining fields with template grouping, while preserving every row’s existing form ID and action. Give source quotes a tinted, labelled panel within each row and keep Save beside its field. Put approval and rejection in a final action card with a short warning above the buttons: unsourced fields will be emptied on approval. Keep merged-contract metadata in `<details>` when present.

## Sources

[sources.html](app/templates/sources.html:3)

**Problems:** Three dense policy paragraphs push the actual sources down (lines 4–9). Raw JSON config is exposed as the main table content (line 12). At 1440px, the access-group field and data-class select are visibly squeezed; the select shows only “con” for “confidential” (lines 13–14). Last-run counts would render as a line-per-key dump, while the screenshot’s never-run rows have no useful count state (lines 17–18). The add form presents three unrelated JSON examples in one placeholder (line 27).

**Redesign:** Open with a brief rule and move the full data-class, contract and access-group guidance into labelled `<details>`. Present each source as a summary row or card: name, kind, enabled chip, full data-class chip, last run and key counts. Put config JSON and less common counts inside “Configuration and run details.” Give access groups a full-width labelled edit field in that detail area. Keep Save and Crawl now together, but distinguish configuration save from starting a crawl. In “Add source,” provide visible labels and put the existing format examples in nearby help text rather than the placeholder.

## Client registry

[clients.html](app/templates/clients.html:3)

**Problems:** The long alias policy dominates the page before the registry (lines 4–8). Repeated text fields make the table feel like a spreadsheet and obscure which client is being edited (lines 9–16). The 18px checkboxes have small click targets, and repeated `aria-label="referenceable"` or `"logo allowed"` gives little row context. Delete sits immediately beside Save on eligible rows (line 16).

**Redesign:** Lead each client row with its name and initials avatar, then show referenceability and logo permission as status chips. Keep the same inputs in an “Edit client” `<details>` area with visible labels that include the client name. Place the alias guidance in one “How aliases work” disclosure; retain the warning about ordinary words. Separate the Add client form from existing clients. Put Delete behind a short disclosure in the eligible row, with the existing delete form unchanged.

## Model approvals

[models.html](app/templates/models.html:3)

**Problems:** The screenshot shows an empty table header with no explanation or state (lines 6–10). The form gives no visible indication that approval is for **confidential** data because the field is hidden (lines 13–15). Model ID, expiry and note have no visible labels; the date format is left to a bare control (lines 13–17).

**Redesign:** When there are no approvals, replace the empty table with a compact state card: “No model approvals yet,” followed by the existing local or own-cloud rule. For populated rows, show model ID first, then active/expired chip, expiry and approver; keep Revocation as a distinct action. Above the form, show “Data class: Confidential” as read-only display text while retaining the hidden field. Use visible labels for model ID, expiry date and note, and keep the 12-month limit beside expiry.

## Audit

[audit.html](app/templates/audit.html:3)

**Problems:** The heading says “generated outputs,” but the page also covers three kinds of admin changes (lines 13–29). The exact-sub filter stretches almost the full width. Four separate “None” messages create a page that looks unfinished in the screenshot. When populated, raw “yes/no” columns and case IDs will be hard to scan (lines 7–10).

**Redesign:** Title the page **Audit activity**. Put four compact section summaries at the top: generated outputs, model approvals, data-class changes and access-group changes. Keep the exact-sub filter near the generated-output section with a visible label and bounded width. Give each empty section a specific one-line state. In populated output rows, show format and time prominently; render anonymised and acknowledgement values as labelled status chips, and case IDs as a compact linked list. Retain pagination with clearly labelled newer/older controls.

## Research

[research.html](app/templates/research.html:3), [research_preview.html](app/templates/research_preview.html:3), [research_view.html](app/templates/research_view.html:3)

**Problems:** The input page is mostly an empty canvas around one large form. “1. Question” implies a visible step sequence that only appears after submission. The privacy explanation is a paragraph above the form rather than part of the decision to send (research lines 4–9). The preview’s exact outgoing query is plain code text, so the key safety check has weak emphasis (preview lines 5–9). Results use dense tables for quotes and comparison (view lines 11–22).

**Redesign:** Make the input a narrower task card with a short privacy line and a “How query preview works” disclosure. Use **Your research question** as the label; show the workflow as “Ask → Preview query → Send” near the button. On preview, put the exact query in a prominent bordered panel immediately above Send and Cancel. On results, make each quoted claim a compact source card with publisher, retrieved date and claim-type chip; retain comparison as a table where side-by-side reading helps. Put download controls in a final, clearly separate card with a visible format label and the existing acknowledgement wording.

## Shared CSS and markup

[portal.css](app/static/portal.css:20), [base.html](app/templates/base.html:6)

| Component | Purpose |
|---|---|
| `.page-head` | Consistent heading, short introduction, status and primary action. |
| `.stat-grid`, `.stat-tile` | Small counts on home, review and audit; no new data source required. |
| `.section-card`, `.card-head` | Group a source, client or approval with its actions. Reuse existing `.card` styling. |
| `.empty-state` | Deliberate empty tables and queues, with one useful next step. |
| `.field`, `.field-help` | Visible labels and concise help for forms currently using placeholders. |
| `.action-row` | Align a primary action with quieter secondary actions and wrap cleanly. |
| `.detail-panel` | Consistent `<details>/<summary>` treatment for guidance, config and download formats. |
| `.status-list` | Short labelled counts and chips instead of raw count lines or yes/no text. |

Extend the existing `.badge` classes rather than creating another chip system. Use small inline SVGs sparingly for empty states and primary navigation cards. The header already wraps at 1440px because `.site-header-in` allows wrapping and the navigation plus identity exceed one row ([portal.css:22](app/static/portal.css:22)); give the header an intentional two-row layout or shorten the displayed role string. Keep the present visible focus rule and ensure new disclosures, checkboxes and links have **44px clickable areas**.

## Bugs and accessibility defects to address

- **Clipped source value:** The screenshot’s “con” is a narrow select, not an empty select; the options are populated in [sources.html:14](app/templates/sources.html:14). Do not replace or alter its values.
- **No empty approvals state:** [models.html:6](app/templates/models.html:6) renders only a table header when the collection is empty.
- **Missing visible labels:** Search filters, source/client edit fields, model approval fields and the audit filter use placeholders or `aria-label` as their only names. Add associated `<label for>` elements without changing `name` attributes.
- **Small checkbox targets:** [portal.css:72](app/static/portal.css:72) sets controls to 18px; their labels or wrappers need 44px hit areas.
- **Repeated ambiguous control names:** “Save,” “Delete,” “Crawl now” and row checkboxes need client/source context in their accessible names.
- **Table markup:** Several tables omit `<thead>` and `<tbody>` ([clients.html:9](app/templates/clients.html:9), [sources.html:10](app/templates/sources.html:10), [models.html:6](app/templates/models.html:6)). Add proper header structure when restyling them.

## Priority: professional gain per change

1. **Fix form labels, checkbox targets, clipped controls and header layout.** These address immediate usability defects across multiple pages.
2. **Add real empty states and compact page heads.** This most improves the sparse audit, approvals, research and review screenshots.
3. **Reduce action clutter on search results and review detail.** The main workflows become much easier to scan.
4. **Move source and client guidance/config into disclosures.** The operational information remains available without burying the task.
5. **Add stat tiles and selective SVGs.** Useful finishing touches after hierarchy and controls work.

I did not edit files or test responsive layouts. The screenshots show demo states, so populated-table and mobile recommendations are based on the templates and CSS.