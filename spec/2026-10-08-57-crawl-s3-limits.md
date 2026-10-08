---
status: approved
issue: 57
intent: intent/2026-10-08-57-crawl-s3-limits.md
---

# Spec: File-share crawler skips unsupported and oversized files

## Design

One change, in `crawl_s3` (`app/crawl.py`), copying the SharePoint crawler's
filter (`crawl_sharepoint`, `app/crawl.py` ~line 170 and ~line 201).

- **Settings, read once per crawl:**
  - `exts`: `config.include_ext` (optional, per source), defaulting to
    `["docx", "pptx", "pdf"]`, normalised to lower case without a dot, as
    SharePoint does.
  - `cap`: `int(os.environ.get("S3_MAX_BYTES", 50 * 1024 * 1024))`, named like
    `SHAREPOINT_MAX_BYTES` and `CONFLUENCE_MAX_BYTES`.
- **In the listing loop**, after `seen.append(key)` and before the cursor
  check and `get_object`:
  - suffix of the key's last segment not in `exts` → `counts["skipped_type"] += 1`, next key;
  - `obj["Size"] > cap` → `counts["skipped_too_large"] += 1`, next key.
- Both counts start at 0 in `counts`, so they appear in `last_counts`. The
  sources page already prints every count key (`app/templates/sources.html`
  line 16), so it needs no change.

What this gives:

- A skipped file is never downloaded, never copied to `originals/` and never
  read into memory, so a large file can't kill the worker and an unsupported
  one isn't fetched again on each crawl.
- A skipped key is still in `seen`, so it is **not** marked deleted. A
  document already ingested that grows past the cap, or is renamed to another
  type, keeps its last good version live (intent question 1, as SharePoint).
- **Uploads go through the same crawler.** The upload endpoint already accepts
  only `.docx`, `.pptx` and `.pdf` up to `UPLOAD_MAX_BYTES` (50 MB by default,
  `app/main.py` lines 19 and 65), so nothing that can be uploaded is newly
  skipped. If an admin raises `UPLOAD_MAX_BYTES` above `S3_MAX_BYTES`, those
  uploads are skipped and counted, not lost silently. Documented in the
  README next to the other caps.
- Legacy `.doc` stays unsupported (intent question 3).

## Alternatives rejected

- **DataSync include filter by extension (intent question 2).** Not possible:
  DataSync include filters accept `*` only as the last character of a
  pattern, and its documentation says `*.txt` is not supported. Filters are
  also case sensitive. An *exclude* list of unwanted types can never be
  complete. Unsupported files therefore still reach the bucket; the crawler
  now ignores them without downloading. The extra S3 storage is the cost. An
  S3 lifecycle rule for the share prefix could limit it later if needed.
- **Streaming the body with a read limit instead of trusting `Size`.** S3's
  listing `Size` is the object's real size, and the check runs before any
  download. A second guard adds code for a case S3 doesn't produce.
- **Recording skipped files as `documents` rows** so they are known. That
  would put unusable files into search and ACL handling. The listing check is
  cheaper than the row and needs no schema change.
- **Withdrawing a document that grows past the cap.** Rejected in the intent:
  the last good version stays, as SharePoint does.

## Risks

- **Existing tests use `.txt` keys** (`tests/test_ingest.py`,
  `test_crawl_cursor_and_deletion`) and assert the exact counts dict. They
  must switch to a supported extension and expect the two new keys, or they
  fail.
- **A source with other types on purpose** (for example `.xlsx`) must set
  `include_ext`. Docling may not read them; that is today's behaviour for
  SharePoint too.
- **Hosts:** the worker on ECS only; no Terraform, schema or migration change.
  Rollback is a revert.

## Verification

- `docker compose build app && docker compose run --rm app pytest` is green.
- New tests in `tests/test_ingest.py`, with moto as today:
  - a `.mp4` and a `.txt` are counted `skipped_type` and never fetched
    (`get_object` not called for them), and no `originals/` object is written;
  - an object over a small `S3_MAX_BYTES` is counted `skipped_too_large` and
    never fetched;
  - a skipped key is not marked deleted, and a previously ingested document
    that is now too large stays live;
  - `include_ext` on the source widens the filter;
  - uppercase `.PDF` is accepted.
- A second crawl of the same bucket downloads nothing for the skipped keys.
