---
status: approved
issue: 52
spec: spec/2026-10-07-52-sow-engagement-references.md
---

# Plan: Signed SOWs and change orders as engagement references

**Depends on #53.** It uses #53's flat `Extraction`/`assemble()`, the
`data_class` parameter and the evaluation script. Rebase this branch on main
after #53 merges, then start at step 1.

## Approved decisions (self-contained)

- **Triage:** `kind` is `case | contract | proposal | deck | other`; `contract`
  means an SOW, change order, amendment or call-off.
  - `executed: bool` is true only for a completed signature block: names *and*
    dates for both parties. Templates, blank lines and "draft" are not
    executed.
  - Triage reads the first 8,000 characters plus the last 4,000, because
    signatures sit at the end.
- **Admin override:** `sources.config.executed_contracts: true` treats every
  contract in that source as executed.
- **Routing:**
  - `case`, or `proposal`/`deck` with delivered work → basis `delivered`
    (today's behaviour).
  - Executed `contract` → basis `engagement`.
  - Anything else is not extracted (it stays searchable as a related
    document).
- **Engagement extraction:**
  - A contract prompt: scope as challenge and solution, capabilities,
    technology, team, duration and period. No outcomes, targets or SLAs; no
    prices, rates or payment terms; no person names.
  - Code enforces, whatever the model returns:
    - basis comes from triage only;
    - outcomes are always dropped for engagements;
    - items with a currency amount, rate (`/day`, `per hour`, `p.d.`) or
      payment wording are dropped and counted in `needs_attention`.
  - All existing protections are unchanged.
- **Record:** `ReferenceCase.basis` is a `SkipJsonSchema[Literal["delivered","engagement"]]`
  defaulting to `"delivered"`, plus `ReferenceCase.basis_reason` (hidden from
  the LLM; `"executed contract"` or `"source marked executed"`). The additive
  column `cases.basis` (with a check constraint) is kept in step with it.
- **Search:** engagements rank alongside delivered cases, by relevance with
  delivered first on a tie. A badge on every result reads "Delivered case" or
  "Engagement (contracted scope)". The AI pick is told each candidate's basis
  and must not present engagements as delivered results.
- **Review:** the basis badge and the reason.
- **Outputs (all formats):** engagements carry the fixed line "Engagement
  reference: contracted scope; no outcomes are claimed." and no Outcomes
  section. On the slide, the Outcomes box holds that line instead.
- **One reference per document** (no merging in v1); merging becomes a
  follow-up issue.

## Steps

1. **Schema and record.**
   - In `sql/schema.sql`, after the `alter table` block: `alter table cases add column if not exists basis text not null default 'delivered'`,
     plus an idempotent check constraint (`delivered`|`engagement`).
     *Done: placed after the `cases` indexes rather than the `alter table`
     block; it still runs after `cases` is created.*
   - In `app/schema.py`: `ReferenceCase.basis` and `basis_reason` as above.
     Neither goes in `search_text` or `quotes()`.

   → verify by `pytest tests/test_db.py tests/test_schema.py` (the column and
   default exist; `llm_schema()` has no `basis`).
2. **Triage and routing.** In `app/ingest.py` (`Triage` line 25,
   `wants_extraction` line 44, the triage call line 64):
   - add `contract` to `kind` and add `executed: bool`;
   - triage input = `text[:8000] + "\n…\n" + text[-4000:]` when the text is
     longer than 12,000 characters;
   - rewrite `TRIAGE_SYSTEM` to define `contract` and `executed`, saying
     "draft" means not executed;
   - replace `wants_extraction` with `basis_for(t, source_config) -> str | None`;
   - enqueue `extract` with payload `{document_id, basis, basis_reason}`.

   → verify by `pytest tests/test_ingest.py`, extended: the routing table for
   every combination; the source flag; tail characters included in the triage
   input.
   Traps:
   - Re-ingest of a changed document: the routing retires or re-opens cases
     exactly as today (`rejected` when there's no basis).
   - The worker's `extract` handler must pass the payload through
     (`app/worker.py` HANDLERS).
   *Done, with deviations:* `sql/schema.sql` also widens `documents_kind_check`
   to allow `contract` (an insert fails without it); `extract()` takes
   `basis`/`basis_reason` now so the worker cannot crash before step 3;
   `ingest` derives `basis_reason` (`executed contract` if triage said
   executed, else `source marked executed`).
3. **Engagement extraction.** In `app/extract.py`:
   - `extract(document_id, basis="delivered", basis_reason="")` picks the
     contract prompt when `basis == "engagement"`;
   - after `assemble()` and `check()`, for engagements: clear `outcomes`, then
     run the commercial filter over every item's value and quote, removing
     matches and adding a count note;
   - set `case.basis` and `case.basis_reason`; upsert `cases.basis`.

   → verify by `pytest tests/test_extract.py`, extended:
   - outcomes dropped even when returned;
   - the commercial filter on a table of strings (`£1,200/day`, `USD 450 per
     hour`, `payment within 30 days`, `€2.5m fixed price`; and negatives such
     as `30 servers`, `phase 2`);
   - basis stored; delivered extraction unchanged.
   Traps: the filter must not touch `duration_months` or `team_size` digits.
   *Done, with deviations:* the filter also blanks a matching summary and
   single-value text field (a price cannot leak there); `build()` takes
   `basis`/`basis_reason` with defaults so the eval script still runs.
   "Payment wording" means payment *terms* (`payment terms/within/schedule`,
   `payable`, `invoice`), and rates are per day/hour only: bare "payments" is
   a banking capability and "/month" is usually a volume. Currency amounts
   still catch any price.
4. **Executed-contracts flag.** In `app/sources.py` (create line 34, update
   line 57) and `sources.html`: an "All contracts here are executed" checkbox,
   stored as `config.executed_contracts`.

   → verify by `pytest tests/test_sources.py` (saved, and shown).
5. **Search.**
   - In `app/search.py`: add `c.basis` to the select; order by `rank desc,
     (c.basis = 'delivered') desc, c.id` (line 72); the badge in
     `search.html`.
   - In `pick()` (line 83): candidates are listed with `basis: engagement` or
     `basis: delivered`. The SYSTEM prompt adds: "Engagement cases are
     contracted scope: never describe them as delivered results or outcomes."
     The "no new numbers" check is unchanged.

   → verify by `pytest tests/test_search.py`, extended: the tie-break order;
   the badge rendered; the basis shown in the candidate listing.
6. **Review and outputs.**
   - `app/review.py` (`review_detail` line 111): the badge plus `basis_reason`.
   - `app/render.py`: `section()` (line 80) carries `basis`; for engagements,
     drop the Outcomes list and add a fixed `note` line.
     - docx: the note under the client line (template context `c.note`;
       update `scripts/make_reference_template.py` and regenerate
       `brand/reference.docx` in the container, mode 644).
     - md: the note line.
     - pptx: Outcomes box = `[note]` (line 192).
     - pdf: through docx/pptx.

   → verify by `pytest tests/test_review.py tests/test_render.py`, extended
   (the note present and outcomes absent in docx, pptx and md for an
   engagement; delivered unchanged). Visual check: render one engagement docx
   and pptx to PNG and look.
   Traps: the note goes through `protect()` like every other string.
7. **Follow-up issue.** Open "Merge related contracts into one engagement
   reference" (type:follow-up, area:review), linked under epic #7.

   → verify the issue exists.

## Tests

- `docker compose run --rm app pytest` is green.
  Trap: compose has no bind mount; run `docker compose build app` first.
- Evaluation on the 12 presale documents with `qwen3:14b` (#53's script),
  local only. Record per document: kind, executed, basis, and outcomes count
  (must be 0 for engagements). Pass when:
  - drafts ("draft v.2", "v0.4") are not executed;
  - every executed SOW or change order becomes an engagement;
  - no prices, rates or person names are in any engagement field (spot-check
    the review page in local compose).

## Rollback

- Revert the PR. `cases.basis` is additive; existing cases stay `delivered`.
- To disable engagements without reverting, `basis_for` returns `None` for
  `contract`, and contracts stop being extracted.
