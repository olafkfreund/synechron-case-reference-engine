---
status: draft
issue: 260
author: olafkfreund
---

# Intent: Stop flagging ordinary sentence-opening words as names

## Problem

`check_prose` (`app/writeup.py:52-77`) flags any capitalised term that no
quote in the case supports. That stops the model from inventing product
names.

At the start of a sentence, every word is capitalised. The check copes with
this by dropping the first word only when it is in a hand-kept list
(`COMMON_STARTS`). Any ordinary word outside that list is flagged:

- names 'Need', which no quote supports
- names 'Amend', 'Development', 'Creation', 'Define', …

The user saw five of these on one presale case on 2026-10-14. Two things go
wrong:

- **Approval is blocked.** A flagged section blocks approval until the
  reviewer edits or confirms it.
- **Real flags get lost.** False flags bury the ones that matter, and teach
  reviewers to click through them.

## Proposed outcome

- **Ordinary words are not flagged.** A sentence-opening word is no longer
  flagged when it is ordinary English, which is recognised without a
  growing hand-kept list.
- **Names still are.** A real name at the start of a sentence is still
  checked against the quotes. "Kafka streams …" with no Kafka in the quotes
  is still flagged. So are acronyms and multi-word names ("Azure Data
  Factory").
- **Mid-sentence terms behave as today.** No change to the number or price
  checks either.
- **Existing cases.** Their flags are recomputed the next time they are
  checked (an edit or re-extraction). No bulk job is needed.

## Affected users and systems

- Reviewers: fewer false flags on the case-study text.
- `app/writeup.py` (`check_prose`), the prose and architecture checks that
  share it, and `tests/test_writeup*.py`.
- The architecture checks (`check_architecture`) use the same term rules,
  and are compared before and after.

## Constraints

- **Strictness.** No new dependency, and no weakening for mid-sentence
  names. The check must stay at least as strict for acronyms and words
  containing digits.
- **Measured.** The flag counts before and after are measured on the demo
  cases and on the presale documents (counts only, local `qwen3:14b`, as
  in earlier evals).
- **Confidentiality.** No document text or file names are printed.

## Open questions

1. **How to recognise "ordinary English".** Proposed: treat a
   sentence-opening word as ordinary when it also appears in lowercase
   anywhere in the case's quotes or the text itself. "need", "define" and
   "development" do; a product name almost never appears in lowercase.
   Alternatives are a bundled common-word list, or a part-of-speech model.
   The proposal is the smallest, and adapts to each document.
