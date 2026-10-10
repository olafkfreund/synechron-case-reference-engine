---
status: approved
issue: 140
spec: spec/2026-10-10-140-merge-prefers-sourced.md
---

# Plan: Merging prefers a member's sourced copy over an unsourced one

Approved decisions (from the spec):

- Everything changes in `combine` in `app/review.py`. `do_merge` and
  `merge_preview` are unchanged.
- **Scalars and period with no pick.** The default member is the first one
  whose copy of the field is not `unsourced`. If every copy is unsourced, it
  falls back to `members[0]`, so the field stays unsourced (fail closed). An
  explicit `pick[f]` still wins.
- **Capabilities and tech.** De-duplication keeps first-seen order. When the
  kept copy is unsourced and a later duplicate is sourced, the sourced copy
  (stamped with its own `document_id`) replaces the kept copy in the same
  position.
- `do_merge` still runs `check()` on the combined case, so the kept copy is
  re-verified.

Two steps edit two files, so I implement this myself (below the coder
threshold).

## Steps

1. `app/review.py:278-288` (`combine`): change the field and de-duplication
   loops. → verify by `python -c "import app.review"` in step 2's run.
   ```python
   for f in MERGE_FIELDS:
       default = next((cid for cid, _, c in members if not getattr(c, f).unsourced), members[0][0])
       doc, case = by_id[pick.get(f, default)]
       out[f] = stamped(getattr(case, f), doc)
   for f in ("capabilities", "tech_stack"):
       seen, items = {}, []  # casefolded value -> index in items
       for _, doc, case in members:
           for i in getattr(case, f):
               k = (i.value or "").casefold()
               if k not in seen:
                   seen[k] = len(items)
                   items.append(stamped(i, doc))
               elif items[seen[k]].unsourced and not i.unsourced:
                   items[seen[k]] = stamped(i, doc)  # same position, the sourced copy's origin
       out[f] = items
   ```
   Also update the docstring: scalars come from "the first sourced member
   unless picked".
   Traps:
   - Every `MERGE_FIELDS` value has `.unsourced`. Period does too: `approve`
     reads `case.period.unsourced` at `app/review.py:232`.
   - Don't touch `merge_preview`.

2. `tests/test_review.py`: add two pure tests after
   `test_merge_combines_checked_fields`. They build `ReferenceCase` objects
   directly, using `Sourced` from `app.schema`, so no DB is needed.
   - `test_combine_prefers_sourced_scalar`:
     - A's title is `Sourced(value="Same", source_quote="q", unsourced=True)`
       and B's is the same but sourced. Members are `[(1, 10, A), (2, 20, B)]`.
     - `combine(members, {})`: the title is not unsourced and its
       `document_id == 20`.
     - `combine(members, {"title": 1})`: the title is unsourced, with
       `document_id == 10`.
     - With both copies unsourced: unsourced, with `document_id == 10`.
   - `test_combine_dedupe_keeps_sourced_copy`:
     - A has `capabilities=[Sourced(value="Onboarding", source_quote="q"),
       Sourced(value="Payments", source_quote="q", unsourced=True)]`.
     - B has `[Sourced(value="payments", source_quote="q")]`.
     - `[x.value.casefold() for x in caps] == ["onboarding", "payments"]`:
       one item, in A's position. The kept copy is B's, spelling included.
     - `caps[1]` is not unsourced and has `document_id == 20`.

   → verify by
   `docker compose build app && docker compose run --rm app timeout 900 pytest -q -p no:cacheprovider tests/test_review.py -k "combine"`.
   Both fail on main (`check.sh`).

   Traps:
   - There is no bind mount, so always build first.
   - Never run `docker compose up` or `down`.
   - The repo is public: use made-up names only.
   - `ReferenceCase` may require fields beyond `title`. Build the members
     from `case_data()` (`ReferenceCase.model_validate_json(case_data(...))`),
     which fills the required fields.

## Tests

- `docker compose build app && docker compose run --rm app timeout 900 pytest -q -p no:cacheprovider`:
  the full suite passes, including `test_merge_combines_checked_fields`
  (order and origins unchanged, since all of its copies are sourced).
- The two new tests fail on main's `app/review.py`.

## Rollback

Revert the step commits. There is no schema or data change, and merged cases
already written are not affected.
