---
status: approved
issue: 234
spec: spec/2026-10-13-234-vector-diagrams.md
---

# Plan: Vector diagrams in documents become diagram candidates

Branch `feat/234-vector-diagrams`, worktree `../refs-engine-234`. One PR.

## Approved decisions (from the spec)

- **Formats:** only EMF and WMF are converted. SVG is never converted, because
  Office keeps a PNG fallback next to it and SVG can reference external URLs.
- **Detection:** vector entries come from the same `media/` prefix and are
  picked by magic bytes, never the file name:
  - EMF: `01 00 00 00` with ` EMF` at offset 40
  - WMF: `D7 CD C6 9A`, `01 00 09 00` or `02 00 09 00`
- **Caps:**
  - declared size between `VEC_MIN_BYTES = 2_000` and `MAX_BYTES` (5 MB),
    read bounded
  - at most `MAX_VECTORS = 10`, largest first
- **Converter:** the `to_pdf` subprocess code moves to a new `app/office.py`
  as `convert(files, fmt, timeout) -> dict[name, bytes]`, keeping its
  behaviour:
  - its own temp dir and profile, with the `LOCKDOWN` registry
  - a minimal env (`PATH`, `HOME`)
  - `start_new_session`, with `killpg(SIGKILL)` on timeout
  - the shared `SLOTS = BoundedSemaphore(2)`, which was `PDF_SLOTS`

  `to_pdf` stays a thin wrapper, so `PdfError` and its messages are
  unchanged.
- **One run per document:** a single soffice run per document with
  `VEC_TIMEOUT = 30` s and a 5 s slot wait. A busy slot, a timeout or a
  failure skips the vectors, keeps the raster candidates, and logs the class
  only.
- **After conversion:** each PNG is read bounded, goes through `_decode()`
  and the `MIN_SIDE = 400` filter, and gets its hint from `_hints()` by media
  name.
- **Merge:** raster and vector candidates are merged, sorted by pixel area
  and cut to `MAX_FOUND = 10`. Conversion runs only while fewer than 10
  raster candidates are kept.
- **Where it runs:** inside `store_images()`, after the document commit,
  outside any transaction, and failure-safe.
- **Dockerfile:** add `libreoffice-draw`, unless step 1 shows impress is
  enough.
- **Backfill:** `documents.images_version int not null default 1`;
  `IMAGES_VERSION = 2`. A same-bytes re-ingest of a live-case document with
  `images_version < 2` runs `store_images()` once, then the version is set.
  Used or dismissed images stay as they are.

## Decided here (an addition to the spec, for review)

**On the backfill path, add candidates and never replace them.** Today
`store_images()` deletes every row and inserts again. That gives every image
a new id, so an image the reviewer already used (`Diagram.image_id`) or
dismissed (`dismissed_images`) would be offered again. So:

- `store_images(..., add_only=False)`. On the backfill path, `add_only=True`
  deletes nothing, and inserts only images whose PNG isn't already stored for
  that document.
- The match is on `md5(png)`. It is exact because `_decode()` is
  deterministic: the same source entry gives the same PNG bytes.
- Existing rows keep their ids, so used and dismissed images stay that way,
  and only the newly converted vectors appear.
- The new-version path is unchanged. It deletes in the ingest transaction and
  replaces, as #212 accepted.

## Steps

1. **Check the container.**
   - `docker compose build app`, then in `docker compose run --rm app`:
     create a tiny SVG, run `soffice --headless --convert-to emf` on it, then
     `soffice --headless --convert-to png` on the EMF and a WMF.
   - Record whether the PNG is produced with the current packages.
   - Only if it isn't: add `libreoffice-draw` to the Dockerfile's apt line
     (`Dockerfile:3`) and repeat.
   - → verify: a PNG file with `\x89PNG` magic is produced in the container.
   - Traps: run it from this worktree only, one compose job at a time.
     Record the result as a plan deviation if `libreoffice-draw` isn't
     needed.

2. **`app/office.py` (new): move the converter.**
   - Move `SOFFICE`, `LOCKDOWN`, the profile setup, the `Popen`/`killpg`
     body, `PDF_SLOTS` (renamed `SLOTS`) and `PDF_WAIT` from
     `app/render.py:353-400`.
   - Generalise the body to `convert(files: dict[str, bytes], fmt: str,
     timeout: int, wait: float) -> dict[str, bytes]`. It writes the inputs to
     its temp dir, runs `soffice --convert-to <fmt> --outdir out …`, reads
     each output bounded to `MAX_BYTES`, and raises `RuntimeError` on a busy
     slot, a timeout or a non-zero exit.
   - `render.to_pdf` calls `office.convert({f"in{suffix}": data}, "pdf",
     PDF_TIMEOUT, PDF_WAIT)` and maps the errors to the same `PdfError`
     messages.
   - → verify: `docker compose run --rm app pytest tests/test_render.py -k
     pdf` is green, unchanged.
   - Traps:
     - keep the minimal env and `start_new_session`
     - `office` must not import `render` or `diagrams`, to avoid a cycle
     - grep for `PDF_SLOTS`/`SOFFICE` uses in the tests and repoint them

3. **`app/diagrams.py`: vector candidates.**
   - Add `VEC_MIN_BYTES`, `MAX_VECTORS`, `VEC_TIMEOUT = 30`, `VEC_WAIT = 5`
     and `_is_vector(head: bytes) -> bool` (magic bytes only).
   - In `document_images_from` (`:167-200`), after the raster loop and only
     if `len(found) < MAX_FOUND`:
     - pick vector entries from the same prefix by declared size (bounded
       read), with `_is_vector` on the first 48 bytes
     - take the 10 largest and call `office.convert` once
     - on any exception, print the class only and skip
     - send each output through `_decode` and `MIN_SIDE`, keyed back to its
       media name for `_hints()`
   - Merge with the raster list, sort by `w*h` descending, and cut to
     `MAX_FOUND` before the hint and caption step.
   - → verify: `pytest tests/test_diagrams.py` is green.
   - Traps:
     - never pass `.svg` entries
     - the output file names from soffice are `<stem>.png`; map them back by
       stem, so two inputs with the same stem in different folders can't
       collide (prefix the inputs with an index)
     - keep the existing per-entry `try` and the `MAX_XML_TOTAL` hint budget

4. **`sql/schema.sql` and `app/ingest.py`: the backfill.**
   - `alter table documents add column if not exists images_version int not
     null default 1;`
   - `store_images(doc_id, data, name, wanted, add_only=False)`:
     - When `add_only` is set, skip the delete, and insert each image only if
       `not exists (select 1 from document_images where document_id=%s and
       md5(png)=md5(%s))`.
     - In every case, finish with `update documents set images_version=%s
       where id=%s` (`IMAGES_VERSION = 2`), still inside the `try`.
   - In `ingest()` (`:93-102`), the same-bytes check becomes: a live case
     exists `and images_version < IMAGES_VERSION`. Call
     `store_images(..., True, add_only=True)`.
   - The new-version path (`:135`, delete in the transaction) and its later
     `store_images` call keep replacing, and also set the version.
   - → verify: `pytest tests/test_ingest.py` is green.
   - Traps:
     - The `missing` rule changes meaning. Update the phase C backfill tests
       that assumed "no rows". A document whose case has no usable image
       still bumps the version, so it isn't re-read every crawl, which
       retires the old `shortcut:` comment.
     - Keep `store_images` failure-safe. If it fails, the version isn't
       bumped, so it's tried again next crawl.

5. **Tests.**
   - `tests/test_diagrams.py`, with `office.convert` monkeypatched to return
     a 500×500 PNG per input:
     - EMF and WMF are found by magic bytes in one call; a `.emf` name with
       PNG bytes isn't a vector; `.svg` is never passed
     - entries of 2 KB or less, or over 5 MB, are skipped before reading
     - at most 10 vectors
     - the merged list is sorted by area and capped at 10, and with 10
       raster images `convert` isn't called
     - a converter error or timeout keeps the raster candidates and prints
       the class only
     - the vector hint comes from the alt text and is anonymised
   - **Real conversion** (`pytest.mark.skipif(not shutil.which("soffice"))`):
     generate an EMF in the test from a tiny SVG with soffice, put it in a
     docx's `word/media/`, and assert one PNG candidate of at least 400 px.
   - **`office.convert` timeout:** a fake slow `SOFFICE` (a shell script
     that sleeps) is killed, the error is raised, and no child is left.
   - **`tests/test_ingest.py`, backfill:**
     - A document at `images_version` 1 with one used and one dismissed
       raster candidate gets its vector candidate on a same-bytes ingest.
     - The used and dismissed rows keep their ids, and no duplicate raster
       row appears.
     - The version becomes 2, and a second same-bytes ingest calls nothing.
   - → verify: the full suite is green: `docker compose build app && docker
     compose run --rm app pytest`.

6. **Eval (counts only) and the README.**
   - Run `scripts/eval_extraction.py` on the 11 trial documents, which
     already prints `images=`. Record the before (phase C) and after counts
     per document line, counts only. Use the local model, as before.
   - README: one line in the diagrams section saying vector EMF/WMF diagrams
     are converted, and SVG relies on Office's PNG fallback.
   - → verify: the eval shows `images=` per document; the README line is
     present.
   - Traps: never print file names, text or hints.

## Deviations

- Step 1: no `libreoffice-draw` is needed. The installed core, writer and
  impress packages convert EMF and WMF to PNG. Converted PNGs are a whole
  A4 page, so step 3 crops them to their content before the size filter.
- Step 2: `office.convert(..., limit=None)`. `to_pdf` passes no limit, so
  PDFs over 5 MB still work. Vector conversion passes `MAX_BYTES`.
- No-network rule: the LibreOffice lockdown blocks linked content. There's
  no network namespace, because unsharing needs privileges the container
  doesn't have, and production egress is already restricted. This is
  accepted and noted.

## Tests

```bash
docker compose build app && docker compose run --rm app pytest
```

The full suite should pass. Expect `images=` to rise on the trial documents
that hold EMF or WMF drawings.

## Rollback

Revert the branch. `documents.images_version` stays as a harmless unused
column, or `alter table documents drop column images_version`. Candidate rows
already stored stay valid PNGs.
