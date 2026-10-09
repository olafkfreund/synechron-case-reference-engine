---
status: approved
issue: 99
intent: intent/2026-10-09-99-strip-edit-metric.md
---

# Spec: Trim every edited text field in the reviewer edit

## Design

One change, in `edit` (`app/review.py:161-211`): trim the five text inputs once, before any branch reads them.

```python
value, metric, start, end, quote = (s.strip() if s is not None else None for s in (value, metric, start, end, quote))
```

Place it at the top of the function body, before `with db.connect()` (`:165`). `None` stays `None`,
because an absent form field means "leave unchanged" (`:178`, `:196`, `:203`). Every branch then sees
trimmed text, and a blank input takes the existing empty paths with no other edits:

| Field | Line | Whitespace-only input now stores |
| ----- | ---- | -------------------------------- |
| summary | `:174` `value or ""` | `""` |
| text scalar | `:179` `value or None` | `None` |
| integer scalar | `:179` | `None` (already stripped, unchanged) |
| added list item / outcome | `:181-183` | refused with 400 as now; a valid add is stored trimmed |
| edited outcome | `:195` `metric or "", value or ""` | `""` |
| edited list item | `:197` `value or None` | `None` |
| period | `:200` `start or None, end or None` | `None` |
| quote | `:204` | `""` |

Effects:

- A metric of `"  "` is stored as `""`. `app/render.py:92` and `app/search.py:133` test `if o.metric`,
  so they print the value alone. The search text (`app/search.py:45`) loses the padding.
- The add-row check at `:181` keeps its `.strip()` calls; they are now redundant but harmless, and
  removing them is not part of this issue.
- **Sourced gap closed.** Today a scalar value `"  "` with a quote that is in the document passes
  `check()`: `value not in (None, "")` (`app/extract.py:95`) is true, and `sourced` (`app/schema.py:40-46`)
  passes because `_norm("  ")` (`app/schema.py:19-20`) is `""`, a substring of every quote. The blank
  value is then kept on approve (`app/review.py:222-225`). After the trim the value is `None`, which
  `check()` does not flag and approve stores as empty.

### `check()` is not changed

The task asked whether `check()` (`app/extract.py:80-112`) should also treat a whitespace-only value
as empty, as defence in depth for extraction. **No.** `check()` only sets `unsourced`; it never
changes a value. Treating `"  "` as empty would set `unsourced = False`, so approve would *keep*
the blank value. Today, a blank value whose quote is not in the document is flagged and dropped on
approval. The change would make `check()` less strict and would not remove a single blank value.
The real defence is to trim where values enter (here, and in `assemble` for extraction). There is no
report of the model returning whitespace-only values, so the extraction side is left out until one
appears.

## Alternatives rejected

- **Trim only the outcome metric and value** (`:183`, `:195`), as the issue literally asks: two
  edits instead of one, and it leaves the same bug in the fields beside them and the sourced gap open
  (intent option 1, not chosen at approval).
- **Strip in the Pydantic models** (`app/schema.py`, `str_strip_whitespace`): also changes extraction
  and every stored case on load. It is outside the approved scope (`edit` only) and harder to review.
- **Treat whitespace as empty in `check()`**: see above. It hides blank values instead of removing them.
- **Migrate stored values**: not asked for, and padded values fix themselves on the next edit.

## Risks

- **A quote whose meaning depends on outer spaces:** none. `quote_in` and `sourced` compare through
  `_norm`, which already drops outer whitespace and collapses inner whitespace, so trimming cannot
  change whether a quote matches. No field gives outer spaces a meaning.
- **Inner text** is unchanged: only `str.strip()` is applied.
- **Stored values are not migrated.** Cases saved with padded values keep them until a reviewer edits
  that field. Outputs for those cases are unchanged by this fix.
- **Hosts:** the app container only, through a normal deploy. No schema, template or extraction change.

## Verification

New tests in `tests/test_review.py`, beside `test_edit_with_bad_quote_is_unsourced_whatever_the_form_says`
(`:102`), using the existing `make`, `client(R)`, `post`, `row` and `ver` helpers:

1. **Whitespace metric is stored empty and renders the value only.** Edit `outcomes.0` with
   `metric="   "`, `value=" 30% "` and a document quote. Assert the stored outcome has `metric == ""`
   and `value == "30%"`, and that the rendered output (`app/render.py`) and search text contain
   `30%` and not `": 30%"`.
2. **Whitespace value is not sourced and is dropped on approval.** Edit `industry` with
   `value="   "` and a quote that is in the document (`"for a large retail bank in the United Kingdom"`).
   Assert the stored `industry.value is None` and `unsourced is False`. Approve, and assert
   `industry.value is None` in the approved case. Before the fix this test fails: the value is `"   "`
   and survives approval.
3. A padded add (`outcomes.new`, `metric=" Revenue "`) is stored as `"Revenue"`.

Then the existing suite passes unchanged (`pytest tests/test_review.py`, then the full run), which
covers the 400 refusals at `:181` and the summary re-validation at `:205`.
