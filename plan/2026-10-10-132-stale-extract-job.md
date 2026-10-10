---
status: approved
issue: 132
spec: spec/2026-10-10-132-stale-extract-job.md
---

# Plan: A queued extract job revives a document that a newer version retired

Approved decision (B): `extract()` checks the job's checksum against the
document's current one and does nothing when they differ. The worker passes the
payload's `checksum` (put there by #120). There are two checks:

- **Early:** the first select also reads `d.checksum`. On a mismatch it returns
  before `build()`, so no model call is spent.
- **At write time:** right before the upsert, in the same transaction:
  `select 1 from documents where id=%s and checksum=%s for share`. If no row
  comes back, it returns. This catches a version that lands during the model
  call: `for share` waits for an open `ingest()` and then re-reads the row.

A job with no checksum (queued before #120) skips both checks and runs as
today. A stale job returns normally, so the worker marks it `done`; it does not
raise and nothing is logged.

Coder handoff: yes. Three steps edit files, in four files.

## Steps

1. **Tests first.** `tests/test_extract.py` (append after the last test) and
   `tests/test_worker.py` (append at the end).
   - `tests/test_extract.py`, using the existing `doc` fixture (lines 21-33;
     the document's checksum is `'x'`) and `case()` (line 14):
     - `test_stale_checksum_writes_no_case(doc, monkeypatch)`:
       - Patch `ex.complete_json` with a function that appends to a `calls`
         list and returns `case()`.
       - Call `ex.extract(did, checksum="old")`.
       - Assert no `cases` row for `did`, and `calls == []`.
     - `test_version_replaced_during_model_call_writes_no_case(doc, monkeypatch)`:
       - Patch `ex.complete_json` with a function that opens
         `db.connect(autocommit=True)`, runs
         `update documents set checksum='y' where id=%s`, then returns
         `case()`.
       - Call `ex.extract(did, checksum="x")`.
       - Assert no `cases` row for `did`.
     - `test_no_checksum_still_extracts(doc)`:
       - `did, reply = doc; reply["v"] = case(); ex.extract(did)`.
       - Assert the case row's status is `'extracted'`.
   - `tests/test_worker.py`:
     - `test_extract_handler_passes_checksum(monkeypatch)`:
       - `got = {}`. Patch `worker.extract.extract` with
         `lambda *a: got.setdefault("a", a)`.
       - Call `worker.HANDLERS["extract"]({"document_id": 7, "checksum": "c"})`.
       - Assert `got["a"] == (7, "delivered", "", "c")`.
       - Check first that `app/worker.py` imports the module as `extract`
         (line 11 calls `extract.extract`).
   - Verify: `docker compose build app && docker compose run --rm app timeout 900 pytest -q -p no:cacheprovider tests/test_extract.py tests/test_worker.py`.
     On main code, the two stale tests and the handler test fail and
     `test_no_checksum_still_extracts` passes.
   - Traps:
     - There is no bind mount, so always build before running tests.
     - Never run `docker compose up` or `down`.
     - The `clean` fixture in `test_worker.py` is autouse, so it runs for the
       new test.
     - Made-up names only.

2. **`app/extract.py:141-153`, `extract()`.**
   - Signature:
     `def extract(document_id: int, basis: str = "delivered", basis_reason: str = "", checksum: str | None = None) -> None:`
   - Line 143: select `d.text, s.data_class, d.checksum`. After the
     not-found check, add:
     `if checksum and row[2] != checksum: return  # a newer version replaced this one (#132)`
   - Just before `conn.execute("insert into cases...` (line 148), add:
     ```python
     # the version this job was queued for must still be current (#132)
     if checksum and not conn.execute("select 1 from documents where id=%s and checksum=%s for share",
                                      (document_id, checksum)).fetchone():
         return
     ```
   - Verify: step 1's command. Both stale tests pass now.
   - Traps:
     - Keep the `LookupError` for a missing document.
     - The share lock must be in the same `with db.connect()` transaction as
       the upsert.
     - Don't touch `build()`, which `scripts/eval_extraction.py` calls.

3. **`app/worker.py:11`, `HANDLERS["extract"]`.**
   - Change to:
     `"extract": lambda p: extract.extract(p["document_id"], p.get("basis", "delivered"), p.get("basis_reason", ""), p.get("checksum")),`
   - Verify: `test_extract_handler_passes_checksum` passes, then run the full
     suite.
   - Traps:
     - Use `p.get`, not `p[...]`, because old jobs have no checksum.

## Tests

- `docker compose build app && docker compose run --rm app timeout 900 pytest -q -p no:cacheprovider`
  must pass in full.
- Run the three new failing tests against main's `app/` (the parent's check
  script) to confirm they fail there.

## Rollback

Revert the branch's commits. The payload's checksum stays harmless, because
nothing else reads it.

## Deviations
- Step 1: a small `cases_for(did)` helper in `tests/test_extract.py` is shared by the three new extract tests.
- Review: `sources.update` now applies the documents ACL update before the executed-contracts branch, so the untick locks documents before cases, like ingest and extract; otherwise an untick that also changes groups could deadlock with a running extract.
- Review: `test_extract_waits_for_an_uncommitted_new_version` holds ingest's checksum update open while extract runs; it fails if the `for share` is removed. The worker test also covers a payload with no checksum.
