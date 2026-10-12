---
status: approved
issue: 197
intent: intent/2026-10-12-197-output-preview.md
---

# Spec: Preview a case output before downloading it

## Design

The intent's recommendations are adopted (intent approved 2026-10-12):

- the preview shows content only: one HTML page built from the same sections
  as the downloads, not a picture of the Word or PDF file;
- opening a preview is not logged in `generations`. Only a download is
  logged, as today.

### 1. One builder shared by preview and download

`generate()` (`app/render.py:352-405`) does three things in turn: it checks
the request, builds `sections` and `industry`, then renders and logs. Move the
first two into a helper in the same file:

```python
def build(conn, case_ids, research_id, industry_context_ack, user) -> tuple[list[int], list[dict], dict | None, bool]:
    """ids, sections, industry, anonymised: everything generate() checks, in the same order."""
```

It takes the body of `generate()` from the dedupe, the 3-case limit, the
no-case acknowledgement, the research lookup, the `VISIBLE` case query,
`section()`, `protect()` (a `Withheld` becomes 409) and `industry_section()`,
down to the "no usable public statements" check. Its error messages are
unchanged.

`generate()` keeps the format check, then calls `build()`, renders, and
inserts the audit row. Its behaviour does not change.

Because both routes call the same builder, the preview cannot show anything
the download would not contain, or the other way round.

### 2. `POST /preview`

A new route in `app/render.py`, with the same form fields as `/generate`
(`case_ids`, `research_id`, `industry_context_ack`). A `format` field is
accepted and ignored, so the existing forms can post to it unchanged. The role
is `user`, as for `/generate`.

It calls `build()` and returns `page(request, "output_preview.html", user,
...)` (`app/review.py:43`). It inserts no `generations` row. Errors are the
same HTTP errors as `/generate`.

### 3. `templates/output_preview.html`

The page extends `base.html`. It shows the same structure as the Markdown
template `MD` (`app/render.py:283`), in HTML:

- for each case: the title, client, engagement note, summary, the details as
  a list, the blocks under headings, and the lists as bullets;
- the industry section with its heading, its disclaimer and each statement:
  the phrase, the quote, the publisher, the URL as text (not a link, as in the
  outputs) and the date, followed by the note.

Jinja autoescaping (on for `templates/`) escapes every value. There is no
`| safe`.

Below the content there is a download form posting to `/generate`. It carries
the same hidden `case_ids`, `research_id` and `industry_context_ack` fields as
the preview, and the five format buttons from the case card. A line says
"This is the content of the download; the layout comes from the Word and
PowerPoint templates."

### 4. Entry points (no JavaScript)

- **Case card and "Download all"** (`templates/search.html`): add a "Preview"
  button to each `/generate` form with `formaction="/preview"`. It is the
  first button, so the layout stays the same.
- **Research result** (`templates/research_view.html`): add a "Preview"
  button with `formaction="/preview"` next to Download. The checkbox and the
  acknowledgement are posted as they are.

### 5. Docs

Add one sentence on Preview to the user guide page that describes downloads
(`docs/user-guide/`, wherever the `/generate` buttons are described). Fix any
wording in `test_user_guide` that checks the button labels.

## Alternatives rejected

- **A real visual preview** (convert Word, PowerPoint or PDF to images): it
  needs LibreOffice in the web image (it is in the worker image only), takes
  seconds per view, and shares the PDF slots used by downloads.
- **Render the Markdown output as HTML** (`markdown-it-py` is in the lock
  only as a dependency of another package): it adds a dependency to the HTML
  path, and the escaping rules of `md()` were written for files, not for
  browsers. A Jinja template over the same sections is simpler and is escaped
  by default.
- **A `format=preview` value on `/generate`:** the research form sends
  `format` from a select, and a button with the same field name would send
  two values. A separate route with `formaction` avoids that, and keeps the
  audit insert out of the preview path.
- **Logging previews:** nothing leaves the system, so a preview is the same
  as viewing the case (decided above).

## Risks

- **Preview and download drift apart** if someone later changes one path
  only. Mitigation: the shared `build()` and a test that the preview contains
  every string of the Markdown download.
- **Moving code out of `generate()`** could change an error code or the order
  of checks. Mitigation: the existing `/generate` tests in
  `tests/test_render.py`, `tests/test_industry.py` and
  `tests/test_hardening.py` must pass unchanged.
- **A client name in the page:** the same `protect()` runs, which fails
  closed, so this is no worse than the download.

## Verification

New tests in `tests/test_render.py`:

- `test_preview_shows_the_download_content`: for one approved case with
  research, every value in the Markdown download (title, client, summary,
  each detail, block, bullet and industry statement) appears in the
  `/preview` HTML.
- `test_preview_is_not_logged`: `/preview` adds no `generations` row; the
  download from the preview page adds one.
- `test_preview_enforces_generate_rules`: these give the same status as on
  `/generate`:
  - a case the user cannot see: 404;
  - more than 3 cases: 400;
  - no case and no acknowledgement: 400;
  - a protected client: 409.
- `test_preview_escapes_values`: a case title containing `<script>` is
  escaped in the page.

The full suite passes. Manual check in the demo: open Preview from a case
card, then Download Word from the preview page.
