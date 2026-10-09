---
status: draft
issue: 85
spec: spec/2026-10-09-85-outcome-metric-name.md
---

# Plan: Extraction names outcome metrics "metric" on local qwen3

## Approved decisions (self-contained)

On `ollama_chat/qwen3:14b`, extraction often sets `Outcome.metric` to the
word "metric", or to the whole outcome sentence. This plan changes the
prompt, flags outcomes that still have no clear metric, and measures the
result. It flags only. It never repairs or drops an outcome.

1. **Prompt (`app/extract.py`, `SYSTEM`, line 16).** Replace the sentence
   `"For outcome write the value as 'metric: value'. "` with exactly:

   ```
   "For outcome write the value as 'metric: result', where metric is a short noun phrase "
   "naming what was measured, in the document's words, for example 'claim handling time: "
   "cut by 38 percent'; never write the word 'metric' itself. "
   ```

   The number in the example is on purpose: a copied example is not in the
   document, so `check()` marks it unsourced. `CONTRACT_SYSTEM` and the
   `Item`/`Extraction` schema do not change.
2. **No colon (`app/schema.py`, `assemble`, lines 193-194).** The metric is
   left empty and the whole string is the value:

   ```python
   metric, sep, val = v.partition(":")
   c.outcomes.append(Outcome(metric=metric.strip() if sep else "",
                             value=(val if sep else v).strip(), source_quote=q))
   ```

   Split on the first colon stays ("ratio: 3:1" is out of scope). `check()`
   is unchanged.
3. **The rule (`app/schema.py`, after `class Outcome`, line 64-69).**

   ```python
   PLACEHOLDER_METRICS = {"metric", "outcome", "result", "value"}

   def vague_metric(o: Outcome) -> bool:
       """No usable metric name: empty, a placeholder word, or a copy of the value (#85)."""
       m = o.metric.strip().lower()
       return not m or m in PLACEHOLDER_METRICS or m == o.value.strip().lower()
   ```

   A plain function, not a method or validator. Used by `build` and the eval.
4. **One note per case (`app/extract.py`, `build`).** After the
   `if basis == "engagement":` block (which empties `case.outcomes`, so
   engagement cases never get it):

   ```python
   if n := sum(vague_metric(o) for o in case.outcomes):
       notes.append(f"{n} outcome(s) with no clear metric")
   ```

   The review page already lists `needs_attention` and approval clears it.
   No change to `app/review.py`.
5. **Empty metric in outputs.** `app/render.py:92` and `app/search.py:133`
   change `f"{o.metric}: {o.value}"` to
   `(f"{o.metric}: {o.value}" if o.metric else o.value)`. `search_text`
   (`app/schema.py:127`) and `app/search.py:45` join with a space and stay.
6. **Eval (`scripts/eval_extraction.py`).** Per-document line: add
   `bad_metrics={…}` right after `outcomes={len(case.outcomes)}`. `rows` gets
   a fifth element (the count) and the `== model:` summary line adds
   `mean bad_metrics {x:.1f}`. Output stays counts only, never a metric name.
7. **Demo data (`demo/cases.json`).** Change only `metric` strings; never
   `paragraphs`, `value` or `source_quote`:

   | case | file | line | new metric |
   | ---- | ---- | ---- | ---------- |
   | 0 | contoso-claims-intake | 174 | claim handling time |
   | 1 | northwind-stock-forecasting | 259 | stock-outs |
   | 2 | woodgrove-payments-gateway | 344 | payment success rate |
   | 3 | tailspin-route-planning | 429 | driving hours |
   | 4 | litware-citizen-portal | 514 | online self-service |
   | 5 | adventure-works-booking-platform | 599 | page load time |
   | 6 | wide-world-importers-order-automation | 684 | manual orders |
   | 7 | proseware-billing-modernisation | 769 | invoice runs |
   | 8 | alpine-ski-house-guest-app | 854 | app downloads |
   | 10 | northwind-loyalty-platform | 1025 | active members |
   | 11 | litware-records-archive | 1110 | searchable records |

   Case 9 (`contoso-fraud-scoring`, status `extracted`, line 939) keeps
   `"metric": "metric"` (plan #75 put it there on purpose) and its `case`
   object gets `"needs_attention": ["1 outcome(s) with no clear metric"]`,
   because the seed uses `check()`, not `build()`, and would never add it.
8. **Out of scope:** re-extracting stored cases, repairing metrics in code,
   one note per outcome, the definition in the schema, the refsdemo/refsdev
   projects (a seeded demo volume keeps the old names).
9. **Eval policy:** local `ollama_chat/qwen3:14b` only, counts only
   (`bad_metrics=`, `sourced=`), no document text to any third-party model,
   Claude included. Done = fewer bad metrics after than before, `sourced`
   no lower.

## Steps

Every step that runs tests: compose has no bind mount, so
`docker compose build app` first, then `docker compose run --rm app pytest …`.
Never `docker compose up` or `down`.

1. **`app/schema.py`: the rule and the no-colon split** (decisions 2, 3).
   - Lines 64-69: add `PLACEHOLDER_METRICS` and `vague_metric` after `class Outcome`.
   - Lines 193-194: the `partition` split from decision 2.
   - `tests/test_schema.py:132`: assertion becomes `("", "faster")`. Add a
     parametrised `test_vague_metric`: `""`, `"Metric"`, `" result "`, and a
     metric equal to its value in another case → True; `"claim handling time"`
     and `"order value"` → False.

   → verify by `docker compose build app && docker compose run --rm app pytest tests/test_schema.py`

   Traps: made-up test data only; no bind mount (build first).
2. **`app/extract.py`: prompt and note** (decisions 1, 4).
   - Line 7: import `vague_metric` from `app.schema`.
   - Line 16: replace the sentence with the exact text from decision 1; keep
     the surrounding string pieces (`"Only include "` follows it on the same
     line today) intact.
   - `build`, after the engagement block (line 124): the note from decision 4.
   - `tests/test_extract.py`: outcome `"metric: 12 days to 3 days"` →
     `needs_attention == ["1 outcome(s) with no clear metric"]`; two vague
     outcomes → `"2 outcome(s) with no clear metric"`; default fixture still
     `[]`; `test_engagement_drops_outcomes_and_commercial_items` (line 97-110)
     still passes with a vague outcome in its reply.

   → verify by `docker compose build app && docker compose run --rm app pytest tests/test_extract.py tests/test_schema.py`

   Traps: prompt text exactly as decision 1, no rewording; `CONTRACT_SYSTEM`
   untouched; made-up test data; no bind mount.
3. **`app/render.py:92`, `app/search.py:133`: empty metric** (decision 5).
   - Both: `(f"{o.metric}: {o.value}" if o.metric else o.value)`. In
     `search.py` this is the argument to `clean(...)`.
   - `tests/test_render.py`, `tests/test_search.py`: an outcome with
     `metric=""` renders as the value with no leading `": "`.

   → verify by `docker compose build app && docker compose run --rm app pytest tests/test_render.py tests/test_search.py`

   Traps: made-up test data; no bind mount; do not touch `search.py:45`.
4. **`scripts/eval_extraction.py:106-117`: bad_metrics** (decision 6).
   - Import `vague_metric` from `app.schema`.
   - `bad = sum(vague_metric(o) for o in case.outcomes)`; `rows.append((sourced, len(items), secs, vram, bad))`.
   - Per-document line: `… outcomes={len(case.outcomes)} bad_metrics={bad} notes=…`.
   - Summary line: add `, mean bad_metrics {sum(r[4] for r in rows) / n:.1f}`.
   - `tests/test_eval_script.py:70`: also assert `"bad_metrics=0" in out.out`
     and `"mean bad_metrics" in out.out`; the no-document-text loop stays.

   → verify by `docker compose build app && docker compose run --rm app pytest tests/test_eval_script.py`

   Traps: print counts only, never a metric name or value; no bind mount.
5. **`demo/cases.json`: metric renames and case 9 note** (decision 7).
   - Lines 174, 259, 344, 429, 514, 599, 684, 769, 854, 1025, 1110:
     `"Result"` → the name from the table.
   - Case 9 `case` object: add `"needs_attention": ["1 outcome(s) with no clear metric"]`.
   - One-off check (not committed), run in the container:
     ```
     docker compose run --rm app python -c "
     import json; from app.schema import Outcome, vague_metric, numbers
     d = json.load(open('demo/cases.json'))
     for i, e in enumerate(d['cases']):
         t = '\n\n'.join(e['paragraphs'])
         for o in e['case'].get('outcomes', []):
             assert vague_metric(Outcome(**o)) == (i == 9), (i, o['metric'])
             assert o['source_quote'] in t, i
             assert numbers(o['value']) <= numbers(o['source_quote']), i
     print('ok')"
     ```

   → verify by the one-off check printing `ok`, then
   `docker compose build app && docker compose run --rm app pytest tests/test_seed_demo.py`

   Traps: demo quotes, paragraphs and values stay verbatim (only `metric`
   strings change); `git diff demo/cases.json` shows 11 changed metric lines
   plus the one added note; never touch the refsdemo or refsdev projects.
6. **Full suite.**

   → verify by `docker compose build app && docker compose run --rm app pytest` (all pass)

   Traps: no bind mount; never `up`/`down`.
7. **Eval: session model only, not the coder** (decision 9).
   - Baseline: on the branch, temporarily revert only the `SYSTEM` sentence at
     `app/extract.py:16` to `'metric: value'. ` (not committed), then
     `docker compose build app` and run
     `docker compose run --rm --network host app python scripts/eval_extraction.py --docs <dir outside the repo> --models ollama_chat/qwen3:14b`.
     `origin/main` has no `bad_metrics=` counter, and the rule and split are
     the same in both runs, so this isolates the prompt.
   - `git checkout app/extract.py`, build again, run the same command on the
     same documents.
   - Record in the PR only the per-file `bad_metrics=` and `sourced=x/y`
     counts and the two `== ollama_chat/qwen3:14b` lines.
   - If bad metrics do not fall, or `sourced` drops, or the example is
     copied (unsourced outcomes rise): change the example wording, not the
     code, and update decision 1 in this plan in the same commit.

   → verify by both runs exiting 0, printing only count lines, and
   `git status` clean after the revert.

   Traps: local `ollama_chat/qwen3:14b` only; metrics only, never names,
   values or quotes in the PR or chat; never send document text to a
   third-party model (Claude included); `--docs` outside the repo; no
   `destination` in `EXTRACT_MODEL_OPTIONS`; never read the
   Synechron-presale-doc folder into the session.

## Tests

- `docker compose build app && docker compose run --rm app pytest` → all pass,
  including the new `test_vague_metric`, the extract note cases, the render
  and search empty-metric cases, `bad_metrics=0` in the eval script test,
  and `tests/test_seed_demo.py` unchanged.
- Step 5 one-off check prints `ok`.
- Step 7: bad metrics after < before, `sourced` no lower.

## Coder handoff

Steps 1-5 edit files (11 files in all), so steps 1-6 go to one `coder`
agent: start it with this plan path and step 1, send steps 2-6 to the same
agent with `SendMessage`. The session model runs step 7 and reviews with a
fresh `opus` agent given only this plan and `git diff`. The PR says which
steps the coder did.

## Rollback

Revert the branch's implementation commits (`git revert <sha>…`). No
schema migration, no stored data rewritten: cases already extracted keep
their notes until re-extracted, and a demo volume seeded with the new names
keeps them until `scripts/demo.sh reset`.
