---
status: draft
issue: 108
intent: intent/2026-10-09-108-blank-outcome.md
---

# Spec: an outcome with no value survives approval

## Design

The approved answers ask for two changes: one rule in the shared check, and
one refusal on the edit. The rule is on the outcome's **value** only, so it
also covers a metric with an empty value (answer 2).

1. **`check()` marks an outcome with an empty value unsourced**
   (`app/extract.py:104-105`). After the existing `mark(o, ...)`, add one
   condition: if `o.value.strip()` is empty, set `o.unsourced = True`.
   - `Outcome.value` is a required `str` (`app/schema.py:66`), so no `None`
     case is needed.
   - All four `check()` callers get it with no change of their own:
     - extraction (`app/extract.py:121`);
     - reviewer edit (`app/review.py:209`);
     - approve (`app/review.py:222`);
     - merge (`app/review.py:372`).
   - Approve already re-runs `check()` and then drops unsourced outcomes
     (`app/review.py:222,229`). So outcomes already stored with an empty value
     are dropped on their next approval, with no migration.
   - Search (`app/search.py:136-137`) and generated documents
     (`app/render.py:92`) already skip unsourced outcomes.
   - `mark()` and the "empty values are not flagged" rule for other fields
     do not change. Outcomes are the only field whose empty value is now
     flagged, because their checked string (`f"{metric} {value}"`) is never
     empty.
2. **The edit refuses an empty outcome value with 400** (`app/review.py:195-196`,
   the `isinstance(obj, Outcome)` branch).
   - **The rule:** if `value` is empty after the #99 trim (`None` or `""`),
     raise `ValueError`. The handler's existing `except` turns it into
     `400 "invalid value"`, like the add path (`app/review.py:182`).
   - **Metric:** an empty metric with a value is still allowed. That is
     today's behaviour, and the `test_whitespace_metric_stored_empty` test
     covers it.
   - **Clearing an outcome:** the reviewer uses Remove (#40), which is
     handled earlier in the same handler and is not affected.
   - **Form:** the review page always sends both `metric` and `value` for
     an outcome row (`app/review.py:102`), so a normal save is never refused
     by accident.
3. **Docstring.** `check()`'s docstring (`app/extract.py:83`) gains one
   clause: an outcome with no value is unsourced.

No change to the schema, templates, search, render, extraction prompts or
demo data.

## Alternatives rejected

- **The 400 alone (intent option 1).** It doesn't cover outcomes already
  stored, or the model returning an empty value. The approved answer adds it
  only for immediate reviewer feedback.
- **Treat an empty value as "not flagged" (intent option 2).** Approval
  would also need a new drop rule for unflagged empty outcomes. That is
  two changes where one does the job.
- **Check `o.value` in `mark()` for every field.** `mark()` already treats
  `None`/`""` as not flagged. That is right for scalars, whose empty value is
  dropped elsewhere or is meant to be empty. Changing it would alter every
  field.
- **Refuse in the schema (`Outcome.value` with `min_length=1`).**
  - It would make stored records with an empty value fail to load.
  - It would make model output with one fail validation, failing the whole
    extraction instead of one outcome.

## Risks

- **Extraction counts.** If the model returns an outcome with an empty
  value, it now counts as unsourced. That is the intended behaviour, and
  outcomes with a value are marked exactly as before.
  - Check with `scripts/eval_extraction.py` on the made-up demo documents:
    the sourced and unsourced counts for outcomes with values should be
    unchanged.
  - Never run it on presale documents.
- **Stored records.** A case already holding an empty outcome shows it as
  unsourced on its next save or approval, and approval drops it. No data is
  lost that had any content.
- **Merged cases.** Merge calls `check()` with a dict of texts. The new
  condition doesn't read the text, so it behaves the same.
- No host, infra or runtime-config impact. Tests run only in the
  `refs-engine-108` compose project; `refsdemo` and `refsdev` are not
  touched.

## Verification

New tests in `tests/test_review.py`, beside the #99 tests. The first two
must fail on main:

1. **Edit refused.**
   - **Request:** `post(client(R), cid, field="outcomes.0", metric="onboarding", value="  ", quote=Q_TITLE)`.
   - **Expect:** 400, and the stored record is unchanged.
   - **Same for a blank metric:** `metric="", value=""` also gives 400.
2. **Stored empty outcome dropped on approval.**
   - **Setup:** `make(data=case_data(outcomes=[Outcome(metric="Revenue", value="", source_quote=Q_TITLE), Outcome(metric="onboarding", value="12 to 3 days", source_quote=Q_TITLE)]))`.
   - **Before approval:** the empty outcome shows `unsourced` on the review page.
   - **After approval:** the stored outcomes are `[("onboarding", "12 to 3 days")]`.
3. **`check()` directly** (`tests/test_extract.py`, where the `check()`
   tests live).
   - An outcome with `value=""` is `unsourced=True`, both with a metric and
     without.
   - An outcome with a sourced value keeps `unsourced=False`.

Then:

- `docker compose build app && docker compose run --rm app timeout 900 pytest`:
  all pass, at main's count plus the new tests.
- `test_add_needs_value_and_quote` and the three #99 tests pass unchanged.
- **Eval:** `scripts/eval_extraction.py` on the made-up demo documents gives
  the same counts as main for outcomes with values.
