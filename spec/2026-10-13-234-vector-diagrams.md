---
status: approved
issue: 234
intent: intent/2026-10-13-234-vector-diagrams.md
---

# Spec: Vector diagrams in documents become diagram candidates

## Design

### Which formats: EMF and WMF only (decision)

- **SVG is skipped.** Office never stores an SVG alone: next to every
  `svgBlip` it keeps a PNG fallback in the same `media/` folder, and phase C
  already reads that PNG. SVG is also the one format that can point outside
  the file (`xlink:href`, `<image href="http…">`), and the conversion runs
  where the task can still reach the VPC and the metadata endpoint. Skipping
  SVG costs nothing and removes that risk.
- **EMF and WMF are what Office saves for vector drawings** (Visio, SmartArt,
  pasted shapes). They have no fallback, so they are the real gap.

### Shared converter: `app/office.py` (new, small)

The `to_pdf` subprocess code moves out of `app/render.py` into
`office.convert(files, fmt, timeout) -> dict[name, bytes]`, unchanged in
behaviour:

- its own temp dir and LibreOffice profile with the `LOCKDOWN` registry
  (macros off, untrusted links blocked)
- a minimal environment (`PATH`, `HOME` only)
- `start_new_session=True`, with `os.killpg(SIGKILL)` on timeout
- the shared `SLOTS = BoundedSemaphore(2)` (today's `PDF_SLOTS`), so PDF
  export and image conversion together never run more than two soffice
  processes

`to_pdf` becomes a three-line wrapper over it, so `PdfError` and its messages
stay as they are. The module is needed because `render.py` imports
`diagrams.py`, so `diagrams` can't import `render`.

**One soffice run per document:** `soffice --convert-to png --outdir out
a.emf b.wmf …` converts every vector file of a document in one start (about
2–3 s), not one start per image.

### In `document_images_from` (app/diagrams.py)

- **Selection.**
  - Vector entries are picked from the same `media/` prefix by magic bytes,
    never the file name: EMF starts with `01 00 00 00` and has ` EMF` at
    offset 40; WMF starts with `D7 CD C6 9A` (placeable) or `01 00 09 00` /
    `02 00 09 00`.
  - Each must be between `VEC_MIN_BYTES = 2_000` and `MAX_BYTES` (5 MB)
    declared, and is read bounded as today.
  - The 10 largest are taken (`MAX_VECTORS = 10`).
- **Conversion.**
  - One `office.convert(..., "png", timeout=VEC_TIMEOUT = 30)` call, with
    `SLOTS` acquired using a 5 s wait.
  - If the slot is busy, the call times out or it fails, the vectors are
    skipped and the raster candidates are kept. The result is logged with
    the exception class only.
- **After conversion, exactly as raster.** Each PNG out is read bounded
  (`MAX_BYTES`) and goes through `_decode()` (re-encoded, metadata dropped,
  bombs refused) and the `MIN_SIDE = 400` filter, so icons and logos fall
  out. Hints come from `_hints()` keyed by the vector's media name, which
  already works for any media name.
- **Merge.** Raster and vector candidates are sorted together by pixel area,
  then cut to `MAX_FOUND = 10`.
- **Order.** Raster decoding runs first. Conversion runs only while fewer than
  `MAX_FOUND` candidates are kept, so a document that is full of PNGs never
  starts soffice.

### Where it runs

It runs in `store_images()` (ingest), unchanged: after the document commit,
outside any transaction, and failure-safe. The worker image already has
LibreOffice.

**Dockerfile:** add `libreoffice-draw`. EMF and WMF import, and PNG export of
a graphic, are Draw filters, and the image today has only core, writer and
impress. The plan's first step checks this in the container
(`soffice --convert-to png x.emf`). If impress turns out to be enough, the
package isn't added.

### Backfill for older documents (decision, 2026-10-13)

- `documents` gets `images_version int not null default 1`, added with
  `add column if not exists`, so existing rows read as 1. `store_images()`
  sets it to `IMAGES_VERSION = 2` after a run, whatever it found.
- On a same-bytes re-ingest, `store_images()` runs once for a document whose
  case is live and whose `images_version < IMAGES_VERSION`. Today the rule is
  "no candidate rows".
- So a document that already has PNG or JPEG candidates (trial line 3, the
  user's case 17) also gets its vector ones on the next full crawl. Used or
  dismissed images stay as they are: re-running replaces only rows that are
  not used.
- A later change to image handling bumps `IMAGES_VERSION`.

## Alternatives rejected

- **Convert SVG too.** It has a PNG fallback already, and it can reference
  external URLs and files.
- **A Python EMF/WMF renderer (Pillow's WmfImagePlugin).** On Linux it only
  reads the header; it doesn't render.
- **Inkscape, or ImageMagick with a delegate.** Both are new large
  dependencies with their own attack surface, and LibreOffice is already
  there.
- **One soffice start per image.** That would take up to 10 × 2–3 s per
  document.
- **Converting the whole docx or pptx to PDF and rasterising pages.** That
  produces pages, not diagrams, and costs far more.

## Risks

- **LibreOffice parsing hostile EMF/WMF.** The parser is native code and has
  had CVEs. The process gets an isolated profile, macros off, a minimal
  environment with no secrets, a timeout with a process-group kill, and at
  most two at a time. The output is only ever read through `_decode()`.
  Remaining risk: code execution inside the worker container, the same
  exposure as today's PDF export of user-supplied templates.
- **Time.** A slow document adds up to 30 s to ingest, outside transactions.
  A busy slot skips the vectors rather than waiting.
- **Contention with PDF downloads.** They share the two slots. Conversion
  waits only 5 s, so a busy PDF export makes ingest skip vectors rather than
  delay users.
- **Too many candidates.** The output is still capped at 10 per document,
  largest first, and nothing reaches an output without the reviewer's Use.

## Verification

- **Unit tests** (`tests/test_diagrams.py`), with `office.convert`
  monkeypatched to return a 500×500 PNG per input name:
  - EMF and WMF entries detected by magic bytes and converted in a single
    call; a `.emf` name with PNG bytes isn't treated as a vector.
  - SVG is never passed in.
  - Entries under 2 KB or over 5 MB are skipped before they're read.
  - At most 10 vectors are converted.
  - The merged result is sorted by area and capped at 10. With 10 raster
    candidates already found, `convert` isn't called.
  - A converter error or timeout keeps the raster candidates and logs the
    class only. A busy slot skips the vectors.
  - The hint comes from the vector's alt text and is anonymised.
- **Real conversion** (skipped when `soffice` is missing; it runs in the
  container): generate an EMF in the test by converting a tiny generated SVG
  with `soffice --convert-to emf`, put it in a docx's `word/media/`, then
  check `document_images_from` returns one PNG candidate of at least
  400 px.
- **`office.convert`:** `to_pdf` tests stay green unchanged, and a timeout
  test kills the process group (with a fake slow command).
- **Trial eval** (counts only): `images=` per document on the 11 trial
  documents, before and after.
- **Full suite:** `docker compose build app && docker compose run --rm app pytest`.
