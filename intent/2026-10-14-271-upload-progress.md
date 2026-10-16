---
status: approved
issue: 271
author: olafkfreund
---

# Intent: Upload status that says what is happening

## Problem

After an upload, the "Your uploads" list (`/upload`) and the MCP
`get_upload_status` tool show **"queued"** for the whole processing
time:
1. the file waiting for the worker
2. the worker converting it
3. the model deciding what kind of document it is
4. the model writing the case

That takes from about one minute to several minutes per file on the local
model, and longer when files are queued behind each other.

On 2026-10-14 the user uploaded two documents, saw "queued" and an empty
review queue, and asked whether this should happen instantly. One file
was in fact being written by the model at that moment.

The status derivation (`app/ingest.py:149-158`) folds every one of those
stages into `queued`. The page doesn't refresh itself, and it gives no
idea of how long to wait.

## Proposed outcome

- **Distinct stages, in plain language**, each with a one-line
  explanation:

  | Status | Shown as | Explanation |
  |---|---|---|
  | `queued` | Waiting | "Waiting in line: N files ahead of yours." |
  | `reading` | Reading the document | The worker is converting the file. |
  | `writing` | Writing the case | The model is drafting the case. "This usually takes a few minutes." |
  | `in_review` | In review | "A reviewer will check it before it appears in search." |
  | `indexed` | Stored, not a case study | Stored but not a case study, with the reason category. |
  | `approved`, `rejected`, `failed` | unchanged wording | each with a short explanation |

  The review queue gets the same idea: "N uploads are still being
  processed", so an empty queue doesn't look like nothing arrived.
- **Auto-refresh.** The upload page refreshes itself while anything is
  still in progress, then stops.
- **A rough estimate.** "about N minutes", from the recent average
  extraction time. A fixed "a few minutes" is used when there's no
  history.
- **MCP.** `get_upload_status` and `list_my_uploads` return the new codes
  plus a `detail` sentence. The old codes keep their meaning, and
  `reading` and `writing` are new values. Tool descriptions list them.

## Affected users and systems

- Everyone who uploads (portal and MCP), and reviewers (the queue line).
- Code:
  - `app/ingest.py` (status derivation, queue position, estimate)
  - `app/main.py` and `upload.html` (labels, explanations, refresh)
  - `review_list.html` (the "still processing" line)
  - `app/mcp_server.py` (codes and detail)
  - `docs/mcp.md`
  - the tests

## Constraints

- **Owner-only, as today.** Queue position counts jobs, never names or
  shows other users' files.
- **No new tables.** Stages are derived from `jobs` (status, kind,
  `created_at` and `updated_at`), which is the existing pattern.
- **Light refresh.** The page refresh is plain HTML (a meta refresh only
  while something is pending), with no polling script and no new
  endpoint.
- **MCP compatibility.** Existing MCP codes keep their meaning, so
  clients that only know the old set still work: `reading` and `writing`
  are both still "not done".

## Open questions

1. **Estimate source.** Proposed: the median `updated_at - created_at` of
   the last 20 finished `extract` jobs, rounded to whole minutes and shown
   as a range ("about 3–5 minutes"). Alternative: a fixed text only.
2. **The review queue's "still processing" line.** Proposed: it counts
   uploads and crawled documents still in triage or extraction, as one
   number. Reviewers see the total and nothing about who uploaded what.
