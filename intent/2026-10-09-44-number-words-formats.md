---
status: approved
issue: 44
author: olafkfreund
---

# Intent: Number words and European number formats in source checks

## Problem

The source check compares a field's numbers with the numbers in its quote.
`numbers()` in `app/schema.py` (line 31) finds digit runs only, and drops
commas as thousands separators. That rule misreads two kinds of number that
real documents use:

- **Number words.** The quote "a team of fourteen engineers" has no digits, so
  a `team_size` of `14` fails `sourced()` (`app/schema.py` line 39) and
  `check()` in `app/extract.py` (line 79) marks the field `unsourced`.
- **European formats.** "1.200" (one thousand two hundred) keeps its dot and
  stays `1.200`, so a value of `1200` or `1,200` is flagged. "1,5 Mio." (one
  and a half) has its comma stripped and becomes `15`. That is the opposite
  failure: a value of `15` is accepted as sourced when the document never
  says 15.

Plan #1 step 6 (`plan/2026-10-06-1-reference-engine.md` line 244) records the
first two as known gaps that the reviewer resolves by hand. The `1,5` false
pass is not recorded there.

`numbers()` is also the guard in three other places, so the same misreadings
reach them:

- `ReferenceCase.summary_sourced()` (`app/schema.py` line 108): it blanks a
  summary whose numbers are absent from the quotes. It is called from
  `app/extract.py` line 119 and `app/review.py` line 54.
- `app/search.py` lines 102–108: it rejects tailored bid text and reasons
  whose numbers are not in the record.
- `app/research.py` line 345: it drops web claims whose statement numbers are
  absent from the quote.

We do not know how often this happens. The issue asks first whether the noise
justifies a change.

## Proposed outcome

- We know how many fields the extraction eval flags only because of number
  words or European formats. `scripts/eval_extraction.py` already counts
  sourced items (line 101), and the decision to convert or not is recorded
  against that number.
- If we convert: a value backed by "fourteen" or "1.200" in its quote is no
  longer flagged `unsourced`. A value of `15` is no longer accepted from the
  quote "1,5". Both cases are covered by tests in `tests/test_schema.py`.
- The guard does not get looser anywhere else. A number the document does not
  state is still flagged in fields, summaries, tailored text and research
  claims.
- If we don't convert: the gap, including the `1,5` false pass, is recorded
  where reviewers see it, and the issue is closed with the measured reason.

## Affected users and systems

- `app/schema.py` (`numbers`, `sourced`, `summary_sourced`), and through it
  `app/extract.py`, `app/review.py`, `app/search.py`, `app/research.py`.
- Reviewers: fewer false flags to clear by hand. Bid writers: what search
  lets through in tailored text.
- `tests/test_schema.py`, and possibly `tests/test_search.py` and
  `tests/test_research_claims.py`.
- No hosts, services or schema changes. Cases already stored keep their
  flags until they are re-extracted or saved in review.

## Constraints

- Confidential documents must never be sent to third-party models. The check
  stays deterministic and local, with no LLM call to read numbers.
- Test data must be public or made up. No quotes from real client documents.
- No new dependencies unless unavoidable. A word-to-number library is not
  justified for a check this small.
- It must fail closed. When a number's format is ambiguous ("1.200" or
  "1,200" could be a thousand or a decimal), it must not let an invented
  number through. A false flag is acceptable. A false pass is not.
- The guard must stay one function, so fields, summaries, search and research
  cannot diverge.
- Tests run with `docker compose build app && docker compose run --rm app pytest`.

## Open questions

1. **Convert at all?** (a) Measure with the eval first and decide on the
   numbers. (b) Convert now. (c) Close the issue as accepted noise.
   Lean: (a), but fix the `1,5` → `15` false pass whatever the outcome,
   because it is a correctness bug and not noise.
2. **Ambiguous separators.** (a) Read every number every plausible way, so
   "1.200" yields both `1.2` and `1200`, and pass if any reading matches.
   (b) Guess the locale per document. (c) Compare digit runs only, ignoring
   separators. Lean: (a) on the quote side only. It cannot invent a number
   that is not written in the document, and it needs no locale guess. (c)
   would let `1.5` match `15`.
3. **How many number words?** (a) Zero to twenty, plus tens. (b) Compounds
   like "twenty-five" and "a dozen". (c) Also "1.2 million" and "2 Mio." as
   scale words. Lean: (a) and hyphenated compounds up to ninety-nine. Defer
   scale words unless the eval shows them.
4. **Which callers get the wider reading?** Lean: all of them, through
   `numbers()`, so the four guards stay identical. The spec must show that
   search and research do not get looser.
