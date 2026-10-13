---
status: draft
issue: 24
intent: intent/2026-10-13-24-recall-gate-top20.md
---

# Spec: The recall gate measures what the AI can pick from

## Design

The gate becomes the top-20 hit rate. Top-3 stays in the report.

- **`scripts/recall.py:42`**: the `--min` help text becomes "minimum top-20
  hit rate (the gate)".
- **`scripts/recall.py:57`**: the summary line reads
  `queries: N  top-3: X%  top-20: Y%  (gate: top-20 ≥ Z%)`.
- **`scripts/recall.py:58-60`**: MISS lines are unchanged. They still list
  every top-3 miss and say "in top 20 only" (ranking) or "not in top 20"
  (retrieval, which counts against the gate).
- **`scripts/recall.py:61`**: `return 0 if top20 >= args.min else 1`.
- **Module docstring, `:1-7`**: "Exit code: 0 at or above --min (top-20 hit
  rate)…", plus one line on why: top-20 is what the AI picks from, and top-3 is
  reported for ranking.
- **`tests/recall.yaml:14`**: the comment becomes "Below 90% top-20: add
  pgvector on `summary`."
- **Plan** `plan/2026-10-06-1-reference-engine.md`: `:19`, `:791`, `:795` and
  `:839` say top-20 ≥ 90% (#24) where they say top-3 or plain "recall".

Exit code 2 (nothing to score) and the flags are unchanged.

## Alternatives rejected

- **Gate on both** (top-20 ≥ 90% and a lower top-3 bar). That adds a second
  threshold nobody has a basis for yet. Top-3 is still printed, so a bar can be
  added once real numbers exist.
- **Add a `--gate top3|top20` flag.** It's flexibility nobody asked for.
  `--min` stays the only knob.

## Risks

- **A poor top-3 with a good top-20 now passes.** The UI's first page could
  rank badly while the gate is green. This is accepted: the report still shows
  top-3 and the MISS lines, and the AI pick re-ranks.
- Nothing else reads the exit code: the gate (#35) is run by hand.

## Verification

- `tests/test_hardening.py::test_recall_script_scores_hits_and_misses` stays
  green. Its sample misses both top-3 and top-20 (67% < 90% → 1), and it now
  also asserts the summary shows `gate: top-20`.
- A new test monkeypatches `recall.evaluate` to return one query with
  `top3=False, top20=True`. `main()` returns 0, and the output has
  `MISS … in top 20 only`. This proves the gate moved.
- `docker compose run --rm app pytest tests/test_hardening.py` passes.
