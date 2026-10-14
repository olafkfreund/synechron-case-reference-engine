---
status: approved
issue: 260
intent: intent/2026-10-14-260-sentence-start-flags.md
---

# Spec: Stop flagging ordinary sentence-opening words as names

## Design

**Where.** One change, in the sentence-start branch of `check_prose`
(`app/writeup.py:66-69`). Today that branch drops the opening word only if
it is in `COMMON_STARTS`, or if `heading=True`. After the change it also
drops the word when **either** of these holds:

1. **It is used in lowercase elsewhere.** Its casefolded form appears as a
   lowercase whole word in the case's quotes (`case_quotes`, before
   `_norm`) or in `text` itself. For example, "Need" is dropped because
   "need" appears in lowercase. A product name almost never does: no
   "kafka", no "servicenow".
2. **It is an ordinary opener.** `schema._opener(word)` is true: the
   existing `OPENERS` list, with its suffix stemming ("Defining" →
   "define"), already used by the architecture checks
   (`app/schema.py:420-431`). The prose check then shares one list with
   the architecture checks instead of keeping two.

**What is unchanged:**
- The existing exception stays: an all-caps word of two or more letters
  (an acronym) is always checked, and so is a word containing a digit.
- Only the **first word** of the term is dropped. The rest of a
  multi-word term is still checked: "Define Azure Data Factory" still
  checks "Azure Data Factory".
- Mid-sentence terms, the number check and the price check are untouched.
- `heading=True` keeps its current behaviour.

**Callers.** No signature change. `brief.py`, `review.py`, `extract.py`,
`writeup.py` and `schema._prose` all benefit automatically. `schema._prose`
already strips an opener before calling `check_prose`; that becomes
redundant but harmless, and is left alone.

**Existing cases.** Flags are stored per prose section, and are recomputed
the next time that section is checked: a reviewer save, a re-extraction,
or `extract.py:286` when the document changes. There is no bulk job,
matching the intent.

## Alternatives rejected

- **Growing `COMMON_STARTS` by hand.** That is what produced this bug. Each
  new document brings new opening verbs.
- **A bundled English word list.** Adds a data file and still misses domain
  words. The lowercase evidence comes from the document itself.
- **A part-of-speech model (for example spaCy).** A new heavy dependency
  for one check.
- **Never checking sentence-opening words.** "Kafka streams …" at a
  sentence start must still be checked.

## Risks

- **A name that also appears in lowercase** in the quotes or text, for
  example a product called "Atlas" next to "atlas" used as an ordinary
  word, would pass at a sentence start. This is narrow: the same word must
  appear in lowercase in this document. Mid-sentence uses are still
  checked.
- **Shared code.** The brief check uses `check_prose` too, and gets the
  same relief. Its tests must stay green.

## Verification

- **Unit tests in `tests/test_writeup.py`:**
  - "Need to update …" with "need" in a quote: no flag
  - "Amend the plan …" with no lowercase "amend" anywhere, but `_opener`
    true via stemming: no flag (if it is in `OPENERS`). Otherwise it is
    still flagged; the test pins whichever is true.
  - "Kafka streams …" with no Kafka in the quotes: still flagged
  - "SAP runs …": still flagged
  - "Define Azure Data Factory pipelines" with no "Azure Data Factory"
    quoted: "Azure Data Factory" is flagged, "Define" is not
  - Each test is negative-controlled once.
- **Existing tests:** `tests/test_writeup.py`, `tests/test_brief*.py`,
  `tests/test_schema.py` and `tests/test_review*.py` stay green.
- **Measured.** `scripts/eval_extraction.py` on the demo documents and on
  the presale documents, with local `qwen3:14b`, temperature 0, comparing
  main against the branch:
  - Report the total prose name-flags, and how many disappear. Counts
    only: no text and no file names (masked doc1…docN).
  - The eval output records **"names '…'"** flag counts per document. If
    `eval_extraction` doesn't count them yet, add a `name_flags=` counter,
    following how `flag_kinds` was added in #246.
- **Reviews:** Opus and Codex, read-only.
- **Full suite:**
  `docker compose build app && docker compose run --rm app pytest`.
