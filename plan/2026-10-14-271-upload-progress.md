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
