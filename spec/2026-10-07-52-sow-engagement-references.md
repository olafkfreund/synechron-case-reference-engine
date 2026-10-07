---
status: approved
issue: 52
intent: intent/2026-10-07-52-sow-engagement-references.md
---

# Spec: Signed SOWs and change orders as engagement references

Depends on #53: it uses the flat extraction format, the model profiles and the
data policy. Implement it after #53.

## Design

### 1. Triage (`app/ingest.py`)

- **`Triage` gains a kind and a flag:**
  - `kind`: `case | contract | proposal | deck | other`. `contract` covers
    statements of work, change orders, amendments and call-offs.
  - `executed: bool`: true only when the document shows a **completed
    signature block**, meaning signatory names *and* dates filled in for both
    parties. Templates, blank signature lines and anything marked "draft"
    count as not executed.
  - Kept: `describes_delivered_work`.
- **Prompt:** the triage prompt defines `contract` and `executed` as above and
  says "draft" in the text means not executed. Triage keeps reading the first
  ~8,000 characters, plus the **last** ~4,000, because signature blocks sit at
  the end.
- **Executed-contracts sources (admin override):**
  `sources.config.executed_contracts: true` (set on `/admin/sources`) means an
  admin vouches that every contract in that source is signed. `executed` is
  then treated as true for kind `contract`.
  *Default answer to "how do we know it's signed": AI detection, with the admin
  flag overriding. Change at spec review.*
- **Routing:**

| Triage | Becomes |
| ------ | ------- |
| `case`, or `proposal`/`deck` with delivered work | delivered reference (today) |
| `contract` and executed (by AI or source flag) | **engagement reference** |
| `contract` not executed, `proposal`, `deck`, `other` | not extracted; searchable as a related document (today) |

### 2. Engagement extraction (`app/extract.py`)

- **A separate prompt for contracts:** extract client, scope (as challenge and
  solution), capabilities, technology, team size, duration and period. It must
  **not** extract outcomes, targets, service levels, prices, rates, payment
  terms, or names of individual people.
- **Code-side enforcement**, which doesn't rely on the prompt:
  - the record's **basis** is set from triage (see 3), never from the model;
  - for basis `engagement`, **outcomes are always dropped**, so contracted
    targets can never appear as results;
  - any item whose value or quote contains a currency amount, a rate (`/day`,
    `per hour`, `p.d.`), or payment wording is dropped and counted in
    `needs_attention` ("2 commercial items removed");
  - `client_mention` must be an organisation, as today. Person names aren't
    extracted for any field: the prompt says so, and the reviewer sees the
    result.
- All existing protections apply unchanged: quote check, summary numbers,
  anonymisation, review, access groups, and re-review after 12 months.

### 3. The case record (`app/schema.py`, `sql/schema.sql`)

- `ReferenceCase.basis: SkipJsonSchema[Literal["delivered", "engagement"]] = "delivered"`,
  set by extraction from triage and hidden from the LLM (as with `unsourced`).
- An additive column `cases.basis text not null default 'delivered'` (with a
  check constraint), kept in step with the record on extract and approve, for
  search and display.

### 4. Search, review and outputs

- **Search** (`app/search.py`): engagement references rank **alongside**
  delivered cases by relevance; on equal rank, delivered cases come first.
  - Every result shows a badge: "Delivered case" or "Engagement (contracted
    scope)".
  - The AI pick prompt is told the basis of each candidate, and must not
    describe engagement work as delivered results. The existing "no new
    numbers" check still applies.
  - *Default answer to "rank below or alongside": alongside, with a label.
    Change at spec review.*
- **Review** (`app/review.py`): shows the basis badge, plus triage's reason
  for an engagement ("executed contract" or "source marked executed").
- **Outputs** (all formats, `app/render.py`):
  - An engagement reference carries the fixed line "Engagement reference:
    contracted scope; no outcomes are claimed."
  - It has no Outcomes section.
  - On the slide, the Outcomes box holds that line instead.
- **Several documents for one engagement:** v1 keeps **one reference per
  document**. The NatWest Phase 0 and Phase 1 SOWs are two references, and the
  reviewer may reject a near-duplicate. Merging related contracts is a
  follow-up issue. *Default answer; change at spec review.*

## Alternatives rejected

- **Treating every SOW as an engagement:** drafts and unsigned versions are
  common (the sample folder has "draft v.2" and "v0.4"), and an unsigned SOW is
  a proposal.
- **Letting the model decide the basis, or keep outcomes for contracts:** a
  contracted target presented as a result is the worst failure this tool can
  have. So the basis comes from triage and outcomes are dropped in code.
- **Merging documents into one engagement now:** it needs reliable matching
  across documents (client plus programme plus period). That's a later step,
  once real volumes are known.
- **Ranking engagements strictly below delivered cases:** that hides relevant
  engagements whenever any delivered case exists. A label plus a tie-break is
  enough.

## Risks

- **AI misreads a signature block** (a draft treated as executed). The
  reviewer sees "executed contract" with the triage reason. An admin can
  instead mark a source executed, and leave drafts out of it.
- **The commercial filter misses an unusual rate format.** The quote check and
  review still apply, and the reviewer approves every field.
- **Search mixes the two kinds.** Mitigated by the badge on every result and
  the fixed output line.

## Verification

- **`pytest`:**
  - routing for every triage combination, including the source flag;
  - outcomes always dropped for engagements, even when the model returns them;
  - the commercial filter on a set of rate and price strings;
  - basis persisted, and searchable with the tie-break;
  - the badge on search and review;
  - the fixed line and missing Outcomes section in docx, pptx and md;
  - a "no new numbers" test for an engagement pick.
- **Evaluation on the 12 presale documents** with the local model (#53):
  - every executed SOW or change order becomes an engagement with no outcomes
    and no prices, rates or person names;
  - drafts and proposals are not extracted;
  - the results are listed in `plan/` as evidence.
