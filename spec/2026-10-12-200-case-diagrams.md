---
status: approved
issue: 200
intent: intent/2026-10-12-200-case-diagrams.md
---

# Spec: Case studies: diagrams

## Decisions

User, 2026-10-12:

- Diagrams come from **reviewer upload only**. No generation, and no pictures
  taken from the source documents.
- A case can have **several diagrams, each with a caption**.
- **Approving the case confirms the diagrams name no client.** A person
  checks every image, because `protect()` reads text only.
- **The worker, not the web task, decodes the image** (user, 2026-10-12, on
  the spec review). The deployment rule of 2026-10-07 holds: only the worker
  parses untrusted files, and the web task, which holds the session secret,
  never does. The web task checks only the size and the magic bytes, stores
  the raw upload as pending and queues a job. The worker validates and
  re-encodes it, then marks it ready or rejected. Uploads are therefore
  asynchronous: a diagram shows "processing" for a few seconds.

## Design

### 1. What is accepted

- **PNG and JPEG only.**
  - SVG is refused: it can carry script and external references, and Word
    cannot embed it through python-docx.
  - GIF, WebP and HEIC are refused: one way in keeps the checks small.
- **Limits:**
  - at most 5 MB per file (`DIAGRAM_MAX_BYTES`);
  - at most 8,000 px on a side and 16 megapixels;
  - at most 5 diagrams per case;
  - a caption of 1 to 200 characters is required. It is also the alt text,
    so it cannot be empty.
- **Checks in the web task, at upload** (no image library is called):
  1. Size cap. The guard middleware (`app/main.py:138`) gives the diagram
     upload route the diagram cap, next to `/admin/upload`.
  2. Magic bytes: `\x89PNG\r\n\x1a\n` or `\xff\xd8\xff`, like `UPLOAD_MAGIC`
     (`app/main.py:22`).
  3. At most 5 diagrams on the case, counting pending ones.
- **Checks in the worker** (job kind `diagram`, `app/worker.py` `HANDLERS`):
  1. `PIL.Image.open` (Pillow 12.3 is already installed through python-pptx;
     no new dependency). Its `format` must be PNG or JPEG.
  2. Check `size` against the pixel limits **before** `load()`, so a
     decompression bomb is never decoded. `Image.MAX_IMAGE_PIXELS` is set to
     the same limit, so Pillow refuses one too.
  3. `load()` the image, then save it again as PNG (RGB or RGBA).
  - Re-encoding drops EXIF (which can hold a camera owner's name or GPS
    position), PNG text chunks, ICC profiles and anything appended after the
    image data. Only re-encoded bytes are ever served or rendered.
  - Any failure in steps 1–3 marks the diagram **rejected** with a short fixed
    reason ("not a PNG or JPEG", "too large: at most 8,000 px a side and 16
    megapixels", "the image could not be read"). Exception text is never
    stored. A rejection is final and is not retried. A database error is
    raised, so the job queue retries it as usual (`MAX_ATTEMPTS`).
  - The raw upload is deleted (set to null) when the job ends, ready or
    rejected.
- **Caption checks.** The caption goes through `anonymise.blocked()` at
  upload. A caption that names a protected client is refused ("the caption
  names a protected client"), and the client is not named in the message.

### 2. Storage

- **Image bytes go in Postgres, not S3.** New table in `sql/schema.sql`:

  ```sql
  create table if not exists diagrams (
    id bigserial primary key,
    case_id bigint not null references cases(id) on delete cascade,
    raw bytea,                 -- the upload, until the worker has processed it
    png bytea,                 -- the re-encoded image, when ready
    created_by text not null,
    created_at timestamptz not null default now());
  ```

  One row per upload. The row and the job are written in the same
  transaction as the case edit, so a failed edit leaves no orphan and no job.
  S3 would add a second write that cannot roll back with the case.
- **The case refers to its diagrams from `cases.data`.** `ReferenceCase`
  (`app/schema.py:89`) gains:

  ```python
  diagrams: SkipJsonSchema[list[Diagram]] = []
  # Diagram: id, caption, status ("pending" | "ready" | "rejected"), reason, width, height
  ```

  - It is hidden from the LLM schema, like `needs_attention`, so the model
    never sees or fills it.
  - Because it is in `data`, `VERSION` (`md5(c.data::text)`,
    `app/review.py:40`) covers it. A reviewer who approves with a stale page
    gets a 409, so nobody approves diagrams they have not seen.
  - **The worker writes the result into `data`** (status, reason, width,
    height) under `for update` of the case row, so `VERSION` changes when a
    diagram becomes ready. A reviewer who saw "processing" cannot approve the
    finished image without reloading the page. The worker locks only that
    case row. When the entry is gone (removed while processing), it deletes the
    `diagrams` row and stops.
- **Re-extraction keeps them.** Diagrams do not come from the document, so
  the extract upsert (`app/extract.py:163-168`) carries them over:
  `data = excluded.data || jsonb_build_object('diagrams', coalesce(cases.data->'diagrams', '[]'::jsonb))`.
  The case still goes back to review, as today.
- **Removal:** the entry is removed from `data`, and its `diagrams` row is
  deleted in the same transaction.

### 3. Review page

- **Diagrams card:** `templates/review_detail.html` gains it, below the facts.
  - Each diagram: when ready, a thumbnail (`<img>` from the route in 5, with
    `alt` = caption); when pending, "Processing: reload in a moment"; when
    rejected, "Rejected: <reason>". Then its caption in an edit form and a
    Remove button.
  - An upload form: `multipart/form-data`, a file field, a caption field and
    the hidden `v`.
  - No JavaScript.
- **Routes** (`app/review.py`, reviewer role, each takes `v`):
  - `POST /review/{cid}/diagrams` adds one.
  - `POST /review/{cid}/diagrams/{did}/caption` changes a caption.
  - `POST /review/{cid}/diagrams/{did}/remove` removes one.
  - Each calls `load()`, which locks the row, checks `VERSION` and `OPEN`,
    and returns 404 if the reviewer cannot see the case. Then it saves.
  - Then the same statement as `edit` (`app/review.py:223`) re-opens an
    approved case: **any change to diagrams sends an approved case back to
    review** (#143).
- **Approve** refuses with 409 while any diagram is pending ("a diagram is
  still being checked; reload in a moment"). Rejected diagrams are left out of
  outputs and do not block approval. When a case has ready diagrams, the form
  shows the text "I have
  checked every diagram: no client name, logo, person or identifying detail"
  and a required checkbox `diagrams_checked`. `approve()` refuses with 400
  without it. A case with no diagrams is unchanged.
- **Merged cases (#55):**
  - `combine()` does not copy the members' diagrams. The merged case starts
    with none, and the reviewer adds them to it.
  - Re-merge (#190) keeps the merged case's own diagrams: `remerge`
    (`app/review.py:429`) builds a fresh case with `combine()`, so it must copy
    `merged.diagrams` onto it before `save()`.
  - Un-merge leaves them on the rejected merged row.
  - A member's own diagrams stay on the member row, hidden while it is
    merged.

### 4. Outputs

- **Section data:** `section()` (`app/render.py:82`) adds
  `diagrams=[{"id", "caption", "width", "height"}]` for **ready** diagrams
  only. `protect()` already
  walks every string, so captions are anonymised and checked fail-closed.
  The id is a number, so it never matches a name.
- **Bytes:** `generate()` loads the bytes for the ids in the chosen cases
  with one `select id, png from diagrams where id = any(%s) and case_id = any(%s)
  and png is not null`. It passes them to the writers as `images: dict[id,
  bytes]`, a new keyword argument with default `{}`, so the existing
  signatures still work. The web task only copies these bytes into the file;
  it never decodes them. python-pptx reads the image header to size the
  picture, which is safe on bytes the worker has already re-encoded.
- **Word:** docxtpl `InlineImage(tpl, BytesIO(png), width=Mm(160))`, scaled
  down only (never wider than the text area). The bundled `brand/reference.docx`
  gains a loop after the case lists: the image, then the caption as a
  paragraph in the "Caption" style. Marketing's real template (#15) needs the
  same loop; `docs/` says so.
- **PowerPoint:** one slide per diagram, from a new layout **"Diagram"** with
  placeholders Title, Picture and Caption (`DIAGRAM_PLACEHOLDERS`).
  - The picture goes in with `placeholder.insert_picture` and is fitted
    inside the box, keeping its aspect ratio.
  - The title is the case title.
  - The bundled `brand/master.pptx` gains the layout.
  - A master without it raises `ValueError(... has no slide layout named
    'Diagram')`, as the case layout does today. This happens only when a
    chosen case has diagrams, so cases without diagrams still generate.
- **PDF:** no change. It converts the Word or PowerPoint file, and the
  LibreOffice lockdown is unchanged. The images are embedded, not linked.
- **Markdown:** it cannot embed an image without a data URI, which bloats the
  file and many viewers block. Each diagram becomes a line under a
  "Diagrams" heading: `- <caption> (in the Word, PowerPoint and PDF versions)`.
- **Preview (#197):** the preview page shows `<img src=...>` from the route
  below, with `alt` = caption, and the caption under it.

### 5. Serving an image

- **Two routes:**
  - `GET /cases/{cid}/diagrams/{did}.png` (role `user`) applies the same
    restriction as `/generate`: approved, in date, `VISIBLE`.
  - `GET /review/{cid}/diagrams/{did}.png` (reviewer) uses the review page's
    visibility.
  - In both, the id must be a **ready** entry in that case's `data.diagrams`,
    and `diagrams.case_id` must be that case. Otherwise it is 404, so a
    guessed id never opens an image from another case. The raw upload is
    never served.
- **Headers:**
  - `Content-Type: image/png` (fixed, never from the upload);
  - `X-Content-Type-Options: nosniff`;
  - `Content-Security-Policy: default-src 'none'`;
  - `Cache-Control: private, no-store`, so access is re-checked on every
    view;
  - `Content-Disposition: inline`.
- **No model ever sees an image.** The `diagram` job calls no model.
  Extraction reads document text only.
  Research seeds from capabilities, technology and engagement type
  (`app/research.py:129`). Diagrams are not in `search_text`. A test pins
  this.

### 6. Audit

Case edits are not audited row by row today. The audit page shows approvals
(`approved_by`, `approved_at`). Diagrams follow the same rule. `diagrams.created_by`
records who uploaded each image, and the forced re-approval records who
vouched for it. No new audit table.

## Alternatives rejected

- **SVG:** script, external `href` and `<foreignObject>`, plus python-docx
  cannot embed it. Sanitising SVG is a project of its own.
- **Decode in the web task at upload** (the first draft of this spec): the
  result would be synchronous, but Pillow would parse untrusted bytes in the
  task that holds the session secret, against the 2026-10-07 rule. Decided
  against by the user.
- **Content-addressed storage (sha256 key):** saves a duplicate upload, but
  needs reference counting on removal. One row per upload is simpler.
- **Store the upload as is:** keeps EXIF and GPS, and any polyglot payload.
  Re-encoding is a few lines with Pillow.
- **S3 for the bytes:** a second store, a write that cannot roll back with the
  case, and S3 read permissions on the web task for a few MB per case.
  Postgres (RDS, encrypted) is enough at this size. Revisit only if diagrams
  grow past 25 MB per case.
- **A separate table that links diagrams to cases, outside `data`:**
  `VERSION` would not cover it, so a reviewer could approve diagrams added
  after they loaded the page.
- **Copy members' diagrams into a merged case:** members can come from
  different contracts, and their diagrams may overlap or conflict. The
  reviewer chooses for the merged case.
- **A data URI in Markdown:** a large file that many renderers refuse.
- **Automatic image checks (OCR for client names):** OCR is a large new
  dependency, and it misses logos. The decision puts a person on it.

## Risks

- **A person misses a client logo.** It is mitigated by the required checkbox,
  and by any diagram change re-opening approval. It is not eliminated, and the
  user guide says so.
- **Marketing's templates (#15) lack the Word loop or the "Diagram" layout.**
  Word would leave diagrams out silently (a template without the loop
  renders nothing), and PowerPoint fails loudly. A test on the bundled
  templates pins both. `docs/` lists the two additions for marketing.
- **Database size:** 5 diagrams × 5 MB per case worst case, though re-encoded
  diagrams are usually much smaller. Watch the RDS storage alarm (exists).
- **A diagram stays "processing" if the worker is down.** Approval waits
  for it, so nothing unchecked is published. The existing "no running
  worker" alarm covers it.
- **The worker writes `cases.data`**, which edits also write. Both lock the
  case row first (`load()` takes `for update`), so neither overwrites the
  other. The worker takes only that one row lock, so the lock order (source,
  then documents, then cases) is kept.
- **Re-extraction carry-over** changes the extract upsert. If the merge
  expression is wrong, re-extraction drops the diagrams. A test pins it.

## Verification

New tests (names indicative):

- `tests/test_review.py`:
  - `test_upload_diagram_queues_job_and_shows_processing` (raw stored,
    entry pending, one `diagram` job)
  - `test_upload_diagram_refuses_svg_gif_wrong_magic_oversize_and_sixth`
  - `test_approve_refused_while_diagram_pending`
  - `test_upload_diagram_refuses_caption_naming_protected_client`
  - `test_diagram_change_reopens_approved_case`
  - `test_stale_version_cannot_approve_after_diagram_added` (409)
  - `test_approve_with_diagrams_requires_checkbox`
  - `test_remove_diagram_deletes_unreferenced_bytes`
  - `test_merge_starts_without_diagrams_and_remerge_keeps_them`
- `tests/test_diagrams.py` (the worker job, no web task):
  - `test_png_and_jpeg_become_png_without_metadata` (a JPEG with EXIF GPS
    comes back as PNG with no EXIF; raw is null afterwards)
  - `test_pixel_bomb_rejected_before_decode`
  - `test_corrupt_or_wrong_format_rejected_with_fixed_reason`
  - `test_ready_changes_case_version`
  - `test_removed_while_processing_deletes_row`
  - `test_web_task_never_imports_pil_for_upload` (the upload route works with
    `PIL.Image.open` patched to raise)
- `tests/test_extract.py`: `test_reextraction_keeps_diagrams`.
- `tests/test_render.py`:
  - `test_docx_embeds_diagram_with_caption`
  - `test_pptx_diagram_slide_per_diagram`
  - `test_pptx_without_diagram_layout_fails_only_with_diagrams`
  - `test_markdown_lists_diagram_captions`
  - `test_caption_is_anonymised_and_withheld_fail_closed`
- `tests/test_hardening.py`:
  - `test_diagram_route_404_for_case_user_cannot_see_foreign_id_and_pending`
  - `test_diagram_route_headers` (`image/png`, nosniff, CSP, no-store)
- `tests/test_schema.py`: `test_diagrams_hidden_from_llm_schema`.
- Full suite green. A manual check in the demo: upload, approve, then
  download Word, PowerPoint, PDF and Markdown, and look at the preview.
