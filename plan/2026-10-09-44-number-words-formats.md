---
status: draft
issue: 44
spec: spec/2026-10-09-44-number-words-formats.md
---

# Plan: Number words and European number formats in source checks

Branch: `feat/44-number-words-formats`. Intent and spec are approved.

## Approved decisions

The plan carries these over from the spec, so it can be implemented without
opening the spec.

1. **One function, two sides.** `numbers(s, quote=False)` in `app/schema.py`.
   - The claim side (the default) covers field values, summaries, tailored
     text, research statements and search facts. It gives one canonical
     reading per digit token, English first, and does not read words.
   - The source side (`quote=True`) covers quotes. It gives every plausible
     reading per digit token, plus the words zero to ninety-nine.
2. **Canonical form.** A reading is a `Decimal` written in plain form, with no
   exponent and no trailing zeros: "1,200" → `1200`, "3.50" → `3.5`,
   "007" → `7`. In the table, "thousands" means every group after the first
   has exactly 3 digits.

   | Token shape | Claim side | Source side |
   | --- | --- | --- |
   | digits only: `14` | `14` | `14` |
   | one `,` + 3 digits: `1,200` | `1200` | `1200`, `1.2` |
   | one `,` otherwise: `1,5` | `1.5` | `1.5` |
   | one `.` + 3 digits: `1.200` | `1.2` | `1.2`, `1200` |
   | one `.` otherwise: `3.5` | `3.5` | `3.5` |
   | repeated, all thousands: `1.200.000`, `1,200,000` | `1200000` | `1200000` |
   | mixed, the last separator is decimal and the others thousands: `1.234,5`, `1,234.5` | `1234.5` | `1234.5` |
   | anything else: `1,2,3`, `1.23.4`, `1,2.345` | raw token | raw token |

3. **Words, source side only.** `zero` to `nineteen`, `twenty` to `ninety`,
   and compounds with `-` or a space (`twenty-five`, `twenty five`). Match
   whole words, case-insensitive, after `_norm()`, which folds typographic
   dashes. No scale words ("million", "dozen", "Mio.").
4. **Callers.**
   - `sourced()` and `summary_sourced()` in `app/schema.py`, and
     `app/research.py`, read their quotes with `quote=True`.
   - `app/search.py` is **not changed**. Its allowed set comes from
     `facts()`, which are values, so it stays on the claim side.
5. **Newly accepted pairs (claim number, source text).** This is the complete
   list, approved with the spec:
   - `1200` against "1.200";
   - `14` against "fourteen";
   - `1.5` against "1,5", and `3.5` against "3.50";
   - `1.2` against "1,200", and `1200` against "1.200" read the other way.
     This is the factor-1000 widening, **accepted by the user**.
6. **Newly rejected.**
   - `15` against "1,5", in quotes and in search facts.
   - A claim "1.200" now needs `1.2` from its source, so it is rejected
     against "1200".
7. **Fail closed.** A token that cannot be read only matches the identical raw
   token. No LLM, no locale guess, no new dependency.
8. **Measurement.** The session model runs the eval (step 6), metrics only, on
   local `ollama_chat/qwen3:14b`. If the branch gains no sourced items, drop
   the source-side readings and words and keep the `1,5` fix and the
   canonical form.

## Coder handoff

Steps 1–5 edit 4 files (`app/schema.py`, `app/research.py`,
`tests/test_schema.py`, `tests/test_search.py`), so the handoff rule applies.

- Start one `coder` with this plan path and step 1.
- Send steps 2–5 to the same agent with `SendMessage`.
- Step 6 is for the session model, not the coder.
- After step 5, a fresh `opus` reviewer gets only this plan path and
  `git diff origin/main`.

## Steps

1. **`app/schema.py`: replace `numbers()`.** It is lines 31–32 today:
   `def numbers(s: object) -> set[str]:` and its one-line body.

   Add `from decimal import Decimal` to the imports (lines 1–5). Then replace
   the function with:

   ```python
   _UNITS = ("zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen "
             "fifteen sixteen seventeen eighteen nineteen").split()
   _TENS = "twenty thirty forty fifty sixty seventy eighty ninety".split()
   _WORDS = {w: i for i, w in enumerate(_UNITS)} | {
       f"{t}{sep}{u}" if u else t: 20 + 10 * i + j
       for i, t in enumerate(_TENS) for j, u in enumerate([""] + _UNITS[1:10]) for sep in ("-", " ")}
   _WORD_RE = re.compile(r"\b(" + "|".join(sorted(map(re.escape, _WORDS), key=len, reverse=True)) + r")\b")


   def _plain(s: str) -> str:
       return format(Decimal(s).normalize(), "f")


   def _readings(tok: str, quote: bool) -> set[str]:
       seps, groups = re.findall(r"[.,]", tok), re.split(r"[.,]", tok)
       if not seps:
           return {_plain(tok)}
       if len(seps) == 1:
           a, b = groups
           dec, whole = _plain(f"{a}.{b}"), _plain(a + b)
           if len(b) != 3:
               return {dec}
           # "1.200" / "1,200": a quote may mean either; a claim is read the English way
           return {dec, whole} if quote else {whole if seps[0] == "," else dec}
       if len(set(seps)) == 1:
           return {_plain("".join(groups))} if all(len(g) == 3 for g in groups[1:]) else {tok}
       if seps[-1] not in seps[:-1] and all(len(g) == 3 for g in groups[1:-1]):
           return {_plain("".join(groups[:-1]) + "." + groups[-1])}
       return {tok}  # unreadable: matches only the same raw token


   def numbers(s: object, quote: bool = False) -> set[str]:
       """Canonical numbers in s. quote=True (the source side) adds every reading and number words."""
       text = "" if s is None else str(s)
       out = set().union(*(_readings(t, quote) for t in re.findall(r"\d+(?:[.,]\d+)*", text)))
       if quote:
           out |= {str(_WORDS[w]) for w in _WORD_RE.findall(_norm(text))}
       return out
   ```

   The session model checked this code outside the repo against every row of
   the decision 2 table, plus `numbers(0) == {"0"}` and
   `numbers(None) == set()`.

   → verify by
   `docker compose build app && docker compose run --rm app python -c "from app.schema import numbers as n; assert n('1,5') == {'1.5'} and n('1.200', quote=True) == {'1.2', '1200'} and n('Fourteen', quote=True) == {'14'} and n(0) == {'0'}"`

   Traps:
   - No bind mount: build before every run, or you test the old image.
   - `numbers(0)` must stay `{"0"}`, the earlier fix in plan #1 step 6.
   - `_norm` is defined above `numbers` (line 19). Keep that order.

2. **`app/schema.py`: the source side in the two schema guards.**
   - Line 39 becomes
     `if not numbers(value) <= numbers(quote, quote=True):` (keep the
     comment).
   - Line 110 becomes
     `return numbers(self.summary) <= set().union(*(numbers(q, quote=True) for q in self.quotes()))`.

   Line numbers are from before step 1. Find the lines by text.

   → verify by
   `docker compose build app && docker compose run --rm app pytest tests/test_schema.py tests/test_extract.py tests/test_review.py`
   (the existing tests stay green).

   Traps: no bind mount. Do not change `quote_in()` or `MIN_QUOTE_WORDS`.

3. **`app/research.py:345`: the source side for web claims.**
   `numbers(c.statement) <= numbers(c.quote)` becomes
   `numbers(c.statement) <= numbers(c.quote, quote=True)`. Do not touch
   `app/search.py`; decision 4 keeps it on the claim side.

   → verify by
   `docker compose build app && docker compose run --rm app pytest tests/test_research_claims.py tests/test_research.py tests/test_search.py`
   (unchanged and green).

   Traps: no bind mount. The research tests fake DNS and never fetch a real
   page. Do not add real URLs.

4. **`tests/test_schema.py`: new tests.** Add `numbers` to the import on
   line 5. Then add:
   - `test_numbers_readings`: a `pytest.mark.parametrize` table with every row
     of decision 2, `(token, claim set, source set)`. Add `numbers(0)`,
     `numbers(None)`, and "Twenty‑five" with a non-breaking hyphen, which
     must give `{"25"}` on the source side.
   - `test_number_words_source_side_only`:
     - `numbers("a team of fourteen engineers", quote=True) == {"14"}`;
     - `numbers("fourteen") == set()`;
     - `numbers("someone", quote=True) == set()`, because whole words only.
   - `test_sourced_number_formats`, using a made-up document string:
     - `sourced(14, "a team of fourteen engineers", doc)` is True;
     - `sourced("1200 users", "1.200 users were onboarded", doc)` is True;
     - `sourced(15, "1,5 Mio. EUR saved per year", doc)` is False;
     - `sourced("1.5", "1,5 Mio. EUR saved per year", doc)` is True;
     - `sourced("1.2", "1,200 users were onboarded", doc)` is True, with the
       comment `# accepted widening, see plan #44`.
   - `test_summary_numbers_from_word_quote`: a case whose team-size quote is
     "a team of fourteen engineers". The summary "14 engineers delivered it."
     is sourced. "15 engineers delivered it." is not.

   → verify by
   `docker compose build app && docker compose run --rm app pytest tests/test_schema.py -q`

   Traps:
   - No bind mount.
   - Made-up test data only: no client names and no quotes from real
     documents.
   - `sourced()` also requires the quote to be in the doc. Build `doc` so it
     contains every quote.

5. **`tests/test_search.py`: search did not widen.** Add
   `test_comma_decimal_fact_no_longer_licenses_15(approved, monkeypatch)`
   after line 86. It approves
   `data().model_copy(update={"outcomes": [Outcome(metric="saved", value="1,5 days", source_quote="q")]})`.
   It then uses `picks_reply` (line 64) with `tailored="Saved 15 days."`, and
   asserts that the pick falls back to the summary and a note is returned,
   like `test_summary_numbers_do_not_license_tailored_numbers` (line 80).

   → verify by
   `docker compose build app && docker compose run --rm app pytest tests/test_search.py -q`

   Traps:
   - No bind mount.
   - The test needs the compose `db`. `docker compose run` starts it. Never
     run `docker compose up` or `down`: the user's dev stack is live.
   - Made-up data.

6. **Session model: measure, then decide.** Not the coder.
   - Run
     `docker compose run --rm --network host app python scripts/eval_extraction.py --docs <dir outside the repo> --models ollama_chat/qwen3:14b`
     once on `origin/main` (build that image first), then once on the
     branch.
   - Record in the PR only the per-file `sourced=x/y` counts and the
     `== ollama_chat/qwen3:14b` summary lines.
   - If the branch gains no sourced items, drop the source-side readings and
     words. Keep the `1,5` fix and the canonical form. Update this plan in the
     same commit as that code.

   → verify by checking that both runs exit 0 and print only metric lines.

   Traps:
   - Local model only, never `gpt-oss:120b-cloud` or any "cloud" model. The
     data policy refuses them on confidential documents, and the run must not
     depend on that.
   - The documents directory must be outside the repo; the script refuses
     otherwise.
   - Never paste document text, quotes or values anywhere, including the PR,
     issue or chat.
   - Set no `destination` in `EXTRACT_MODEL_OPTIONS`.
   - If no document directory is available, record "deferred: no documents"
     in the PR and keep the full change.

## Tests

- `docker compose build app && docker compose run --rm app pytest`: all
  green, including the new tests in steps 4 and 5 and the unchanged
  `tests/test_search.py`, `tests/test_research_claims.py`,
  `tests/test_extract.py` and `tests/test_review.py`.
- Step 6 eval: metric lines only, with branch `sourced` counts ≥ `main` on
  each file.

## Rollback

- Revert the implementation commits (`git revert <sha>…`). `numbers()` goes
  back to the digits-only version, and the callers drop `quote=True`.
- No data migration either way. Stored `unsourced` flags only change when a
  case is re-extracted or saved in review.
