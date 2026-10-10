---
status: draft
issue: 158
intent: intent/2026-10-10-158-data-class-at-triage.md
---

# Spec: triage uses the data class as it is after conversion

## Design

Decision A: read the class again just before `triage_text`, and don't hold a lock
across the model call.

- **`app/ingest.py`, `ingest()`.** Between `to_markdown(...)` and
  `triage_text(...)`, add:
  ```python
  with db.connect() as conn:  # conversion can take minutes; an admin may have raised the class (#158)
      row = conn.execute("select data_class from sources where id=%s", (source_id,)).fetchone()
  if not row:
      raise LookupError(f"source {source_id} no longer exists")
  data_class = row[0]
  ```
  Then call `triage_text(text, data_class)` as today.
- **The early read stays.** It is the existence check before the S3 put and
  Docling. It still supplies `config` on main, and after #133 it reads only
  `data_class`. Its value no longer reaches triage.
- **Extraction is unchanged.** `extract()` already reads the class when its
  job runs.

## Clash with #133

#133 edits the same function. It trims the early select to `data_class` and moves
the `config` read into the write transaction, under `for share`. The two changes
touch neighbouring lines and don't overlap in meaning: #158 changes what reaches
triage, and #133 changes what reaches `basis_for`. Whichever lands second
rebases, keeping both reads.

## Alternatives rejected

- **B, `for share` on the source row across the model call.** This closes the
  seconds-long window too, but an admin's save would then wait on model latency,
  which was rejected at intent.
- **Drop the early read altogether.** That is a smaller diff, but a deleted
  source would then still pay for the S3 put and the conversion before it fails.
- **Pass the class into `to_markdown` and check again afterwards.** Conversion
  never sends data out, so only the class at triage matters.

## Risks

- A raise that lands during the triage call itself still goes out under the old
  class. The intent accepts this window of a few seconds.
- The change adds one short read per ingested document, on the same pooled
  connection pattern as the rest of `ingest()`. Its cost is negligible.

## Verification

New test in `tests/test_ingest.py`, `test_data_class_raised_during_conversion_triages_under_new_class`:

- Set the env source to `data_class='public'`.
- Patch `ing.to_markdown` with a function that commits
  `update sources set data_class='confidential' where id=%s` in its own
  connection, then returns the text.
- Patch `ing.triage_text` to record the `data_class` it receives and return a
  delivered `Triage`.
- After `ing.ingest(sid, "a", "a", b"x")`, the recorded class is
  `"confidential"`. On main it is `"public"`, so the test fails there.

Then the full suite: `docker compose build app && docker compose run --rm app
timeout 900 pytest -q -p no:cacheprovider`.
