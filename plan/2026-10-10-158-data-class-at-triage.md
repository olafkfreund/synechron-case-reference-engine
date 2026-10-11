---
status: approved
issue: 158
spec: spec/2026-10-10-158-data-class-at-triage.md
---

# Plan: triage uses the data class as it is after conversion

Approved decisions, carried over from the spec:

- Decision A: read the source's `data_class` again just before `triage_text`,
  after the S3 put and the Docling conversion. No lock is held across the
  conversion or the model call.
- The early read in `ingest()` stays as the existence check before the S3 put
  and Docling; its value no longer reaches triage.
- A source deleted during conversion raises `LookupError`, as the early read does.
- `llm.allowed` is not touched; an unknown class still fails closed.
- Extraction is unchanged: `extract()` reads the class when its job runs.
- A raise that lands during the triage call itself still goes out under the old
  class; the intent accepts that window.

The code below is `ingest()` as it is after #133 (PR #174, branch
`fix/133-config-read-under-lock` in `refs-engine-133`): the early select reads only `data_class`,
and `config` is read `for share` inside the write transaction.

## Steps

0. Rebase on main after #174 (#133) has merged: `git fetch origin && git rebase
   origin/main`. → verify by `grep -n "for share" app/ingest.py` showing the
   `select config from sources where id=%s for share` line.
   Traps: this worktree is behind main (it predates #144 too). Rebase before any
   edit; do not hand-copy #133's lines.

1. `app/ingest.py`, `ingest()`: re-read the class between conversion and triage.
   After `text = to_markdown(...)` (line 95 on #133's branch) and before
   `triage = triage_text(text, data_class)` (line 96), insert:
   ```python
   with db.connect() as conn:  # conversion can take minutes; an admin may have raised the class (#158)
       src = conn.execute("select data_class from sources where id=%s", (source_id,)).fetchone()
   if not src:
       raise LookupError(f"source {source_id} no longer exists")
   data_class = src[0]
   ```
   → verify by the test in step 2.
   Traps:
   - Plain select, no `for share`: a lock here would be held only until the
     `with` ends anyway, and Decision A rejected holding one across the call.
   - Leave the early read (lines 88–91) as it is.

2. `tests/test_ingest.py`: add
   `test_data_class_raised_during_conversion_triages_under_new_class(env, monkeypatch)`
   next to the #133 toggle tests (after `test_ingest_waits_for_an_uncommitted_tick`,
   around line 170):
   - `_, sid = env`; set `data_class='public'` on the source in its own
     `with db.connect()` (the schema default is `confidential`).
   - Patch `ing.to_markdown` with `def convert(data, name)` that runs
     `update sources set data_class='confidential' where id=%s` in its own
     `with db.connect() as c:` (commits on exit) and returns `data.decode()`.
   - Patch `ing.triage_text` with a function that appends its `data_class` to a
     list and returns `CASE`.
   - `ing.ingest(sid, "a", "a", b"x")`; assert the list is `["confidential"]`.
   → verify by `docker compose build -q app && docker compose run --rm app pytest
   -q -p no:cacheprovider tests/test_ingest.py`.
   Traps:
   - The `env` fixture already patches `to_markdown` as `(data, name)`; the
     test's patch overrides it and must keep that signature.
   - Must fail on main (records `"public"`): check with the scratchpad
     `check.sh` after committing.

## Tests

- The new test fails on main and passes on the branch.
- Full suite: `docker compose build -q app && docker compose run --rm app timeout
  900 pytest -q -p no:cacheprovider`, all green.

## Rollback

Revert the branch's commits. No schema or data change.
