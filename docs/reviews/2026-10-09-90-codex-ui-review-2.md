## First review: status

| Finding | Status |
|---|---|
| Home hierarchy and admin links | **Done** — the search task leads; review and administration have separate sections. |
| Search form and results | **Partly** — labels, result hierarchy, suggested wording and download grouping are in place; “Other matches” still have no next action. |
| Review queue | **Partly** — tiles and clearer rows are in place; the attention tile conflicts with the visible unsourced case. |
| Review detail | **Partly** — title, metadata, quotes and decision warning are clearer; field inputs still depend on placeholders and screen reader only labels, and merged metadata remains a table. |
| Sources | **Partly** — policy guidance and editing are tucked away, and the data class is readable; the planned run-count tiles are absent. |
| Client registry | **Partly** — compact rows and disclosures work on desktop, but wrapping breaks the intended row layout on phones. |
| Model approvals | **Done** — empty state, confidential class and visible form labels are present. |
| Audit | **Partly** — title, summaries, filter and empty messages are present; populated rows still say “Yes/No” without the meaning inside each chip. |
| Research | **Partly** — question and query preview are clearer; the results and download screens were not shown in the screenshots. |
| Shared components and header | **Partly** — components are consistent, but the phone header takes too much vertical space. |
| Clipped source value; empty approvals; missing labels; checkbox targets; ambiguous repeated controls; table structure | **Mostly done** — the source value and approvals are fixed, checkbox wrappers and row action names are improved, and tables have headers. Review-detail inputs remain the visible-label exception. |

## New problems, highest impact first

1. **The phone header consumes about 243px before page content.** Seven tabs wrap across three rows after the brand and account rows on every 390px screen. Keep the tabs on one horizontally scrollable row, with a visible scroll cue: [portal.css](app/static/portal.css:26) and [base.html](app/templates/base.html:13).

2. **Client rows break into isolated pieces on phones.** The 260px minimum basis for `.who` pushes the avatar onto its own line; chips, Edit and Delete then wrap unpredictably. Give the identity a phone layout and keep actions together: [portal.css](app/static/portal.css:142) and [clients.html](app/templates/clients.html:27).

3. **The review queue says “0 Needs attention” beside a case marked “1 unsourced”; its detail page says “3 need attention.”** Those labels give different answers to the same reviewer. Align the tile’s wording or count with the row and detail definitions: [review_list.html](app/templates/review_list.html:7) and [review_detail.html](app/templates/review_detail.html:16).

4. **Review-detail value fields have no visible associated labels.** On the phone, “start” and “end” are identifiable only by placeholders, which disappear while typing. Show short labels within each field group: [review_detail.html](app/templates/review_detail.html:10).

5. **Search result actions wrap into an uneven two-row cluster on phones.** “Research this” occupies one row while formats and Word download sit below it; the primary action is less obvious than on desktop. Make the action row follow one consistent phone layout: [search.html](app/templates/search.html:23) and [portal.css](app/static/portal.css:119).

## Five best low-effort changes

1. Make the phone navigation a single scrollable row.
2. Add a phone layout for client identity, chips and actions.
3. Reconcile the review attention count and labels.
4. Make review-detail value labels visible.
5. Standardise the phone result action row.

No files were edited. Research preview/results and populated audit/model states were checked in templates only; the supplied screenshots do not show them.