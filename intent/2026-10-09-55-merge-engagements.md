---
status: draft
issue: 55
author: olafkfreund
---

# Intent: Merge related contracts into one engagement reference

Follows #52 (`spec/2026-10-07-52-sow-engagement-references.md`, "Several
documents for one engagement": v1 keeps one reference per document, and merging
is a follow-up).

## Problem

A client engagement usually has one SOW plus several change orders and
amendments. In the presale sample, NatWest has a Phase 0 and a Phase 1 SOW, and
Deutsche Bank has an SOW and an amendment. Since #52, each executed contract
becomes its own engagement reference, so one engagement shows up as several:

- **Each case belongs to one document.** `sql/schema.sql:48-51` gives `cases`
  a `document_id ... not null unique`, and `app/extract.py:133-138` upserts
  `on conflict (document_id)`. A case cannot hold more than one document.
- **Each reference shows part of the story.** The SOW has the original scope.
  A change order has the extension, the extra team or the new period. Search
  (`app/search.py:71-73`) ranks these partial references side by side. A bid
  writer has to notice they are the same engagement and put them together by
  hand.
- **The reviewer approves the same engagement several times.** The review
  queue (`app/review.py:92-107`) lists one row per document. #52 suggests
  rejecting near-duplicates, but rejecting one throws away its scope and its
  quotes.
- **Outputs repeat the engagement.** `app/render.py:351-390` builds one section
  per selected case, so choosing both NatWest references gives two partial
  sections about one engagement.

## Proposed outcome

- A reviewer can merge related engagement references into **one engagement
  reference**: same client, overlapping scope or period.
- The merged reference keeps every source document and every field quote
  against the document it came from. The review page lists all member
  documents (today it shows one: `app/review.py:114-127`,
  `app/templates/review_detail.html:4`).
- The merged reference goes through the same quote check, human approval,
  anonymisation and 12-month re-review as any other case. It shows up once in
  the review queue, in search and in outputs. Each member no longer appears as
  a separate reference.
- It keeps basis `engagement`, with the fixed "contracted scope" line and no
  Outcomes (`app/render.py:93-98`).
- Users who could not see every member document never see the merged
  reference, or anything in it.

## Affected users and systems

- **Reviewers:** the review queue and detail page (`app/review.py`). **Bid
  team:** search and downloads.
- **The case record:** `sql/schema.sql` (`cases.document_id` unique, line 50),
  `app/schema.py` (quotes are per field with no document, lines 51-67).
- **Extraction:** `app/extract.py` (`extract()` and its upsert at lines
  126-138), and the per-document data class at line 128.
- **Ingest and crawl:** they reopen or retire a case by its one document
  (`app/ingest.py:99-102`), and soft-delete or re-ACL documents
  (`app/crawl.py:70-82`, `196-198`).
- **Search:** `app/search.py:49-75`. **Outputs:** `app/render.py:81-101`,
  `351-390`.
- **The ACL checks:** `ACL` at `app/review.py:23`, shared by search and
  render. It joins one document per case.

## Constraints

- **Need-to-know never widens.** Today `d.acl_groups && groups` is an
  any-group overlap on one document (`app/review.py:23`). A merged reference
  may only be shown, reviewed, searched or rendered for a user who can see
  **every** member document. Each member must also still be live
  (`deleted_at is null`). A merge must never let one document's readers see
  another document's content.
- **Approval is per content.** A merge produces new content, so the merged
  reference needs its own approval. A member's earlier approval never carries
  over.
- **Confidential documents are never sent to third-party models.** The rule is
  per source data class (`README.md:90-95`; `app/extract.py:128`). If the
  members come from sources with different data classes, any LLM step must use
  the strictest one.
- Every #52 rule still holds: basis comes from triage, never the model;
  outcomes are dropped for engagements; the commercial filter applies (no
  prices, rates, payment terms or person names); drafts never count as
  engagements.
- Test data must be public or made up. No real client documents or names in
  `tests/`.
- No new dependencies unless unavoidable. No JavaScript (server-rendered forms
  only, as in `app/templates/`).
- Tests run with `docker compose build app && docker compose run --rm app pytest`.
- `sql/schema.sql` changes stay additive and idempotent. Existing
  one-document cases keep working unchanged.

## Open questions

1. **Who merges?**
   - (a) A reviewer picks the references and merges them by hand.
   - (b) The system suggests candidates (same client, overlapping period) and
     a reviewer confirms.
   - (c) Automatic merging.
   *Lean: (a) for v1. Matching needs real volumes (#52 spec, "Alternatives
   rejected"), and (c) breaks the human-approval rule.*
2. **What does "related" mean?**
   - (a) The same registry client (`cases.client_id`), with nothing else
     checked.
   - (b) The same client, plus an overlapping or adjacent period or a shared
     programme name.
   - (c) Whatever the reviewer decides.
   *Lean: (a) as the hard rule (no merge across clients), and the reviewer
   judges scope. The period is shown, not enforced.*
3. **Member documents with different access groups.**
   - (a) Refuse the merge unless every member has the same `acl_groups`.
   - (b) Allow it. The merged reference is visible only to users who can see
     every member (the intersection), and the reviewer is warned that it
     reaches fewer people.
   - (c) Union of the groups. **Not acceptable:** it widens need-to-know.
   *Lean: (b), with the merging reviewer required to see every member. If
   nobody can see all of them, (a) applies in effect: the merge is refused.*
4. **Approval of the merged reference.**
   - (a) It starts unapproved (`extracted`) and needs a fresh approval. The
     members leave search but are kept.
   - (b) It inherits approval when every member was already approved.
   *Lean: (a). The combined text is new content.*
5. **How is the merged record built?**
   - (a) Re-extract from the members' texts together (an LLM call, strictest
     data class).
   - (b) Combine the members' already-checked fields, with each quote tagged
     with its document. No LLM call.
   *Lean: (b). There is no model and no data-class risk, and quotes stay
   sourced. The summary is re-checked or left for the reviewer.*
6. **Un-merge.**
   - (a) Supported. Members return as separate references, back to
     `extracted`.
   - (b) Not supported. Reject the merged reference and re-extract.
   *Lean: (a). Keep the members, so un-merging is cheap and loses nothing.*
7. **A member document changes or is deleted.**
   - (a) A change reopens the merged reference for review (as
     `app/ingest.py:99-102` does for one document today). A delete or withdraw
     (`deleted_at`) hides the merged reference until a reviewer removes that
     member or un-merges.
   - (b) Drop the changed or deleted member automatically, and keep the rest
     approved.
   *Lean: (a). Never keep approved content from a document that has changed or
   gone. A member's ACL change applies right away through the intersection
   rule (question 3).*
