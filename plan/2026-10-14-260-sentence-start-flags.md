---
status: draft
issue: 260
spec: spec/2026-10-14-260-sentence-start-flags.md
---

# Plan: Stop flagging ordinary sentence-opening words as names

Worktree `../refs-engine-260`, branch `fix/260-sentence-start-flags`, based
on main. One PR. Small enough that the session implements it itself: two
code files and one test file.

## Approved decisions (from the spec)

1. **The change.** In `check_prose`'s sentence-start branch
   (`app/writeup.py:66-69`), the opening word is also dropped when either
   holds:
   - **(a)** its casefold appears as a lowercase whole word in the raw
     `case_quotes` or in `text`
   - **(b)** `schema._opener(word)` is true
2. **The exception stays.** An acronym (all caps, two or more letters) or a
   word containing a digit is always checked. Only the first word is
   dropped; the rest of the term is still checked.
3. **Nothing else changes:** mid-sentence terms, numbers, prices,
   `heading=True` and every signature.
4. **No bulk recompute.** Flags refresh on the next check of a section.
5. **Measure** name flags before and after with `eval_extraction`, counts
   only.

## Steps

1. **`app/writeup.py:52-77`.**
   - Build `lower = set(re.findall(r"\b[a-z][a-z-]*\b", " ".join(case_quotes) + " " + text))`
     once per call. These are lowercase-only tokens, so a capitalised "Need"
     never counts as evidence for itself.
   - Extend the drop condition:
     `(heading or first.lower() in COMMON_STARTS or first.lower() in lower or _opener(first))`,
     keeping the existing `and not (first.isupper() and len(first) > 1)`.
   - Import `_opener` lazily from `app.schema` inside the function, since
     `schema` imports `writeup` lazily too (`app/schema.py:427,470`), as
     the existing import of `COMMERCIAL` does.
   - → verify: `pytest tests/test_writeup.py tests/test_schema.py tests/test_brief*.py tests/test_review*.py`.
   - Traps:
     - `tests/test_writeup.py:31`
       (`..._unless_acronym_digit_or_uncommon`) pins that an uncommon
       opener is flagged. Keep that true when the word does **not** appear
       in lowercase anywhere and is not an opener. Adjust the fixture only
       if its word now appears in lowercase, and say so.
     - The digit check: `"Need2"` must still be flagged.
2. **Tests in `tests/test_writeup.py`**, the cases from the spec:
   - "Need …" with "need" in a quote: no flag
   - an opener via stemming ("Defining …"): no flag
   - "Kafka streams …": flagged
   - "SAP runs …": flagged
   - "Define Azure Data Factory …": only "Azure Data Factory" is flagged
   - "Need …" where the only "need" is the capitalised one: still flagged,
     so a word can't vouch for itself

   Negative control: revert step 1, and the new no-flag tests fail.
3. **`scripts/eval_extraction.py:121`.** Add `name_flags=` (the count of
   prose flags starting `names '`) to the printed line. It is a count only.
4. **Measure.** Run `eval_extraction` on main and on the branch:
   - **Documents:** the demo documents, then the presale documents.
   - **Setup:** local `qwen3:14b` at `http://localhost:11434`,
     `--network host`, temperature 0.
   - **Privacy:** write to a private file (`umask 077`), mask file names
     with awk as doc1…docN, and report only the `name_flags` and
     `flagged` totals before and after.
   - **Expected:** `name_flags` drops, and nothing else in the counts
     changes. Extraction is deterministic, so any other difference is
     investigated before merge.
5. **Full suite, then the reviews and the PR.** Run
   `docker compose build app && docker compose run --rm app pytest`, then
   the Opus and Codex reviews, then the PR.

## Tests

As listed per step.

## Rollback

Revert the merge commit. Stored flags then refresh on the next check, as
they do now.

## Deviations

None yet.
