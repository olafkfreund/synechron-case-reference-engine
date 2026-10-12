---
status: approved
issue: 200
spec: spec/2026-10-12-200-case-diagrams.md
---

# Plan: Case studies: diagrams

## Decisions

These are carried over from the approved spec, checked against HEAD `e8644f0`
(main `3c13b8c` plus the artifacts).

- **Source:** reviewer upload only. There is no generation, and nothing is
  taken from the documents. A case has up to 5 diagrams, each with a caption
  of 1 to 200 characters. The caption is also the alt text.
- **Formats:** PNG and JPEG only, up to 5 MB, 8,000 px a side and 16
  megapixels. SVG, GIF, WebP and HEIC are refused.
- **The worker decodes; the web task never does** (user, 2026-10-12):
  - The web task checks only the size cap, the magic bytes and the 5-diagram
    limit. It stores the raw upload in a `diagrams` row, adds a `pending`
    entry to `cases.data.diagrams`, and queues a `diagram` job, all in one
    transaction.
  - The worker runs Pillow and checks the pixel size before `load()`. It
    re-encodes to PNG, which strips EXIF, text chunks, ICC and trailing data.
    It sets the entry to `ready` (with width and height) or `rejected` (with
    a fixed reason), and sets `raw` to null.
  - A rejection is final. Database errors raise and retry.
- **Storage:** a `diagrams` table holds `id`, `case_id`, `raw`, `png`,
  `created_by` and `created_at`. `cases.data.diagrams` holds the list of
  `{id, caption, status, reason, width, height}`. The list is
  `SkipJsonSchema`, so it is hidden from the LLM.
- **`VERSION` covers the diagrams:** it is `md5(data)`, and the worker writes
  its result into `data` under `for update`. A page loaded while a diagram
  was "processing" gets a 409 on approve.
- **Approval:**
  - Any diagram change (add, caption, remove) re-opens an approved case
    (#143).
  - Approve refuses with 409 while a diagram is pending.
  - With ready diagrams, approve needs `diagrams_checked`, or it returns 400.
  - Rejected diagrams don't block approval and are never rendered.
- **Re-extraction keeps the diagrams** (the extract upsert carries them).
  **Re-merge keeps** the merged case's own. A **merge starts with none.**
  **Un-merge** leaves them on the rejected row.
- **Outputs** use ready diagrams only:
  - **Word:** a docxtpl `InlineImage`, scaled down only to 160 mm at most,
    then the caption.
  - **PowerPoint:** one slide per diagram on a new layout "Diagram" (Title,
    Picture, Caption), with the picture fitted and its aspect ratio kept. A
    master without the layout fails only when a chosen case has diagrams.
  - **PDF:** unchanged.
  - **Markdown:** a "Diagrams" heading with
    `- <caption> (in the Word, PowerPoint and PDF versions)`.
  - **Preview (#197):** `<img>` from the user route.
- **Serving:**
  - `GET /review/{cid}/diagrams/{did}.png` (reviewer, `REVIEWABLE`) and
    `GET /cases/{cid}/diagrams/{did}.png` (user, the same restriction as
    `/generate`).
  - The id must be a ready entry of that case, and `diagrams.case_id` must
    match.
  - Headers: `image/png`, nosniff, `Content-Security-Policy: default-src
    'none'`, `Cache-Control: private, no-store`, inline.
  - Raw bytes are never served.
- **Never to a model:** the `diagram` job calls no model, and diagrams are not
  in `search_text`, the research seed or the extraction input.
- **Captions** are checked with `anonymise.blocked()` at upload and on a
  caption edit. A blocked caption is refused with a message that doesn't name
  the client. Outputs run them through `protect()`, as every string is.

## Steps

1. **Check docxtpl `InlineImage` against the bundled template, before any
   code.**
   - In the image, render a copy of `brand/reference.docx` with an extra
     paragraph `{{ img }}`, using `InlineImage(tpl, BytesIO(png), width=Mm(50))`
     and `autoescape=True`, exactly as `to_docx` calls it
     (`app/render.py:133-142`).
   - Confirm `word/media/` holds the image and `document.xml` has a
     `<w:drawing>`.
   - Command:
     `docker compose build -q app && docker compose run --rm app python -c "<the snippet>"`.
   - If autoescape breaks it, record a deviation. The fallback is to render
     a marker paragraph and replace it with `paragraph.add_run().add_picture`
     after `tpl.render`.
   - Traps: none (no repo file is changed).

2. **`app/schema.py:86-111`:** add `class Diagram(_Model)` after `Period`,
   with fields `id: int`, `caption: str`,
   `status: Literal["pending", "ready", "rejected"]`, `reason: str = ""`,
   `width: int = 0` and `height: int = 0`. Add
   `diagrams: SkipJsonSchema[list[Diagram]] = []` to `ReferenceCase` after
   `needs_attention` (`:109`). Don't add it to `quotes()`, `search_text()`
   or the extraction prompt.
   - Verify: new `tests/test_schema.py::test_diagrams_hidden_from_llm_schema`
     (not in `model_json_schema()`, and old data without the key validates
     to `[]`).
   - Traps: `extra="forbid"` means the worker must write exactly these keys.

3. **`sql/schema.sql` (append at the end, after `:187`):** add
   `create table if not exists diagrams (...)` as in the spec, with
   `case_id bigint not null references cases(id) on delete cascade`.
   - Verify: the suite's schema load runs twice (`tests/test_db.py`).
   - Traps: idempotent DDL only, because migrate re-runs it.

4. **New `app/diagrams.py` and `app/worker.py:10-16`:**
   - Constants: `MAX_BYTES = 5 * 1024 * 1024`, `MAX_SIDE = 8000`,
     `MAX_PIXELS = 16_000_000`, `MAX_PER_CASE = 5`, `MAX_CAPTION = 200` and
     `MAGIC = (b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff")`, plus the three fixed
     reasons.
   - `process(diagram_id)`, in one connection:
     - Read `case_id` from `diagrams` without a lock. When the row is gone,
       return.
     - Lock the case row (`select data from cases where id=%s for update`),
       then `select raw from diagrams where id=%s and case_id=%s for update`.
       When the row or `raw` is gone, return.
     - Decode. `Image.MAX_IMAGE_PIXELS = MAX_PIXELS`, `im = Image.open(BytesIO(raw))`,
       check `im.format in ("PNG", "JPEG")` and `im.size` before `im.load()`.
       Convert to RGB or RGBA and `save(buf, "PNG")`.
     - Map any `PIL.UnidentifiedImageError`, `Image.DecompressionBombError`,
       `OSError` or `ValueError` to a rejected reason. Never store the
       exception text.
     - Find the entry by id. When it's missing, delete the `diagrams` row and
       return.
     - Set the status, reason, width and height. Write
       `data = jsonb_set(data, '{diagrams}', %s)`.
     - Set `png` (when ready) and `raw = null`.
   - Add `"diagram": lambda p: diagrams.process(p["diagram_id"])` to
     `HANDLERS`.
   - Verify: new `tests/test_diagrams.py` with the six tests named in the spec
     (call `process` directly, and build the images with Pillow in the test),
     plus `tests/test_worker.py` staying green.
   - Traps:
     - Lock order, for every path (worker and review routes): source, then
       documents, then cases, then diagrams. The worker takes the case row
       before the diagram row, so a reviewer removing a diagram while it is
       processed cannot deadlock.
     - No model call.
     - Don't use `md5(data)` from Python. Let SQL write `data`, so
       `VERSION` changes.

5. **`app/main.py:138` (the guard):** the cap becomes `upload_cap()` for
   `/admin/upload`, `diagrams.MAX_BYTES` when
   `re.fullmatch(r"/review/\d+/diagrams", path)`, and `BODY_CAP` otherwise.
   - Verify: new `tests/test_hardening.py::test_diagram_upload_over_cap_is_413_before_parsing`.
   - Traps: import `app.diagrams` lazily or at module top. It must not import
     PIL at module import time in the web task: keep `from PIL import Image`
     inside `process()`.

6. **`app/review.py`, new routes after `edit` (`:166-225`).** All take `v`
   and call `load()` (which locks the case and checks version and `OPEN`).
   Each ends with the re-open statement copied from `:223-224`.
   - `POST /review/{cid}/diagrams` (`file: UploadFile`, `caption`, `v`):
     - Check the caption: 1 to 200 characters, stripped, and not
       `anonymise.blocked(caption, load_clients(conn))`.
     - Read at most `MAX_BYTES + 1` and check the length.
     - Check the magic bytes, and that there are fewer than 5 entries.
     - `insert into diagrams(case_id, raw, created_by) ... returning id`.
     - Append a `Diagram(status="pending")`, `save()`, and
       `insert into jobs(kind, payload) values ('diagram', jsonb_build_object('diagram_id', %s::bigint))`.
   - `POST /review/{cid}/diagrams/{did}/caption`: the same caption checks,
     then update the entry and `save()`.
   - `POST /review/{cid}/diagrams/{did}/remove`: drop the entry, `save()`,
     and `delete from diagrams where id=%s and case_id=%s`.
   - `GET /review/{cid}/diagrams/{did}.png`: select `png` joined to a case
     that is `REVIEWABLE`. The entry must be ready and have that id. Return
     the bytes with the headers in Decisions.
   - Verify: the `tests/test_review.py` tests named in the spec (upload
     queues a job, the refusals, re-open, a stale version, removal).
   - Traps:
     - Don't `import PIL` here.
     - Errors never echo the upload or the caption.
     - Use `refuse`-style fixed messages, like `/admin/upload`
       (`app/main.py:226-250`).

7. **`app/review.py` `approve` (`:228-258`) and `remerge` (`:429-462`):**
   - In `approve`, after `load()`:
     - any pending entry gives 409 "a diagram is still being checked; reload
       in a moment";
     - any ready entry without the form field
       `diagrams_checked: bool = Form(False)` gives 400.
   - In `remerge`, set `case.diagrams = merged.diagrams` before
     `save(conn, cid, case)` (`:457`).
   - `combine()` is unchanged, so a merge starts empty.
   - Verify:
     - `test_approve_refused_while_diagram_pending`
     - `test_approve_with_diagrams_requires_checkbox`
     - `test_stale_version_cannot_approve_after_diagram_added`
     - `test_merge_starts_without_diagrams_and_remerge_keeps_them`
     - `tests/test_review.py -k "merge or approve"` stays green.
   - Traps: remerge locks the members before the merged case (#144). Don't
     reorder.

8. **`app/review.py` `review_detail` (`:135-163`) and
   `templates/review_detail.html:42-55`:**
   - Pass `diagrams=case.diagrams`.
   - Add a "Diagrams" card after Fields:
     - ready: `<img src="/review/{{ id }}/diagrams/{{ d.id }}.png" alt="{{ d.caption }}" width=... >`;
     - pending: "Processing: reload in a moment";
     - rejected: "Rejected: {{ d.reason }}";
     - a caption form and a Remove form for each, each with `v`;
     - when `reviewable` and fewer than 5, an upload form
       (`enctype="multipart/form-data"`, `accept="image/png,image/jpeg"`).
   - In the approve form (`:53-54`), when any diagram is ready, a required
     checkbox `diagrams_checked` labelled "I have checked every diagram: no
     client name, logo, person or identifying detail".
   - Verify: `tests/test_review.py` page asserts and `tests/test_ui.py`.
   - Traps: no JavaScript, and no `| safe`.

9. **`app/extract.py:163-168`:** in the upsert, replace `data=excluded.data`
   with
   `data=excluded.data || jsonb_build_object('diagrams', coalesce(cases.data->'diagrams', '[]'::jsonb))`.
   - Verify: new `tests/test_extract.py::test_reextraction_keeps_diagrams`,
     and `tests/test_extract.py` green.
   - Traps: the case still goes back to `extracted`, unchanged.

10. **`app/render.py`: `section()` (`:82-104`) and `generate()` (`:352-405`):**
    - `section()` returns `diagrams=[dict(id=d.id, caption=d.caption, width=d.width, height=d.height) for d in case.diagrams if d.status == "ready"]`.
    - In `generate()`, after `protect()`, load
      `{id: png for id, png in conn.execute("select id, png from diagrams where id = any(%s) and case_id = any(%s) and png is not null", ...)}`
      and pass `images=` to `make`.
    - In `OUTPUTS`, give each writer an `images` keyword with default `{}`.
      The lambdas pass it through, and the PDF wrappers do too.
    - Add `GET /cases/{cid}/diagrams/{did}.png` here, using the same `VISIBLE`,
      approved and in-date query as `generate()`, and the same headers.
    - Verify:
      - `tests/test_hardening.py::test_diagram_route_404_for_case_user_cannot_see_foreign_id_and_pending`
      - `tests/test_hardening.py::test_diagram_route_headers`
      - `tests/test_render.py` green.
    - Traps:
      - **#197 is on another branch.** It moves the body of `generate()`
        into a shared `build()` and adds `POST /preview` with
        `templates/output_preview.html`.
      - If #197 has merged first, load the images in `generate()` after
        `build()`. Add `{% for d in c.diagrams %}<figure><img src="/cases/{{ c.id }}/diagrams/{{ d.id }}.png" alt="{{ d.caption }}"><figcaption>{{ d.caption }}</figcaption></figure>{% endfor %}`
        to the preview template; this needs the case id in the section dict.
      - If #197 has not merged, skip the preview and note it in the PR.
      - Rebase on main before this step.

11. **Word: `scripts/make_reference_template.py:28-34` and
    `brand/reference.docx`, plus `to_docx` (`app/render.py:133-142`):**
    - In the template script, inside the case loop after the lists loop
      (before the case `endfor` at `:33`), add:
      ```
      {%p for d in c.diagrams %}
      {{ d.image }}
      caption paragraph "{{ d.caption }}", style "Caption"
      {%p endfor %}
      ```
      Regenerate `brand/reference.docx` with the script, in the image.
    - In `to_docx(sections, industry=None, images={})`: after creating
      `tpl`, rebuild each section's diagrams with
      `image=InlineImage(tpl, BytesIO(images[d["id"]]), width=Mm(min(160, d["width"] * 25.4 / 96)))`,
      and skip any id missing from `images`.
    - Verify: `tests/test_render.py::test_docx_embeds_diagram_with_caption`
      (a `<w:drawing>`, `word/media/*.png`, the caption text) and
      `test_docx_content_and_leaks`.
    - Traps:
      - Use step 1's result.
      - The `Caption` style must exist in the python-docx default template.
        It does, but check.
      - Don't feed `InlineImage` objects through `protect()`: add them after
        it.

12. **PowerPoint: `scripts/make_reference_master.py:66-68` and
    `brand/master.pptx`, plus `to_pptx` (`app/render.py:185-224`):**
    - Add a layout `build(prs.slide_layouts[7], "Diagram", DIAGRAM, [])` with
      Title, Picture and Caption body placeholders.
    - In `to_pptx(..., images={})`, when any section has diagrams:
      - Resolve `_layout(prs, "Diagram", DIAGRAM_PLACEHOLDERS)`. When it's
        `None`, raise
        `ValueError(f"{MASTER.name} has no slide layout named 'Diagram'")`.
      - For each diagram, `_fill` Title and Caption, plus Picture with `[""]`.
      - Then find the Picture placeholder on the new slide by layout idx.
        Read its left, top, width and height, remove it, and
        `slide.shapes.add_picture(BytesIO(png), left, top, width=w, height=h)`
        with w and h fitted, keeping the aspect ratio.
    - Verify:
      - `tests/test_render.py::test_pptx_diagram_slide_per_diagram`
      - `tests/test_render.py::test_pptx_without_diagram_layout_fails_only_with_diagrams`
      - `test_master_layout_has_unique_idx_and_no_slides`
      - `test_pptx_fills_named_placeholders`
    - Traps:
      - `insert_picture` on a picture placeholder crops to fill. That's why
        we use `add_picture` at the box.
      - `_fill` requires every required name filled.
      - Keep `test_master_template_is_marked_draft` passing.

13. **Markdown: `MD` (`app/render.py:283-336`):** after the lists loop, add
    `{% if c.diagrams %}## Diagrams` with
    `- {{ d.caption | md }} (in the Word, PowerPoint and PDF versions)`.
    - Verify:
      - `tests/test_render.py::test_markdown_lists_diagram_captions`
      - `tests/test_render.py::test_caption_is_anonymised_and_withheld_fail_closed`
      - `test_markdown_values_cannot_create_structure`
    - Traps: the caption is escaped with `md`.

14. **Docs: `docs/user-guide/reviewers.md`:**
    - Add a step on adding diagrams: PNG or JPEG; "processing", then ready
      or rejected; the checkbox; and that any change re-opens approval.
    - Add a note in the template docs (`docs/`, where the Word and
      PowerPoint templates are described for marketing, #15) on the
      `c.diagrams` loop and the "Diagram" layout.
    - Verify: `tests/test_user_guide.py`.
    - Traps: labels quoted in the guide must match the templates exactly.

## Tests

- After each step, the step's tests, with
  `docker compose build -q app && docker compose run --rm app pytest -q -p no:cacheprovider <node ids>`.
- At the end, the full suite:
  `docker compose build -q app && docker compose run --rm app timeout 900 pytest -q -p no:cacheprovider`.
  Expect all green, with the count of main plus the new tests.
- The manual check in a worktree compose project, never `refsdemo` or
  `refsdev`:
  1. Upload a PNG and a JPEG.
  2. Reload until both are ready.
  3. Approve with the checkbox.
  4. Download Word, PowerPoint, PDF and Markdown.
  5. Open the preview, if #197 is in.

## Rollback

- Revert the merge commit. The `diagrams` table and the `data.diagrams` keys
  stay, but they are harmless:
  - the old code ignores an unknown table;
  - old `ReferenceCase` has `extra="forbid"`, so **old code fails to load a
    case with `data.diagrams`**.
- So the rollback also runs
  `update cases set data = data - 'diagrams' where data ? 'diagrams'` before
  the old image starts.
- Then `drop table diagrams` if wanted.
- Queued `diagram` jobs fail as an "unknown job kind" on the old worker,
  which is harmless.
