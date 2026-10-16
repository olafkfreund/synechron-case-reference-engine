---
status: approved
issue: 123
intent: intent/2026-10-10-123-pptx-omitted-note.md
---

# Spec: PowerPoint output drops the "statements omitted" note on the industry slide

## Design

The approved outcome: the PowerPoint industry slide shows the note from
`industry_section`, in both layouts, with no open questions.

Both branches of `to_pptx` (`app/render.py:200-208`) fill their statements box
from `slide_statements(industry)` (`:74-78`). That is the dedicated layout's
`"Statements"` (`:202`) and the fallback's `"Outcomes"` (`:207`), and nothing
else calls `slide_statements`. So the note is added in that one function, and
both layouts get it:

```python
def slide_statements(ind: dict) -> list[str]:
    """... The omitted-statements note (if any) comes last, as in Word and Markdown (#123)."""
    return [f"{i['phrase']}: ..." for i in ind["statements"]][:SLIDE_ITEMS] + ([ind["note"]] if ind["note"] else [])
```

- **It comes after the slice.** The `SLIDE_ITEMS` limit of 5 never cuts the
  note off.
- **The text is reused.** It is `industry["note"]`, built at `:66-67`, and no
  new wording is added.
- **No change to the master template or the placeholders.**

## Alternatives rejected

- **Add the note in each branch of `to_pptx`.** That is two edits that can
  drift apart, and `slide_statements` is the single place both branches use.
- **Put the note in the disclaimer (`Summary`) box.** It would mix a fixed
  legal sentence with a per-output count, and Word and Markdown show it after
  the statements.

## Risks

- **One more paragraph in the box.** On the slide that is at most 6 lines
  instead of 5, which is within the box the master gives.
- **Nothing else changes.** When nothing is omitted, `note` is `""`, so the
  slide is the same as today.
- **No host impact.**

## Verification

Tests go in `tests/test_industry.py`, using its `rows`, `approved`, `reg`,
`gen` and `slides` helpers:

- `test_pptx_industry_slide_shows_the_omitted_note`: one claim names the
  protected client (`reg`), plus one normal claim, with a case included.
  - The last line of the `Statements` box on the industry slide is
    "1 public statement omitted: it named a protected client".
  - The normal claim's line is still the first line.
  - Fails on main.
- The same check with the fallback layout: the master's industry layout is
  renamed, as in `test_fallback_to_case_layout_without_industry_layout`. The
  note is the last line of `Outcomes`. Fails on main.
- The full suite passes.
