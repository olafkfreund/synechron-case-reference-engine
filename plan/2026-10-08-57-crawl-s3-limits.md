---
status: approved
issue: 57
spec: spec/2026-10-08-57-crawl-s3-limits.md
---

# Plan: File-share crawler skips unsupported and oversized files

## Approved decisions (self-contained)

- `crawl_s3` filters each listed object by type and size **before**
  downloading, exactly like `crawl_sharepoint` (`app/crawl.py` lines 170–171
  and 201–206).
- Types: `config.include_ext` (optional per source), default
  `["docx", "pptx", "pdf"]`, compared lower case without the dot, using the
  suffix of the key's last segment.
- Size: `obj["Size"] > int(os.environ.get("S3_MAX_BYTES", 50 * 1024 * 1024))`.
- Skips are counted as `skipped_type` and `skipped_too_large`, both starting
  at 0, and stored in `last_counts`. The sources page already prints every
  count key, so the template is unchanged.
- A skipped key stays in `seen`: it is never marked deleted, and an
  already-ingested document that grows past the cap or changes type keeps its
  last good version.
- No DataSync change: its include filters cannot match by extension.
- Legacy `.doc` stays unsupported. Uploads are unaffected (same types, same
  50 MB default).
- The session model implements this itself: 2 steps, 3 files, below the
  coder hand-off threshold.

## Steps

1. **Filter in `crawl_s3`.**
   - `app/crawl.py` line 37: add `"skipped_type": 0, "skipped_too_large": 0`
     to `counts`. Just above it, read `exts` and `cap` as in
     `crawl_sharepoint` line 170–171, with `config.get("include_ext", ...)`
     and `S3_MAX_BYTES`.
   - After `seen.append(key)` (line 45) and before the `LastModified` and
     cursor lines: the type check, then the size check on `obj["Size"]`,
     each counting and `continue`-ing.
   - `tests/test_ingest.py`:
     - `test_crawl_cursor_and_deletion` (line 83): switch the keys from `.txt`
       to `.docx` (the `env` fixture stubs `to_markdown`, so the bodies can
       stay as they are) and expect the two new keys at 0 in the counts dict.
     - New `test_crawl_skips_type_and_size`, with `S3_MAX_BYTES` set to 10
       by monkeypatch. Objects: `a.docx` (small), `b.PDF` (small, uppercase),
       `c.mp4`, `d.txt`, `e.pdf` (over the cap). Patch `ingest` in
       `app.crawl` to record which keys it gets. Assert:
       - only `a.docx` and `b.PDF` are ingested;
       - `skipped_type == 2` and `skipped_too_large == 1`;
       - no `originals/` object exists for the skipped ones (bucket `orig`
         holds 2 objects);
       - a second crawl still ingests none of the skipped keys.
     - New `test_crawl_include_ext_and_oversized_existing_doc_stays_live`:
       - a source with `include_ext: ["txt"]` ingests a `.txt`;
       - a `.docx` ingested at a small size and then overwritten above the
         cap is counted `skipped_too_large`, and its document is still live
         (`deleted_at is null`).

   → verify by `docker compose build app && docker compose run --rm app pytest tests/test_ingest.py`.
   Traps:
   - Compose has no bind mount: always build before testing.
   - The skip checks must come after `seen.append`, or skipped keys are
     marked deleted.
   - `test_auth.py` and `test_enqueue.py` only enqueue `crawl_s3`, but run
     the full suite in step 2 anyway.
   *Done, with deviations:* four more S3 crawl tests in `tests/test_ingest.py`
   used `.txt` keys (cursor, bad document, ACL/reappearing key, empty
   listing) and moved to `.docx` as well. The skip test runs the real
   `ingest` and records downloads by wrapping the moto client's
   `get_object`, instead of patching `ingest`; it checks `documents` rows
   rather than counting `originals/` objects.
   The skip test is named `test_crawl_skips_type_and_size_without_downloading`;
   the `include_ext` test uses `["txt", "docx"]` because the same source also
   ingests a `.docx`. Ordering against `seen.append` is proven by the
   oversized-existing-doc test (review mutation check).
2. **Document the limits.** In `README.md`, add a short `## Limits` section
   before `## Local development with Ollama`, listing:
   - `S3_MAX_BYTES`, `SHAREPOINT_MAX_BYTES` and `CONFLUENCE_MAX_BYTES`
     (50 MB by default), applied to crawled files before download;
   - `UPLOAD_MAX_BYTES` (50 MB by default) for uploads, which go through the
     S3 crawler, so keep it at or below `S3_MAX_BYTES`;
   - per source, `include_ext` widens the types for S3 and SharePoint; the
     default is `docx`, `pptx`, `pdf`; legacy `.doc` is not supported.

   → verify by `docker compose run --rm app pytest` (full suite green).
   *Deviation from the spec:* the spec said "next to the other caps", but the
   README documents no caps yet, so this adds the section.

## Tests

- `docker compose build app && docker compose run --rm app pytest` is green.
- The new tests prove: skipped files are never fetched or copied, skips are
  counted, skipped keys are not deleted, `include_ext` widens the filter, and
  uppercase extensions are accepted.

## Rollback

- Revert the PR. There is no schema, Terraform or data change.
- Old `last_counts` rows lack the two new keys; the template prints whatever
  keys exist, so nothing breaks either way.
