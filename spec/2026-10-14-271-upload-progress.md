---
status: draft
issue: 271
intent: intent/2026-10-14-271-upload-progress.md
---

# Spec: Upload status that says what is happening

The intent's questions were approved as proposed (2026-10-14):

1. **Estimate:** the median of the last 20 finished extractions, shown as
   a range.
2. **Review-queue line:** one count of documents still in processing,
   with no names and no uploader.

## Design

### 1. Stages, derived from `jobs` only (`app/ingest.py:142-173`)

`status_code` becomes `stage(...) -> (code, detail_args)`. The query gains
the current job's id and status, for the `ingest_upload` job and then the
latest `extract` job of the document.

| Condition (first that holds) | Code | Label | Detail |
|---|---|---|---|
| `ingest_upload` failed, or done with no document | `failed` | Couldn't process | "The file could not be read or converted. Try again, or ask an admin." |
| `ingest_upload` queued | `queued` | Waiting | "Waiting in line: N ahead of yours." |
| `ingest_upload` running | `reading` | Reading the document | "Converting the file. Usually under a minute." |
| document with no case and `extract` queued | `queued` | Waiting | "Waiting in line: N ahead of yours." |
| document with no case and `extract` running | `writing` | Writing the case | "The model is drafting the case: about A–B minutes." |
| document with no case and `extract` failed | `failed` | Couldn't process | "Writing the case failed. A reviewer can retry it." |
| case `extracted` (or a merged parent) | `in_review` | In review | "A reviewer will check it before it appears in search." |
| case `approved` | `approved` | Approved | "Visible to staff in search." |
| case `rejected` | `rejected` | Not used | "A reviewer decided not to use it." |
| otherwise (document, no case, extract done) | `indexed` | Stored, not a case study | "Stored, but it doesn't describe a delivered project." Capability documents (#213) change this later. |

- **Queue position.** N = `count(*) from jobs where status = 'queued'
  and id < <this job id>`. The worker takes jobs in id order
  (`app/worker.py:28`), so this is the number ahead. It is a count only;
  other users' files are never named.
- **Estimate.** A and B are the 25th and 75th percentiles of `updated_at
  - created_at`, in minutes, over the last 20 `extract` jobs with status
  `done`. They are rounded, with a minimum of 1. With fewer than 5 jobs,
  the text is "usually a few minutes". It is computed once per page
  render, in one query.
- **MCP.** `UploadStatusOut` gains `detail` (the sentence above). `status`
  takes the new codes `reading` and `writing`. The other codes keep their
  meaning. The tool descriptions list every code.

### 2. Upload page (`app/templates/upload.html`, `app/main.py:352`)

- **Each row** shows the label as a badge (the in-progress stages use a
  neutral badge with a small pulsing dot) and the detail as `field-help`
  text under the file name.
- **Refresh.** While any row is `queued`, `reading` or `writing`, the
  page shows a muted line: "This page updates every 15 seconds while
  files are processing." It also loads `app/static/upload.js`, which
  reloads the page every 15 seconds **unless a file has been chosen** in
  the form, so a reload never discards the user's pick.
  - With nothing pending, the script isn't loaded at all.
  - Without JavaScript, the line says "Reload to see progress".
  - The script is static, with no inline code, so the CSP is unchanged
    (the `review.js` pattern).
- **After an upload.** The notice reads "File received. It is waiting to
  be processed; this page updates by itself."

### 3. Review queue (`app/review.py:172`, `review_list.html`)

- **The line.** "N documents are still being processed and will appear
  here when ready." It shows only when N > 0, under the stat tiles.
- **What N counts:** distinct documents with a queued or running
  `ingest_upload` (counted by its upload) or `extract` job, limited to the
  documents the reviewer can see (the `VISIBLE` ACL). It never names
  anyone.

## Alternatives rejected

- **A progress table or columns on `uploads`.** The jobs already carry
  the truth, and duplicating it would drift.
- **Client-side polling and a status endpoint.** A meta refresh does the
  job with no JavaScript and no new route.
- **A meta refresh.** It can't be paused once a file is chosen, so it
  would discard the user's pick.
- **A per-document live progress percentage.** The model call gives no
  progress, so any percentage would be invented.

## Risks

- **Rough estimate.** It is shown as a range and labelled "about".
- **A reload could lose a chosen file.** Covered by the script's
  file-chosen check (tested with a stub). Without JavaScript nothing
  reloads by itself.

## Verification

- **Tests:**
  - every stage, from seeded `jobs`, `documents` and `cases`
  - the queue position count
  - the estimate with fewer than 5 jobs and with 20
  - owner-only, unchanged
  - the MCP codes and `detail`
  - `upload.js` is referenced only while something is pending
  - a stub test that the script doesn't reload when a file is chosen
  - the review-queue line counts only visible documents and is hidden at 0
- **Screenshots:** the upload page with "Writing the case" and "Waiting"
  rows, and the review queue line, in light and dark.
- **Full suite:**
  `docker compose build app && docker compose run --rm app pytest`.
- **Reviews:** Opus, Codex and agy, read-only.
