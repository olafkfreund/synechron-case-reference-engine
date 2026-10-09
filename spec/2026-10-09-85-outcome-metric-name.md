---
status: draft
issue: 85
intent: intent/2026-10-09-85-outcome-metric-name.md
---

# Spec: Extraction names outcome metrics "metric" on local qwen3

## Design

The approved answers to the intent's open questions are:

1. The definition and one example go in the `SYSTEM` prompt only.
2. With no colon, the metric is left empty.
3. Four placeholder words are flagged: metric, outcome, result, value.
4. One note per case, with a count.
5. The eval prints `bad_metrics=N` for each document.

### 1. Prompt (`app/extract.py:16`)

In `SYSTEM`, replace the sentence `"For outcome write the value as 'metric: value'. "`
with:

```
"For outcome write the value as 'metric: result', where metric is a short noun phrase "
"naming what was measured, in the document's words, for example 'claim handling time: "
"cut by 38 percent'; never write the word 'metric' itself. "
```

The example has a number on purpose. If a small model copies it, the number
is not in the document, so `check()` marks the outcome unsourced
(`sourced()`, `app/schema.py:45`). The eval then shows the copying. Nothing
else changes: not `CONTRACT_SYSTEM`, which asks for no outcomes, and not the
`Item`/`Extraction` schema.

### 2. No colon (`app/schema.py:193-195`)

In `assemble`:

```python
metric, sep, val = v.partition(":")
c.outcomes.append(Outcome(metric=metric.strip() if sep else "",
                          value=(val if sep else v).strip(), source_quote=q))
```

The colon still splits on its first occurrence. The "ratio: 3:1" case stays
out of scope, as the intent says. `check()` is unchanged: `f"{o.metric} {o.value}"`
with an empty metric checks the value alone (`app/extract.py:103`).

### 3. The rule (`app/schema.py`, next to `Outcome` at line 64)

```python
PLACEHOLDER_METRICS = {"metric", "outcome", "result", "value"}

def vague_metric(o: Outcome) -> bool:
    """No usable metric name: empty, a placeholder word, or a copy of the value (#85)."""
    m = o.metric.strip().lower()
    return not m or m in PLACEHOLDER_METRICS or m == o.value.strip().lower()
```

This is a plain function, not a method or a validator. It flags the outcome
and changes nothing in it. `build` and the eval both use it.

### 4. The note (`app/extract.py`, `build`, lines 118-130)

Add the note after the `if basis == "engagement":` block, because that block
empties `case.outcomes`. Then an engagement case never gets this note:

```python
if n := sum(vague_metric(o) for o in case.outcomes):
    notes.append(f"{n} outcome(s) with no clear metric")
```

The wording matches the existing `"N malformed or unknown item(s) skipped"`
note. The review page already lists `needs_attention` (`app/review.py:145`),
and approval clears it (`app/review.py:228`). No review change is needed.

### 5. Empty metric in the outputs (`app/render.py:92`, `app/search.py:133`)

These two lines format `f"{o.metric}: {o.value}"`. With an empty metric, that
string starts with ": ". Both change to
`f"{o.metric}: {o.value}" if o.metric else o.value`. `search_text`
(`app/schema.py:127`) joins with a space, and an empty metric only adds a
space there, so it stays as it is.

### 6. Eval (`scripts/eval_extraction.py:106-108`)

Add `bad_metrics={sum(vague_metric(o) for o in case.outcomes)}` after
`outcomes={len(case.outcomes)}` in the per-document line. Add a mean
`bad_metrics` to the `== model:` summary line so that before and after can be
compared on one line. The output is still a count, never a metric name or
value: a metric name is document text. `rows` gets a fifth element for the
mean.

### 7. Demo data (`demo/cases.json`)

Change only the `metric` strings. `paragraphs`, `value` and `source_quote`
stay as they are. So every quote is still a verbatim substring of its entry's
paragraphs, every number in a value is still in its quote, and
`tests/test_seed_demo.py` sees the same documents. Each new name is a noun
phrase taken from the quote:

| case | file | metric |
| ---- | ---- | ------ |
| 0 | contoso-claims-intake | claim handling time |
| 1 | northwind-stock-forecasting | stock-outs |
| 2 | woodgrove-payments-gateway | payment success rate |
| 3 | tailspin-route-planning | driving hours |
| 4 | litware-citizen-portal | online self-service |
| 5 | adventure-works-booking-platform | page load time |
| 6 | wide-world-importers-order-automation | manual orders |
| 7 | proseware-billing-modernisation | invoice runs |
| 8 | alpine-ski-house-guest-app | app downloads |
| 10 | northwind-loyalty-platform | active members |
| 11 | litware-records-archive | searchable records |

Case 9 (`contoso-fraud-scoring`, `extracted`) keeps `"metric": "metric"`.
Plan #75 put it there on purpose, to show the weakness for a reviewer to
fix. It also gets `"needs_attention": ["1 outcome(s) with no clear metric"]`,
because the seed builds cases with `check()`, not `build()`
(`scripts/seed_demo.py:79`), and would never add the note itself. The
demo then shows the note on the review page. The seed test requires empty
notes only on approved cases (`tests/test_seed_demo.py:68-69`), and case 9
is `extracted`.

## Alternatives rejected

- **The definition in `Item.value`'s `description`, or separate `metric`/`value`
  keys on `Item`.** Every item would carry it, or the schema would change for
  every item and every model. The prompt is the only text the model reads
  about outcomes. (Intent question 1.)
- **Keep the sentence as the metric when there is no colon.** That puts an
  invented metric in the outputs, and only the "equal to value" rule would
  catch it. An empty metric is caught by the "empty" rule and renders as the
  value alone.
- **Only the word "metric".** "Result" is the same failure: the demo data
  itself shows it in 11 outcomes.
- **One note per outcome.** It makes the notes list longer and adds nothing
  the count does not say. The reviewer sees which outcome on the same page.
- **Computing the note in `assemble`.** That runs before `build` drops the
  outcomes of engagement cases, so those cases would get the note for
  outcomes they do not keep.
- **Repairing the metric in code** (for example, the first words of the value).
  The intent says the note flags and changes nothing. The reviewer decides.
- **Re-extracting stored cases.** Out of scope (intent). They get no note
  until they are extracted again.

## Risks

- **The example is copied.** A small model may return "claim handling time:
  cut by 38 percent" for an unrelated document. `check()` marks it unsourced,
  because 38 is not in that document, and the eval's `sourced=` shows it.
  If it happens in the eval, change the example, not the code.
- **The prompt on cloud models.** The sentence is longer, and its meaning is
  the same as before. The eval runs on local models only (data policy), so a
  cloud regression would show only in use. The wording stays model-agnostic.
- **False positives.** A real metric named "Value" ("order value") is flagged
  only when it is exactly one of the four words. "order value" is not. The
  note changes nothing, so a false positive costs one reviewer glance.
- **Empty metric in other consumers.** `Outcome.metric` stays `str`, so an
  empty string validates. The review page's Add refuses an empty metric
  (`app/review.py:176`), and that stays as it is. An edit of an existing row
  (`app/review.py:190`) already allows "". The only other reader,
  `app/search.py:45`, joins with a space, like `search_text`.
- **The demo DB on hosts already seeded.** The seed skips documents it already
  has (`scripts/seed_demo.py:70-71`). The new metric names appear only on a
  fresh demo volume. This spec does not touch the refsdemo or refsdev
  projects.

## Verification

- `tests/test_schema.py:132`: the assertion becomes
  `("", "faster")`. Add cases for `vague_metric`: "", "Metric", " result ",
  metric == value (case-insensitive) → True; "claim handling time" and
  "order value" → False.
- `tests/test_extract.py`: an outcome "metric: 12 days to 3 days" gives
  `needs_attention == ["1 outcome(s) with no clear metric"]`. Two vague
  outcomes give "2 outcome(s) …". The default fixture ("onboarding time: …")
  still gives `[]`. An engagement case with a vague outcome does not get the
  note (the existing `test_engagement_drops_outcomes_and_commercial_items`
  assertion at line 109 still holds).
- `tests/test_render.py` / `tests/test_search.py`: an outcome with an empty
  metric renders as the value, with no leading ": ".
- `tests/test_eval_script.py:70`: also assert `bad_metrics=0` (the fake reply
  has "settlement time: …"). The no-document-text assertion stays.
- `tests/test_seed_demo.py` passes unchanged. A one-off check in the plan:
  no metric in `demo/cases.json` is vague except case 9's, every
  `source_quote` is in its `"\n\n".join(paragraphs)`, and
  `numbers(value) <= numbers(source_quote)`.
- Full suite: `docker compose build app && docker compose run --rm app pytest`.
- Eval, run by the session model, on local models only:
  `scripts/eval_extraction.py --docs <dir outside the repo> --models ollama_chat/qwen3:14b` on the same
  documents before and after. Only the `bad_metrics=` and `sourced=` numbers
  go in the PR. No metric names, values or quotes. Done means fewer bad
  metrics after than before, with `sourced` no lower.
