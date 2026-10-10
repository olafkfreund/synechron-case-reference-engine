---
status: draft
issue: 122
intent: intent/2026-10-10-122-query-preview-idempotent.md
---

# Spec: Research query preview can differ from what is sent

## Design

The approved outcome: `finalize(finalize(x)) == finalize(x)` for every
input, with no open questions.

`finalize` (`app/research.py:81-87`) is called by `build_query` (`:90-97`),
which feeds both previews (`:107`, `:135`), and again by `research_send`
(`:150`), on the hidden field. So the fix lives in `finalize` alone.

**Reading the code changed the approach from the intent.** The intent
proposed running the identifier filter a second time after the scrub, but two
passes are not enough to guarantee the outcome. Each step can expose new
matches for the others:

- Removing a name can make two numbers adjacent. This is the case reported in
  the issue.
- The `[:MAX_QUERY]` cap (200 characters) can cut a word, so that a name
  followed by more letters ("zorp bankers") ends exactly at the cut. The text
  then ends "zorp bank", and `scrub()` matches that on the next pass, because
  `(?!\w)` is true at the end of the string.
- Removing an identifier can make two name fragments adjacent.

So `finalize` runs the same step until the text stops changing:

```python
def finalize(text: str, clients) -> str:
    """... Run to a fixed point, so the preview is exactly what is sent (#122)."""
    q = text
    while True:  # each pass only removes text, so this ends; removing one thing can expose another
        nxt = anonymise.scrub(_IDENTIFIERS.sub(" ", q), clients)[:MAX_QUERY].strip()
        if nxt == q:
            break
        q = nxt
    if not q or anonymise.blocked(q, clients):
        raise ValueError(...)
    return q
```

**Why the loop ends.** After the first pass the text is folded, and folding
folded text changes nothing. From then on, each pass either removes
characters or returns the same string. So the length strictly decreases until
the text stops changing, and that happens within at most `MAX_QUERY` passes.
In practice it takes 2 or 3.

## Alternatives rejected

- **Run the identifier filter again after the scrub (the intent's
  wording).** That fixes the reported case, but not a name exposed by the cap,
  which leaves the preview different from what is sent.
- **Send the preview's stored value instead of re-running `finalize` on
  send.** The hidden field is controlled by the user (`:150` comment), so it
  must be re-checked on send, and that check must give the same result as the
  preview.
- **Apply the cap first.** A cut can still expose a name at the end of the
  text, so it doesn't remove the need for a fixed point.

## Risks

- **It only ever strips more.** The blocked() refusal at the end is
  unchanged, so this can't become a leak.
- **Cost.** A few extra regex passes over at most 1000 characters, done only
  on preview and send.
- **No host impact.**

## Verification

Tests in `tests/test_research.py`, using `REG` with a made-up protected
client:

- `test_finalize_is_idempotent`: `finalize("Basel 2023 Zorp 2024 reporting", REG)`
  is a fixed point (`finalize(q) == q`). On main the second call strips
  "2023 2024", so the test fails there.
- A cap case: a question whose 200th character falls just after
  "Zorp" + "ers". The test asserts that `finalize(q) == q`, and that "zorp"
  is not in `q`. Fails on main.
- `test_identifiers_stripped_from_query` and the refusal tests still pass, and
  so does the full suite.
