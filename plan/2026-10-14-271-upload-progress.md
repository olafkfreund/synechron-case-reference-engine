---
status: approved
issue: 271
spec: spec/2026-10-14-271-upload-progress.md
---

# Plan: Upload status that says what is happening

Worktree `../refs-engine-271`, branch `feat/271-upload-progress`. One PR.

## Approved decisions (from the spec)

1. **Stages**, derived from `jobs`, `documents` and `cases` only:
   - `failed`
   - `queued`, with N = the queued jobs with a lower id
   - `reading`: `ingest_upload` running
   - `writing`: `extract` running for a document that has no case yet
   - `in_review`
   - `approved`
   - `rejected`
   - `indexed`

   Each has a label and a one-line detail; the wording is the spec's
   table.
2. **Estimate.** The 25th and 75th percentile minutes of `updated_at -
   created_at` over the last 20 `done` extract jobs ("about A–B
   minutes", at least 1). With fewer than 5 jobs: "usually a few
   minutes". One query per render.
3. **Upload page.**
   - A label badge (in-progress rows get a pulsing dot) and the detail as
     `field-help`.
   - While anything is pending: the line "This page updates every 15
     seconds…" and `app/static/upload.js`, which reloads every 15 seconds
     unless a file is chosen.
   - No script when nothing is pending.
   - The upload notice is reworded.
4. **Review queue.** "N documents are still being processed…", counting
   documents visible to the reviewer (`VISIBLE`) that have a queued or
   running `ingest_upload` or `extract` job. Hidden at 0, with no names.
5. **MCP.** `UploadStatusOut` gains `detail`, the codes add `reading` and
   `writing`, and the tool descriptions list every code.

## Steps

1. **`app/ingest.py:142-173`.**
   - The status query also selects the `ingest_upload` and latest
     `extract` job ids and statuses.
   - `stage(...) -> (code, label, detail)`, with `queue_ahead(conn,
     job_id)` and `extract_estimate(conn)`.
   - `upload_status` and `my_uploads` return code, label and detail;
     callers are updated.
   - → verify: `tests/test_uploads.py`
     - every stage from seeded rows
     - the position count
     - the estimate with fewer than 5 jobs and with 20
     - owner-only, unchanged
2. **The upload page.**
   - `app/main.py:352-358`: pass the label, the detail and `pending`.
   - `app/templates/upload.html`.
   - New `app/static/upload.js`, loaded in `{% block head %}` with `defer`
     only when `pending`, as `review.js` does.
   - `portal.css`: the pulsing dot. It uses tokens only and respects
     `prefers-reduced-motion`.
   - → verify:
     - `tests/test_uploads_portal.py`: the labels and details shown; the
       script referenced only while pending; the no-JS line
     - a node stub test that the script doesn't reload when a file is
       chosen
     - `tests/test_ui.py`: the colour lint and the forms baseline
3. **The review-queue line.**
   - Files: `app/review.py:172` and `review_list.html`.
   - → verify: the count includes only visible documents, the line is
     hidden at 0, and no names appear.
4. **MCP.**
   - Files: `app/mcp_server.py:258-266` and `:814-830`, plus
     `docs/mcp.md`.
   - → verify: `tests/test_mcp_upload.py` covers the new codes and
     `detail`, and the schemas validate.
5. **Full suite, screenshots, reviews and PR.** The session takes the
   screenshots (light and dark), runs the Opus, Codex and agy reviews,
   and opens the PR:
   `docker compose build app && docker compose run --rm app pytest`.

## Tests

As listed per step. Every new check is negative-controlled once.

## Rollback

Revert the merge commit. Nothing is stored; the stages are computed.

## Deviations

None yet.
- **Step 1.**
  - **MCP code first.** The `UploadStatusOut.detail` field and the new
    codes landed here, not in step 4, because the tools call
    `upload_status`. Step 4 keeps the docs and the MCP tests.
  - **Deleted job row.** It reads "Waiting, 0 ahead".
- **Steps 2 and 3.**
  - **Refresh.** `upload.js` reloads every 15 seconds unless a file is
    chosen. The pulsing dot respects `prefers-reduced-motion`, and a
    `<noscript>` line says "Reload to see progress". The node stub test is
    skipped when the image has no node; it was run on the host.
  - **Review-queue count.** `VISIBLE` applies to cases, and a document in
    processing usually has no case yet. The count therefore uses document
    ACLs (`documents.acl_groups && groups`, not deleted) for `extract`
    jobs, and the upload source's `acl_groups` for `ingest_upload` jobs.
    It is a count only, with no names.
- **Step 4.** `docs/mcp.md` lists every stage and the `detail` field, and
  the MCP tests cover the queued and reading details. The pending-only
  script check was negative-controlled. Full suite: 1501 passed, 1
  skipped (the node stub test, run on the host).
- **Review fixes** (Opus, Codex and agy; full suite 1503 passed, 4
  skipped as node stubs run on the host):
  - **Stage order.** `doc is None` is checked first, so a vanished
    ingest job can't hide an existing document.
  - **Estimate.** `jobs.started_at` is set by the worker claim with
    `coalesce`, so retries keep the first start. The estimate uses
    `updated_at - started_at`.
  - **One query.** Queue positions come from a single sorted list with
    `bisect`, and that and the estimate are cached per render.
  - **No double count.** An upload is not counted twice in the review
    queue during the handoff to extraction.
  - **Reload.** It goes to `location.pathname`, so an old notice doesn't
    repeat, and it is skipped while focus is in a control.
  - **Wording.** A failed extraction reads "Upload it again or ask an
    admin." A stale reading job reads "This is taking longer than usual."
- **After #213 merged.** The status query returns the case basis, reading
  the parent's for a merged case. A capability case says it will be
  shown as a capability document (in review) or that it is listed under
  Capability (approved). `indexed` reads "Stored, but not a case study or
  capability document." The badge sits after the file name. Full suite:
  1567 passed, 4 skipped (node stubs).
