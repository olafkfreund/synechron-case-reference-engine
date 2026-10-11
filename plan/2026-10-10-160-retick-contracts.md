---
status: approved
issue: 160
spec: spec/2026-10-10-160-retick-contracts.md
---

# Plan: re-ticking "contracts executed" brings back what the untick retired

Approved decision (B): the untick marks the cases it rejects, and re-ticking
re-queues only those.

**The new column.** `cases.retired_by text` is nullable. `'untick'` means the
case was rejected by the source flag. The column is added to the idempotent
`sql/schema.sql` and applied by `db.init()`:

- in AWS, by `app.migrate` as the schema owner;
- locally, on start (`DB_INIT_ON_START=1`);
- in tests, through `db.init()`.

Grants are on whole tables (`sql/roles.sql:13`), so no grant is needed.

**Setting and reading the mark.**

- `RETIRE_FLAGGED` also sets `retired_by='untick'`. It keeps the same
  `where`, including `c.status<>'rejected'`, so a case a reviewer had already
  rejected is never marked. It keeps the same `returning`.
- `QUEUE_CONTRACTS` also queues a contract whose case carries the mark. Its
  case guard becomes:
  `not exists (select 1 from cases c where c.document_id=d.id and c.retired_by is distinct from 'untick')`.
  The payload keeps `'checksum', d.checksum` (#120). The guard against a
  queued or running job stays.

**Clearing the mark.** It is cleared whenever the case is written again, so a
later reviewer reject is never mistaken for the untick's:

- the `extract()` upsert sets `retired_by=null`;
- `ingest()`'s `update cases set status=...` sets `retired_by=null`.

The review actions don't need to clear it. A rejected case is not `OPEN`, so
it can't be edited, approved or rejected. `unmerge` keeps a rejected member
rejected and leaves its mark, which is correct.

**Old rows.** Cases rejected by an untick before this change have no mark and
stay rejected. No backfill.

**Out of scope, follow-up.** A contract ingested while the source is unticked
is rejected by `ingest()` with no mark. Re-ticking does not bring it back, and
it doesn't today either.

**Rejected alternatives:**

- **A:** don't tell the two kinds of reject apart. A reviewer's reject would
  come back.
- **C:** mark reviewers' rejects instead. Ingest's rejects and every old
  reject would then count as the untick's.
- **A general reason enum.** Only one value is needed.

**Coder handoff: yes.** Four steps edit files, across seven files.

**Base.** This plan is written against the code in `refs-engine-132` (PR #175).
Line numbers below are from that tree.

## Steps

0. **Rebase on main after #175 merges first.**
   - Command: `git fetch origin && git rebase origin/main`.
   - #175 rewrites `extract()`: it adds a checksum check before the upsert,
     and moves the documents ACL update in `sources.update()` above the
     executed-contracts branch.
   - Verify: `grep -n "for share" app/extract.py` shows the #132 check, and
     `git log --oneline -1 origin/main` is the #175 merge.
   - Trap: if #175 hasn't merged yet, stop and ask. Don't build on the old
     `extract()`.

1. **Tests first.**
   - In `tests/test_sources.py`, append after
     `test_unticking_executed_retires_flagged_engagements` (line 104). Reuse
     `_src`, `_doc`, `_case`, `_jobs`, `_clean` and `REASON` (lines 46-83).
     - **`test_reticking_brings_back_untick_rejects(env)`:**
       - Setup: `db.init()`. Create `sid = _src(c, True)` and
         `d = _doc(c, sid, "contract")`. Then
         `cid = _case(c, d, "approved", "engagement", REASON)`.
       - Untick: post `/admin/sources/{sid}` with `{"acl_groups": "g1"}`.
         Expect 303. `select status, retired_by from cases where id=%s`
         returns `("rejected", "untick")`.
       - Tick: post with `{"acl_groups": "g1", "executed_contracts": "on"}`.
         Expect 303. `[r[0] for r in _jobs(c, sid)]` equals
         `[{"document_id": d, "basis": "engagement", "basis_reason": REASON, "checksum": "x"}]`.
         `_doc` stores checksum `'x'`.
       - Run `_clean(sid)` in `finally`.
     - **`test_reticking_keeps_a_reviewers_reject(env)`:**
       - Setup: `sid = _src(c, False)`, `d = _doc(c, sid, "contract")` and
         `cid = _case(c, d, "rejected", "engagement", REASON)`. This case has
         no mark.
       - Tick: post with `executed_contracts=on`. `_jobs(c, sid) == []`, and
         the status is still `rejected`.
       - Run `_clean(sid)` in `finally`.
       - This test is a guard: it passes on main by design.
   - In `tests/test_extract.py`, append at the end.
     **`test_extract_clears_retired_by(doc)`:**
     - Setup: `did, _ = doc`. Call `run(doc, case())`, then run
       `update cases set status='rejected', retired_by='untick' where document_id=%s`.
     - Call `run(doc, case())` again.
     - Expect `select status, retired_by from cases where document_id=%s` to
       return `("extracted", None)`.
   - Verify:
     `docker compose build -q app && docker compose run --rm app timeout 900 pytest -q -p no:cacheprovider tests/test_sources.py tests/test_extract.py`.
     On main code:
     - `test_reticking_brings_back_untick_rejects` fails, because the column
       does not exist and no job is queued.
     - `test_extract_clears_retired_by` fails, because the column does not
       exist.
     - `test_reticking_keeps_a_reviewers_reject` passes.
   - Traps:
     - There is no bind mount, so always build before running tests.
     - Never run `docker compose up` or `down`.
     - Use made-up names only.
     - The `env` fixture comes from `tests.test_auth` and needs the
       `# noqa: F811` that the neighbouring tests carry.

2. **`sql/schema.sql`: add the column after line 90** (the
   `cases_merged_into_idx` line).
   - Add this line:
     `alter table cases add column if not exists retired_by text;  -- 'untick': rejected by the source flag (#160)`
   - Verify: run the step 1 command twice in a row. `db.init()` is
     idempotent, and the second run must not error.
   - Traps:
     - Use `add column if not exists`, never a bare `add column`.
     - No default and no check constraint. Null means "not the untick's".

3. **`app/sources.py:31-41`: change `QUEUE_CONTRACTS` and `RETIRE_FLAGGED`.**
   - In `QUEUE_CONTRACTS` (line 35), change the case guard to:
     `and not exists (select 1 from cases c where c.document_id=d.id and c.retired_by is distinct from 'untick')`
   - In `RETIRE_FLAGGED` (line 38), change the set clause to:
     `update cases c set status='rejected', retired_by='untick' from documents d`.
     Leave the rest of the statement as it is.
   - In `app/templates/sources.html:10`, the "Contracts executed" help:
     change "Ticking it also queues contracts already crawled that have no
     case" to "Ticking it also queues contracts already crawled that have no
     case, or whose engagement an earlier untick retired".
     - The spec doesn't list this file. The change keeps the help true.
       Record it as a deviation if review objects.
   - Verify: step 1's command. `test_reticking_brings_back_untick_rejects`
     passes. `test_ticking_executed_queues_contracts_without_a_case` and
     `test_unticking_executed_retires_flagged_engagements` still pass.
   - Traps:
     - Use `is distinct from`, not `<>`. With `<>`, a case with a null mark
       would let the document through.
     - Don't touch `DROP_FLAGGED_JOBS` or the lock order in `update()`. #132
       moved the documents update above this branch, so documents are locked
       before cases.

4. **Clear the mark on rewrite.**
   - **`app/extract.py:155-158`, the upsert in `extract()`.** Append
     `retired_by=null` to the `do update set` list. The list then ends:
     `... status='extracted', approved_by=null, approved_at=null, review_due=null, retired_by=null`.
   - **`app/ingest.py:120-121`, in `ingest()`.** Change the statement to:
     `conn.execute("update cases set status=%s, retired_by=null where document_id=%s", ...)`.
     The parameters stay the same.
   - Verify: step 1's command. `test_extract_clears_retired_by` passes. Then
     run the full suite:
     `docker compose build -q app && docker compose run --rm app timeout 900 pytest -q -p no:cacheprovider`.
   - Traps:
     - #159 adds a flag re-check in `extract()` next to this upsert. Keep
       both if it lands first. The conflict is textual only.
     - #133 (PR #174) and #158 edit `ingest()` above this line. Rebase if
       either lands first.

## Tests

- The two new tests that change behaviour fail on main and pass on the branch.
  The reviewer-reject test is a guard and passes on both.
- The full suite is green.

## Rollback

Revert the commits. The column stays in the database, unused and harmless.
`schema.sql` never drops columns. If wanted, it can be dropped by hand with
`alter table cases drop column retired_by` as the schema owner.
