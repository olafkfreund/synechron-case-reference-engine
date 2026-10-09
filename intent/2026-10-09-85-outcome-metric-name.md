---
status: draft
issue: 85
author: olafkfreund
---

# Intent: Extraction names outcome metrics "metric" on local qwen3

## Problem

On `ollama_chat/qwen3:14b`, extraction often sets `Outcome.metric` to the
word "metric", or to the whole outcome sentence. In 4 of the 7 made-up case
studies used for the local demo (#75), at least one outcome had this problem.
Reviewers can fix it on the review page (#40), but until they do the outputs
show "metric: …" (`app/render.py:92`, `app/search.py:133`).

The code shows how this happens:

- **The model never sees a definition of a metric.** It does not get the
  `Outcome` schema (`app/schema.py:64-69`). It gets the flat `Extraction`/`Item`
  schema (`app/schema.py:140-159`), where an item has only `field`, `value` and
  `quote`. The only guidance is one sentence in the prompt,
  `"For outcome write the value as 'metric: value'"` (`app/extract.py:16`).
  A small model copies that template word for word, so the result is
  "metric: 40% faster".
- **When there is no colon, the code copies the sentence into the metric.**
  `assemble` splits on the first ":" and, if there is none, sets both metric
  and value to the whole string (`app/schema.py:194-195`). A test pins this
  behaviour: `("faster", "faster")` in `tests/test_schema.py:132`. A colon
  inside the value, as in "ratio: 3:1", also splits in the wrong place, but
  the issue does not report that.
- **Nothing flags the result.** `needs_attention` is built only from
  `assemble`'s malformed-item count and from `build`'s truncation and summary
  notes (`app/schema.py:202-205`, `app/extract.py:118-130`). `check()`
  (`app/extract.py:102-103`) asks only whether `"{metric} {value}"` is backed
  by the quote, and "metric" or a repeated sentence still passes that check.
- **The eval does not measure it.** `scripts/eval_extraction.py:106-108`
  prints how many outcomes there are and the notes, but no per-outcome metric
  quality. The before and after effect would show up only as a change in the
  notes.

## Proposed outcome

- The extraction prompt says what a metric is: a short noun phrase such as
  "claim handling time", with one example.
- An outcome whose metric is empty, is "metric", or is equal to its value gets
  a `needs_attention` note. A reviewer sees the note on the review page
  (`app/review.py:145`). Approval clears it as it clears the other notes
  (`app/review.py:228`).
- `scripts/eval_extraction.py` on local models shows fewer bad metrics after
  the change than before. It prints metrics only, and the before and after
  numbers go in the PR.

## Affected users and systems

- `app/extract.py`: `SYSTEM` prompt (line 16), and `build`, where the
  notes are added (lines 118-130). `CONTRACT_SYSTEM` is not affected:
  engagement cases drop outcomes (line 122).
- `app/schema.py`: `assemble`, the outcome split (lines 193-195).
  `Item`/`Extraction` only if the definition goes into the schema.
- `tests/test_schema.py:132` (the `("faster", "faster")` assertion) and
  `tests/test_extract.py` (new cases).
- `scripts/eval_extraction.py`: maybe a count of bad metrics in its output
  line, in which case `tests/test_eval_script.py` changes too.
- Reviewers, who see fewer bad metrics and a note on the ones that remain.
  Existing stored cases are not re-extracted.
- `demo/cases.json` uses `"metric": "Result"` on 11 outcomes. That is a
  generic placeholder too, but it does not match the issue's three rules.

## Constraints

- Data policy: confidential documents go only to `local` or `our-cloud`
  models. The eval runs only on local models, prints metrics only, and never
  sends document text to a third-party model, including Claude.
- Test data is made up. Use demo/cases.json or fixtures written for the test.
- No new dependencies.
- The prompt stays model-agnostic: it must not get worse on the cloud models
  it already works on.
- The note flags the outcome and changes nothing. The outcome stays in the
  case, and the reviewer decides on the review page.
- Tests run with `docker compose build app && docker compose run --rm app pytest`.
  Never run `docker compose up` or `down`, and never use the `refsdemo` or
  `refsdev` projects.

## Open questions

1. **Where does the definition go?** (a) The `SYSTEM` prompt sentence at
   `app/extract.py:16`. (b) A `description` on `Item.value`. (c) Separate
   `metric`/`value` keys on `Item`. Lean: (a). It is the only text the model
   reads about outcomes. (b) applies to every field, not only outcomes, and
   (c) changes the schema for every item and for every model.
2. **No colon: keep the sentence as the metric, or leave the metric empty?**
   Lean: leave it empty. Then the "empty" rule flags it, and no invented
   metric reaches the outputs. `tests/test_schema.py:132` changes.
3. **Is "metric" the only placeholder word?** For example "Result", "outcome"
   or "value". Lean: treat it as a small case-insensitive set:
   metric, outcome, result, value. Then the 11 "Result" outcomes in
   demo/cases.json show the note. Otherwise use "metric" only, as the issue
   says.
4. **Note granularity.** Lean: one note per case with a count, for example
   "2 outcome(s) with no clear metric". This matches the existing
   "N malformed or unknown item(s) skipped" note.
5. **Eval output.** Lean: add a `bad_metrics=N` count to each document line
   in `scripts/eval_extraction.py`. Today the result shows only inside the
   notes text, and that is hard to compare between runs.
