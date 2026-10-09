---
status: draft
issue: 44
intent: intent/2026-10-09-44-number-words-formats.md
---

# Spec: Number words and European number formats in source checks

## Design

The approved intent fixes four decisions:

1. Fix the "1,5" → `15` false pass, whatever the measurement shows.
2. Read each number in a quote every plausible way, and nothing else.
3. Number words zero to ninety-nine.
4. Every caller goes through `numbers()`, and no guard gets looser.

### One function, two sides

`numbers()` in `app/schema.py` (line 31) gets one keyword argument:
`numbers(s, quote=False)`. Every guard asks the same question: is each number
in a *claim* (a field value, summary, tailored text or research statement)
among the numbers of its *source* (a quote or the case facts)? So:

- **Claim side** (`quote=False`, the default): each digit token gives exactly
  one canonical reading. Words are not read.
- **Source side** (`quote=True`): each digit token gives every plausible
  reading, and the words zero to ninety-nine are read too.

The claim side never gains readings, so a claim cannot need fewer numbers than
it does today. Only the source side widens, and only by readings of what the
document actually wrote.

### Reading a digit token

Tokens keep today's regex, `\d+(?:[.,]\d+)*`. Each reading is a `Decimal`
written in plain form, with no exponent and no trailing zeros: "1,200" →
`1200`, "3.50" → `3.5`, "007" → `7`. "Thousands" below means every group after
the first has exactly three digits.

| Token shape | Claim side (one reading) | Source side (all readings) |
| --- | --- | --- |
| digits only: `14` | `14` | `14` |
| one `,` + 3 digits: `1,200` | `1200` | `1200`, `1.2` |
| one `,` otherwise: `1,5` | `1.5` | `1.5` |
| one `.` + 3 digits: `1.200` | `1.2` | `1.2`, `1200` |
| one `.` otherwise: `3.5` | `3.5` | `3.5` |
| repeated, all thousands: `1.200.000`, `1,200,000` | `1200000` | `1200000` |
| mixed, the last one is decimal: `1.234,5`, `1,234.5` | `1234.5` | `1234.5` |
| anything else: `1,2,3`, `1.23.4` | the raw token | the raw token |

The claim side reads English first, because the extraction and drafting
prompts are English. A raw token only matches the same raw token, so an
unreadable shape fails closed.

### Words (source side only)

`zero` to `nineteen`, the tens `twenty` to `ninety`, and the compounds
`twenty-five` or `twenty five`. Matching is case-insensitive on whole words
after `_FOLD`, so typographic dashes in compounds are already folded. This is
a small table in `app/schema.py`, about 15 lines. Scale words ("million",
"Mio.", "dozen") are out of scope, as the intent decided.

### Callers

| Caller | Claim side | Source side | Effect |
| --- | --- | --- | --- |
| `sourced()`, `app/schema.py:39` | field value | its quote, `quote=True` | the target fix |
| `summary_sourced()`, `app/schema.py:110` | summary | quotes, `quote=True` | same as fields |
| `app/search.py:102-108` | tailored text, reason | `facts()`, **claim side** | canonical form only |
| `app/research.py:345` | statement | page quote, `quote=True` | same as fields |

Search builds its `allowed` set from `facts()`. Those are values, not quotes,
so they stay on the claim side. Search therefore gains no readings. It only
changes where canonical forms differ: "1,5" in a fact now allows `1.5`
instead of `15`.

`extract.check()` (`app/extract.py:79`), `review.fix_summary()`
(`app/review.py:53`) and the period check (`app/extract.py:98`, its own
`\d{4}` regex) need no change.

### Why no guard gets looser

Compared with today, a claim number can newly pass only when it equals a
reading of a token in the source. The complete list of new passes:

1. `1200` against "1.200": the target.
2. `14` against "fourteen": the target.
3. `1.5` against "1,5", and `3.5` against "3.50": the same number.
4. `1.2` against "1,200", and `1200` against "1.200" read the other way. This
   is the one real widening. See Risks.

Newly rejected: `15` against "1,5" (the bug), and `15` against "1,5" in search
facts. Every other pair behaves as today. A unit test pins each row above.

### Measurement

The extraction eval measures how much noise this removes. It runs on local
models only, and the user runs it on the user's documents:

```
docker compose run --rm --network host app \
  python scripts/eval_extraction.py --docs <dir outside the repo> --models ollama_chat/qwen3:14b
```

Run it once on `main` and once on the branch, and compare the `sourced=x/y`
counts. The script already prints metrics only (`scripts/eval_extraction.py`
lines 1-9). It refuses a document directory inside the repo, and its data
policy refuses third-party models on confidential documents. So no document
text, quote or value leaves the machine or gets written down. Only the two
`== qwen3:14b` summary lines and the per-file `sourced=` counts go into the
PR, with file names truncated as the script does.

Status: the local Ollama is reachable and has `qwen3:14b`. No document
directory is known to the agent, so **the run is deferred to the user**, as
the last step of the plan. If the branch gains no sourced items, the PR keeps
the `1,5` fix and the canonical form, and drops the source-side readings and
words.

## Alternatives rejected

- **Guess a locale per document.** It needs a heuristic that will be wrong on
  mixed documents (an English case study quoting a German KPI). A wrong guess
  is a silent false pass.
- **Compare digits only and ignore separators.** It would let `15` match
  "1.5" and "1,5", which is the bug we are fixing, made general.
- **Multiple readings on the claim side too.** A claim "1,200" would pass if
  *any* of its readings were in the source. That is looser than today.
- **A library** (`word2number`, `babel`, `num2words`). It is a new dependency
  for about 15 lines of table, and the repo rules forbid it unless
  unavoidable.
- **Ask the LLM to normalise numbers.** Confidential text would go to a model
  to vouch for the model's own output. That breaks the rule that `unsourced`
  comes from the document alone (`app/extract.py:79`).
- **Read words on the claim side.** It would blank summaries and reject
  tailored text that say "one of the largest banks", because the pronoun
  "one" would need a source. That is new false flags with no approved need.
  The existing gap stays as it is: a summary can say "fifteen" unchecked. It
  is no looser than today.
- **Separate functions per side.** The intent requires one function, so the
  four guards cannot drift apart.

## Risks

- **Factor-1000 ambiguity (new pass 4).** If a quote says "1,200" (English)
  and the model writes `1.2`, it now passes. The same goes for a German
  "1.200" read as `1.2`. The quote must still be verbatim in the document,
  with at least 4 words (`MIN_QUOTE_WORDS`). The reviewer sees value and quote
  side by side, so a 1000× mismatch is visible there. Accepting this is the
  approved choice (a). It is listed so the approver accepts it knowingly.
- **"one" as a pronoun** in a source quote ("one of the bank's teams") lets
  the claim number `1` through. The effect is small: a `1` that the quote
  arguably does not state.
- **Stored cases** keep their current `unsourced` flags until they are
  re-extracted or saved in review. There is no migration, and none was asked
  for.
- **Search notes.** Tailored text whose numbers were allowed only through the
  old `1,5` → `15` reading is now replaced by the approved summary, with the
  existing note (`app/search.py:106`). That is intended.
- No hosts, services, schema or dependencies change.

## Verification

- `tests/test_schema.py`:
  - a table test of `numbers()` on both sides, covering every row of the
    reading table, including words, compounds and raw tokens;
  - `sourced()` for "a team of fourteen engineers" with `14` → sourced;
  - "1.200 users" with `1200` → sourced;
  - "1,5 Mio. EUR saved per year" with `15` → unsourced;
  - "1,200 users onboarded" with `1.2` → sourced, pinned as the known
    widening;
  - `summary_sourced()` with a word-only quote.
- The existing `tests/test_search.py` (including line 80) and
  `tests/test_research_claims.py` pass unchanged. That shows those guards did
  not widen. Add one search test showing that a fact "1,5" no longer allows
  `15`.
- All test text is made up.
- `docker compose build app && docker compose run --rm app pytest` is green.
- The measurement above, run by the user, with the metric lines recorded in
  the PR.
