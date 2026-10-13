---
status: approved
issue: 24
spec: spec/2026-10-13-24-recall-gate-top20.md
---

# Plan: The recall gate measures what the AI can pick from

Branch `feat/24-recall-gate-top20`.

## Approved decisions

- `scripts/recall.py --min` (default 0.90) checks the **top-20** hit rate. The
  exit code is 0 at or above it, 1 below it, and 2 when there is nothing to
  score.
- Top-3 is still printed, and the MISS lines still list every top-3 miss,
  marked "in top 20 only" or "not in top 20".
- No second threshold and no `--gate` flag.
- Rollout rule: top-20 ≥ 90% before org rollout. Below that, add `pgvector` on
  `summary` and re-measure.

## Steps

1. **`scripts/recall.py`: gate on top-20.**
   - Docstring `:1-7`: "Exit code: 0 at or above --min (top-20 hit rate)…".
     Add one line: "Top-20 is the gate: it is what the AI picks from. Top-3,
     what users see first, is reported (#24)."
   - `:42`: `help="minimum top-20 hit rate (the gate)"`.
   - `:57`: the summary ends `(gate: top-20 ≥ {args.min:.0%})`.
   - `:61`: `return 0 if top20 >= args.min else 1`.
   → verify: `grep -n "top3 >= args.min" scripts/recall.py` prints nothing.
   Traps: leave the MISS loop (`:58-60`) and exit code 2 as they are.

2. **`tests/test_hardening.py`: prove the gate moved.**
   - In `test_recall_script_scores_hits_and_misses` (`:160`), add
     `and "gate: top-20" in out` to the first summary assertion.
   - Add `test_recall_gate_is_top20` after it. Use
     `monkeypatch.setattr(recall, "evaluate", lambda q, u: [{"id": "q1", "top3": False, "top20": True, "expected": ["x"], "returned": 20}])`
     and a one-entry YAML in `tmp_path`. Assert `recall.main([...]) == 0` and
     that the output has `top-3: 0%`, `top-20: 100%` and
     `MISS q1: expected ['x'], in top 20 only`.
   → verify: `docker compose run --rm app pytest tests/test_hardening.py -k recall` passes.
   Traps: the existing sample miss is outside the top 20 too, so its
   `== 1` assertion still holds. Do not change it.

3. **`tests/recall.yaml:14`: update the comment.** It becomes
   "Below 90% top-20: add pgvector on `summary`."
   → verify: `grep -n "top-3" tests/recall.yaml` prints nothing.
   Traps: none.

4. **`plan/2026-10-06-1-reference-engine.md`: record the decision.**
   - `:19`: "if top-20 recall on the 50-query set is < 90% (#24)".
   - `:791`: "to report top-3 and top-20 hit rates".
   - `:795`: "verify top-20 recall ≥ 90% before org rollout (#24: top-20 is
     what the AI picks from)".
   - `:839`: "reports ≥ 0.90 top-20 recall".
   → verify: `grep -n "top-3 recall" plan/2026-10-06-1-reference-engine.md`
   prints nothing.
   Traps: do not rewrite other steps.
   Deviation (review): step 22's Done note "Gate as written: top-3 ≥ 90%…"
   (`:806`) contradicted the new rule, so it now says it was superseded by #24.

## Tests

```bash
docker compose run --rm app pytest tests/test_hardening.py
docker compose run --rm app pytest
```

Both should pass, with no AWS or Bedrock involved.

## Rollback

Revert the branch's commits. The script and docs go back to top-3, and no data
is involved.
