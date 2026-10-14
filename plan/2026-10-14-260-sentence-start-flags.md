---
status: approved
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

## Revision 2026-10-14 (spec revision, approved by the user)

- **Step 1b.**
  - Add `app/data/common_words.txt`, built from nixpkgs `scowl`
    2020.12.07: `{english,american,british}-words.{10,20,35}`, latin1
    converted to UTF-8, `^[a-z][a-z-]*$` entries only, sorted and unique.
  - Add `app/data/common_words.LICENSE`, the SCOWL copyright notice.
  - In `writeup.py`, an `lru_cache` loader puts the list in a frozenset,
    and `ordinary` also checks `first.lower() in words()`.
  - → verify: the step 2 tests, plus "Need …", "Amend …", "Append …" and
    "Define …" with **no** lowercase evidence give no flag. "Kafka …" and
    "Okta …" are still flagged. "They chose Snowflake." mid-sentence is
    still flagged.
- **Step 4, replaced.** Re-run check_prose, old and new, over the stored
  prose of the demo database, counts only (`scratchpad/m260`). Expected:
  name flags drop from 7 to the true unknowns only.

## Deviations

- **Step 4 on the eval documents measured nothing.** `eval_extraction`
  writes no prose, so both runs gave 0 flags. Following the revision,
  check_prose was re-run over the stored prose instead: 16 cases and 63
  sections in the demo database, counts only.

  | Version | Name flags |
  |---|---|
  | Before | 7 |
  | First design (lowercase evidence) | 6 |
  | With the word list | 1 |

  The remaining flag is an invented product name, which is correct.
- **Existing assertions updated** for the accepted risk: "Ledgers moved."
  and "Phase 2 began." no longer flag the opening word. The
  "no self-vouching" test now uses a non-word, "Zorbulate". The Snowflake
  mid-sentence test uses a quote without "Snowflake".
- **Negative control:** without the word list, the 2 new tests fail.
- **Opus review fixes** (full suite 1431 passed):
  - **Digit words.** A word containing a digit is always checked, as
    decision 2 says ("Plan9" is checked).
  - **No stemming on prose.** `_opener` is no longer used for prose. Its
    loose stemming let coined names such as "Trackly" through. The word
    list covers inflections instead, so the list moved to SCOWL **size
    40**: 45,123 words, which include "deploying", while "trackly",
    "paypal", "kafka", "okta" and "databricks" are absent.
  - **Quotes only.** Lowercase evidence comes from the quotes only, so the
    prose cannot vouch for its own names (for example "bids@globex.com").
  - **Counter removed.** The `name_flags` counter is gone from
    `eval_extraction`; it measured nothing there.
  - **Accepted risk, listed.** Common-word product names at a sentence
    start are no longer flagged: Snowflake, Azure, Oracle, Spark, Slack,
    Teams, Python, Sage, Zoom, Dynamics, Outlook and Apple. They are still
    flagged mid-sentence.
- **Codex** (gpt-6-sol): no findings.
