---
status: approved
issue: 197
spec: spec/2026-10-12-197-output-preview.md
---

# Plan: Preview a case output before downloading it

## Decisions carried over from the approved spec

- **What the preview shows:** content only. It is one HTML page built from
  the same `sections` and `industry` dicts as the downloads, not a picture of
  the Word or PDF file.
- **One builder:** the preview and `/generate` share a single function,
  `build()`, so their content cannot differ.
- **Not logged:** a preview inserts no `generations` row. Only a download is
  logged, as today.
- **New route:** `POST /preview`, role `user`. It takes the same form fields
  as `/generate`. A `format` field is accepted and ignored.
- **Errors:** the same HTTP codes and messages as `/generate`.
- **Entry points:** a "Preview" button with `formaction="/preview"`, added to
  the case card form, the "Download all" form and the research-result form.
  No JavaScript.
- **Preview page:** a download form posting to `/generate`, with the same
  hidden fields and the five format buttons.
- **Escaping:** autoescaped Jinja, with no `| safe`. URLs are shown as text,
  not links.

## Steps

1. **`app/render.py:352-405`: extract `build()`.**
   - Add `def build(conn, case_ids, research_id, industry_context_ack, user)`.
     It returns `(ids, sections, industry, anonymised)`.
   - Move into it, unchanged and in the same order:
     - the dedupe;
     - the 3-case limit;
     - the no-case / research / ack checks;
     - the research lookup;
     - the `VISIBLE` query and the 404;
     - the `section()` loop;
     - `protect()` (a `Withheld` raises 409);
     - `industry_section()`;
     - the "no usable public statements" 400.
   - The format check sits inside `build()`, after the input checks (dedupe,
     limit, no-case/research/ack) and before any query, as on main. `build()`
     takes `fmt=None`; `generate()` passes `format`, `/preview` passes nothing.
     **Deviation from the first draft** (which put the check first in
     `generate()`): a bad request now opens a DB connection before its 400.
   - `generate()` then opens `db.connect()`, calls `build()`, renders, and
     inserts the audit row inside the same `with`, as today.

   Verify: `tests/test_render.py`, `tests/test_industry.py` and
   `tests/test_hardening.py` pass unchanged.

   Traps:
   - The audit insert must stay after `make()` ("a broken template must not
     log a generation").
   - Keep `research_id if industry else None` and
     `industry_context_ack and not ids`.

2. **`app/render.py`: add `POST /preview`.**
   - Parameters: `request: Request`, `case_ids: list[int] = Form([])`,
     `research_id: int | None = Form(None)`,
     `industry_context_ack: bool = Form(False)`, and
     `user: User = Depends(require("user"))`.
   - Inside `with db.connect() as conn:` call `build()`, then return
     `page(request, "output_preview.html", user, cases=sections,
     industry=industry, ids=ids, research_id=research_id,
     ack=industry_context_ack)`.
   - Import `page` from `app.review`. `VISIBLE` is already imported from
     there. Import `Request` from `fastapi`.
   - Insert nothing.

   Verify: step 6 tests.

   Traps: the CSRF origin check applies to every POST, as for `/generate`.
   It is covered by the test in step 6.

3. **`app/templates/output_preview.html` (new).**
   - Extend `base.html`, using the `narrow` and `section-card` classes as in
     `research_preview.html`.
   - Heading: "Preview".
   - The note: "This is the content of the download; the layout comes from
     the Word and PowerPoint templates."
   - For each `c` in `cases`, in an `article.section-card`:
     - `h2` with `c.title` and the client in `.muted`;
     - `c.note` in a `.note` if set;
     - `p` with `c.summary`;
     - `ul` with `<b>{{ x.label }}:</b> {{ x.value }}` per detail;
     - per block, an `h3` heading and a `p` with the text;
     - per list, an `h3` heading and a `ul` of bullets.
   - If `industry`, a section-card with:
     - `h2` heading and the disclaimer in `em`;
     - a `ul` with
       `{{ i.phrase }}: “{{ i.quote }}” — {{ i.publisher }}, {{ i.url }}, retrieved {{ i.date }}`;
     - the note.
   - A form posting to `/generate`:
     - hidden `case_ids` per id, `research_id` if set, and
       `industry_context_ack` value `on` if `ack`;
     - the buttons `Download Word` (docx), `Download (PowerPoint)`,
       `Download (PDF)`, `Download (PDF slides)` and `Download (Markdown)`.
   - A "Back" link: `<a class="btn" href="javascript:history.back()">` is
     not allowed (no JS). Use `href="/"` labelled "Back to search".

   Verify: step 6 tests.

   Traps: no `| safe`; the URL is shown as text, not as an `<a>`.

4. **`app/templates/search.html:24-26` and `:33-36`: entry points.**
   - Before `Download Word` in the case card form, add
     `<button class="btn" formaction="/preview" aria-label="Preview {{ p.title }}">Preview</button>`.
   - In the "Download all" form, add `<button class="btn" formaction="/preview">Preview all</button>`
     before its Word button.
   - **`app/templates/research_view.html:24-29`:** next to `Download`, add
     `<button class="btn" formaction="/preview">Preview</button>`.

   Verify: `tests/test_industry.py` (the form-content test near `:163`) and
   the step 6 tests.

   Traps: a button without `name` sends no `format`, and `/preview` ignores
   it anyway. The research form's `<select name="format">` is still posted,
   and `/preview` ignores it.

5. **`docs/user-guide/bid-team.md:61-73` and `:103-115`: docs.**
   - Under "Download a case", add a step before 1: "Press **Preview** to
     see exactly what the download will contain. The preview page has the
     same download buttons." Mention **Preview all** in step 3.
   - Under "Download with industry context", add: "Press **Preview** to
     check it first."

   Verify: `tests/test_user_guide.py`. Every bold label must exist in a
   template.

   Traps: none.

6. **Tests.**
   - In `tests/test_render.py`, add a helper
     `prev(ids, groups=R, **kw)` that posts to `/preview`. Then:
     - `test_preview_shows_the_download_content`: build `full()` through
       `approved(...)`. Every non-empty line of the Markdown download,
       stripped of Markdown punctuation, must have its values in the
       preview HTML. At least check the title, the client, the summary,
       each detail value, each block text and each bullet.
       `html.unescape` the page first.
     - `test_preview_is_not_logged`: the count of `generations` is
       unchanged after `/preview`, and grows by 1 after `/generate`.
     - `test_preview_enforces_generate_rules`: the same set as
       `test_visibility_and_validation`, minus the format check:
       - acl `g-other`, out of date, unknown id, and not approved each give
         404;
       - 5 distinct cases give 400;
       - a POST without origin gives 403.
     - `test_preview_escapes_values`: a title `<script>x</script>` appears
       escaped (`&lt;script&gt;`), and never raw.
     - `test_preview_withholds_a_protected_client`: reuse the setup of
       `test_protected_name_replaced_or_withheld`. `/preview` returns 409
       where `/generate` does.
   - In `tests/test_industry.py`,
     `test_preview_includes_industry_and_keeps_the_ack`:
     - with `rows`, research plus a case: the preview shows a statement's
       quote and publisher, and contains
       `name="research_id" value="{rid}"`;
     - with no case and no ack: 400;
     - with ack: 200 and `name="industry_context_ack"` in the page.

   Verify: run the new node ids with `check.sh`, then the full suite.

## Tests

- Full suite: all pass. Main is at 683 passed. Expect about 689.
- New node ids, all passing:
  - `tests/test_render.py::test_preview_shows_the_download_content`
  - `tests/test_render.py::test_preview_is_not_logged`
  - `tests/test_render.py::test_preview_enforces_generate_rules`
  - `tests/test_render.py::test_preview_escapes_values`
  - `tests/test_render.py::test_preview_withholds_a_protected_client`
  - `tests/test_industry.py::test_preview_includes_industry_and_keeps_the_ack`
- Manual check in the demo: open Preview from a case card, then use
  Download Word on the preview page. The file's content matches the
  preview.

## Rollback

Revert the merge commit. There are no schema or data changes, and `build()`
is internal.
