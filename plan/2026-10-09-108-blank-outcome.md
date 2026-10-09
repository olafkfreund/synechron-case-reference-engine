---
status: approved
issue: 108
spec: spec/2026-10-09-108-blank-outcome.md
---

# Plan: an outcome with no value survives approval

Today a reviewer can save an outcome with an empty value. `check()` then
tests the string `f"{metric} {value}"`, which is `" "` and never empty, so
any quote of 4 or more words sources it. Approval keeps it. Search then shows
an empty bullet, and a metric with an empty value shows as "Revenue: ".

Approved decisions:

- **Shared rule in `check()`.** An outcome whose value is empty after
  `.strip()` is `unsourced = True`. The rule looks at the value only, so a
  metric with an empty value is covered too.
  - `Outcome.value` is a required `str` (`app/schema.py:66`), so there is no
    `None` case.
  - All four `check()` callers get the rule with no change of their own:
    extraction (`app/extract.py:121`), edit (`app/review.py:209`), approve
    (`app/review.py:222`) and merge (`app/review.py:372`).
  - Approve already re-runs `check()` and drops unsourced outcomes
    (`app/review.py:222,229`). Outcomes already stored with an empty value
    are dropped on their next approval. No migration.
  - `mark()` and the "empty values are not flagged" rule for other fields
    stay as they are.
- **Edit refusal.** Saving an existing outcome (`outcomes.N`) with an empty
  value raises `ValueError`, and the handler's existing `except` turns that
  into `400 "invalid value"`.
  - An empty metric with a value is still allowed; the #99 test
    `test_whitespace_metric_stored_empty` relies on that.
  - Remove (`action=remove`) is handled earlier in the handler and is not
    affected.
- **Docstring.** `check()` says an outcome with no value is unsourced.
- **Out of scope:** the schema (no `min_length`), templates, search, render,
  the extraction prompts and demo data.

## Steps

1. `app/extract.py`:
   1. Lines 104–105: after `mark(o, f"{o.metric} {o.value}")`, inside the
      same loop, add:
      ```python
          if not o.value.strip():  # no value, nothing to source (#108)
              o.unsourced = True
      ```
   2. Docstring, line 83: after "so it is not flagged.", add "An outcome
      with no value is the exception: it is unsourced (#108)."

   → verify with `git diff app/extract.py`: 3 added lines, 1 changed
   docstring line.

   Traps:
   - Add the line after `mark()`, not instead of it. `mark()` resets
     `unsourced` for outcomes that do have a value.
   - Don't touch `mark()` itself. Its `value not in (None, "")` rule is right
     for scalars.

2. `app/review.py:195-196`, the `isinstance(obj, Outcome)` branch: before
   the assignment, add `if not value: raise ValueError`. The branch becomes:
   ```python
               if isinstance(obj, Outcome):
                   if not value:  # an empty outcome is removed, not saved (#108)
                       raise ValueError
                   obj.metric, obj.value = metric or "", value or ""
   ```
   → verify with `git diff app/review.py`: 2 added lines.

   Traps:
   - `value` has already been stripped by #99 (line 165), so `not value`
     covers `None`, `""` and whitespace.
   - Don't check `metric`. An empty metric with a value must still save.
   - Don't touch the add path (line 182). It already refuses an empty value.

3. `tests/test_extract.py`: add a test after
   `test_price_in_numeric_item_quote_clears_the_item` (line 187). It follows
   that test's pattern of building a `ReferenceCase` directly:
   ```python
   def test_outcome_without_value_unsourced():
       from app.schema import Outcome, ReferenceCase
       case = ReferenceCase(outcomes=[Outcome(metric="Revenue", value="", source_quote=Q),
                                      Outcome(metric="", value="  ", source_quote=Q),
                                      Outcome(metric="onboarding", value="12 days to 3 days", source_quote=Q)])
       ex.check(case, DOC)
       assert [o.unsourced for o in case.outcomes] == [True, True, False]
   ```
   → verify with `docker compose build app && docker compose run --rm app pytest -q tests/test_extract.py -k without_value`:
   it fails before step 1 and passes after.

   Traps:
   - No bind mount, so build the image before every run.
   - If `ReferenceCase` needs other required fields, copy them from the
     neighbouring test rather than changing the schema.

4. `tests/test_review.py`: add two tests after `test_padded_add_is_trimmed`
   (line 279). They use the existing `make`, `case_data`, `client(R)`,
   `post`, `row` and `ver` helpers.
   1. `test_edit_outcome_without_value_refused`. For each of
      `dict(metric="onboarding", value="  ")` and `dict(metric="", value="")`:
      `post(c, cid, field="outcomes.0", quote=Q_TITLE, **f)` gives 400, and
      `row(cid)[1]` equals the value read before the posts.
   2. `test_stored_empty_outcome_dropped_on_approval`:
      - Set up with
        `make(data=case_data(outcomes=[Outcome(metric="Revenue", value="", source_quote=Q_TITLE), Outcome(metric="onboarding", value="12 to 3 days", source_quote=Q_TITLE)]))`.
      - Approve with `c.post(f"/review/{cid}/approve", data={"v": ver(cid)})`.
      - Assert `row(cid)[0] == "approved"`.
      - Assert `[(o["metric"], o["value"]) for o in row(cid)[1]["outcomes"]] == [("onboarding", "12 to 3 days")]`.

   → verify with `docker compose build app && docker compose run --rm app pytest -q tests/test_review.py`:
   all pass. With steps 1–2 reverted (`git stash`), both new tests fail.

   Traps:
   - `make()`'s default basis is `delivered`, so outcomes are kept.
     `engagement` would drop them for a different reason.
   - Approve redirects, so assert on `row()`, not the response body.
   - The spec also asked to show the empty outcome as unsourced on the review
     page before approval. Step 3 checks that flag directly. A page-text
     check would be weak, because `case_data` already has an unsourced
     industry. So the page check is left out.

5. **Full suite and eval.** This step edits no files.
   - Run `docker compose build app && docker compose run --rm app timeout 900 pytest`:
     all pass at main's count (552) plus 3.
   - Optionally, run `scripts/eval_extraction.py` on the **made-up demo
     documents only**, as for #85: write the documents outside the repo,
     use the local `ollama_chat/qwen3:14b`, and use `--network host`. Outcome
     counts for outcomes with values are unchanged from main.

   Traps:
   - Never use `--docs ~/presale` or any real document.
   - Never use the `refsdev` project.
   - Never run `docker compose up` or `down`.

## Tests

```
docker compose build app && docker compose run --rm app timeout 900 pytest -q tests/test_extract.py tests/test_review.py
docker compose build app && docker compose run --rm app timeout 900 pytest
```

Expected:
- the three new tests pass, and the two review tests fail on main;
- `test_add_needs_value_and_quote` and the three #99 tests pass unchanged;
- the full suite is main's count plus 3.

## Rollback

Revert the commits. No schema or data change. Records that were dropped on
approval while the change was live had no value, so nothing with content was
lost.
