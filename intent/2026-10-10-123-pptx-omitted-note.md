---
status: draft
issue: 123
author: olafkfreund
---

# Intent: PowerPoint output drops the "statements omitted" note on the industry slide

## Problem

`render.industry_section` (`app/render.py:48-67`) drops each research claim
that names a protected client, and builds a note such as "1 public statement
omitted: it named a protected client". The Word and Markdown outputs print
this note. `to_pptx` (`:199-208`) never reads `industry["note"]`, in either the
dedicated-layout branch or the fallback branch.

When every claim was dropped, the industry slide shows its heading and
disclaimer and an empty statements box, with nothing to say why. When only
some claims were dropped, the reader can't tell anything is missing.
`tests/test_industry.py` checks the note in the DOCX and Markdown outputs only.

## Proposed outcome

The PowerPoint industry slide shows the same omitted-statements note as Word
and Markdown, in both layouts. So an empty or shortened slide explains itself.

## Affected users and systems

- Bid team users who download PowerPoint or PDF slides: `app/render.py`
  (`to_pptx`).
- Tests: `tests/test_industry.py`.
- Not affected: the content of Word and Markdown outputs, and the filtering
  itself.

## Constraints

- Use the note text already built by `industry_section`. Don't add new
  wording.
- Use the existing placeholders in the marketing master. No template change.

## Open questions

None. The note goes after the statements in the same box, in both branches.
