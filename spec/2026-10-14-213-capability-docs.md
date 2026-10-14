---
status: approved
issue: 213
intent: intent/2026-10-14-213-capability-docs.md
---

# Spec: Presale documents as clearly labelled capability documents

The intent's questions were approved as proposed (2026-10-14):

1. **Unsigned SOWs** become capability documents.
2. **Search.** A "Capability" kind filter, plus a count line under the
   results. Nothing is mixed into reference results.
3. **Outputs.** Capability documents appear in bid packs only, as their
   own "Our capability" section.

## Design

### 1. A third basis: `capability`

`capability` follows the existing `engagement` pattern, which already
suppresses results for contracted scope (`app/render.py:211-216`):

- **Schema.**
  - `ReferenceCase.basis` gains `"capability"` (`app/schema.py:223`).
  - `sql/schema.sql:82`: the `cases_basis_check` constraint gains
    `'capability'`.
- **Triage** (`app/ingest.py` `basis_for`). Whatever is not `delivered`
  or `engagement` now returns `capability` when the document is a
  proposal or deck, or a contract that is not executed. `other` stays
  `None`. The delivered and engagement rules are unchanged.
- **Extraction** (`app/extract.py:205`). A `CAPABILITY_SYSTEM` prompt says
  the document describes an offering: what Synechron proposes or can
  do, never results.
  - `outcomes`, `lessons_learned` and every metric are cleared after
    extraction, as `engagement` already does at `:223`.
  - The write-up is told "describe the offering; never claim it was
    delivered".
  - Callouts are dropped, as for engagement.
- **Commercial strip and anonymisation** apply as today. No prices or
  rates leave.

### 2. Labelled everywhere

The label is `CAPABILITY_NOTE = "Capability document: describes what we
offer, not delivery evidence."`, next to `ENGAGEMENT_NOTE`. It appears on:
- review, with a "Capability" badge
- search rows
- preview
- every output section that includes one
- MCP rows, as `basis: "capability"` plus the note

### 3. Search: never mixed in

- **Default results.** `results()` and `browse()` (`app/search.py:80,258`)
  return only `delivered` and `engagement` unless `kind == "capability"`.
  The kind filter gains "Capability".
- **Count line.** Under the results: "N capability documents match. Show
  them". It uses the same query with `kind=capability`, a count only.
- **Ranking.** Capability documents are never counted in "references
  found" or ranked alongside cases.

### 4. Outputs

- **Bid pack** (docx, pdf). If any selected case is `capability`, it
  renders under a separate "Our capability" heading after "Reference
  cases", with `CAPABILITY_NOTE`. It has no outcomes and no "Results"
  block.
- **Case-study and one-pager formats** refuse a capability selection with
  400 "capability documents can't be case studies". The Markdown preview
  shows the section with its label.

### 5. Turning the skipped documents into drafts

- **The admin action.** On Sources: "Capability drafts from skipped
  documents (N)". It queues one `extract` job with `basis=capability`
  for each live document that has no case and whose `documents.kind` is
  `proposal`, `deck` or `contract`. It uses the same job path as
  re-extraction, and N is shown first.
- **Same review as cases:** the review queue, approval, the 12-month
  re-review, and ACLs from the source.

### 6. MCP

- `search_references` and `list_latest` accept `kind="capability"`, and
  every row carries `basis`.
- `generate_output` follows the rules in §4.
- `get_case` shows the note.

## Alternatives rejected

- **A separate table or app for capability material.** It would duplicate
  review, ACL, anonymisation and output code. A third basis reuses all of
  them.
- **Mixing capability documents into reference results with a badge.**
  The intent forbids it. The bid team must never present an offer as
  delivery.
- **Leaving unsigned SOWs skipped.** Rejected by the user's answer to
  question 1.

## Risks

- **Model wording slips** ("we delivered…"). Mitigated by the prompt, by
  clearing outcomes and metrics, and by the human review. The eval also
  counts "delivered"-style claims in capability write-ups (below).
- **Commercial terms in proposals**, which are rich in rates. The existing
  commercial strip applies, and the eval requires 0 leaks.
- **Ranking regressions** for existing searches. Default results exclude
  the new basis, and a test pins the default result set as unchanged.

## Verification

- **Unit tests:**
  - `basis_for` for proposal, deck, unsigned contract, executed contract
    and `other`
  - capability extraction clears outcomes, metrics, lessons and callouts
  - default search excludes capability documents; `kind=capability`
    returns only them; the count line is right
  - the bid pack puts them under "Our capability" with the note
  - case-study and one-pager formats give 400
  - MCP rows carry `basis`
  - the admin action queues exactly the skipped proposals, decks and
    contracts
- **Eval** on the 11 presale documents, counts only, with local
  `qwen3:14b`, masked names and a private file:
  - how many become capability drafts
  - unsupported flags
  - commercial-term leaks (must be 0)
  - "delivered"-style phrases in capability prose, a simple regex count
    (target 0; any hit is reviewed)
- **Full suite:**
  `docker compose build app && docker compose run --rm app pytest`.
- **Reviews:** Opus, Codex and agy, read-only.
