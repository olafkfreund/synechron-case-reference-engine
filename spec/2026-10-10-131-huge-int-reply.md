---
status: approved
issue: 131
intent: intent/2026-10-10-131-huge-int-reply.md
---

# Spec: A model reply with a very long number crashes extraction instead of being skipped

## Design

The approved rule is that a first number longer than 12 characters,
separators included, counts the item as malformed. It is never truncated.

The fix is one condition in `assemble` (`app/schema.py:186-190`):

```python
            first = re.search(r"\d[\d,]*", v)
            if not first or len(first.group()) > MAX_INT_CHARS:  # a runaway reply, not a count; int() would raise (#131)
                bad += 1
```

`MAX_INT_CHARS = 12` sits beside `_INTS`. The regex stays the same, so the
whole run of digits is matched and measured. That is why a capped regex isn't
used: a capped match would take the first 12 digits of a longer run, which
invents a value.

Twelve characters covers every real value of `team_size` or
`duration_months` with separators (for example "1,000,000"), with plenty of
room to spare.

## Alternatives rejected

- **Catch the `ValueError` from `int()`.** That only skips runs of more than
  4300 digits. A 20-digit run would still be stored as a nonsense team size.
  The length rule covers both.
- **Cap the regex at `\d[\d,]{0,11}`.** As above, it truncates.
- **Raise Python's int digit limit.** That parses garbage instead of
  rejecting it.

## Risks

- **A legitimate number over 12 characters is now skipped and counted.**
  There is none for these two fields.
- **The "malformed" count in the notes grows by one** for such a reply, as it
  already does for other bad items.
- **No host impact.**

## Verification

New tests in `tests/test_schema.py`, next to
`test_assemble_takes_the_first_number_only` (`:154`):

- An item with `field="team_size"` and `value="1"*4301` is skipped. The field
  stays `None`, and the notes say "1 malformed". This fails on main, where it
  raises `ValueError`.
- `value="1"*13` is skipped, while `value="1,000,000"` gives `1000000`. The
  first fails on main, because it stores the 13-digit number.

The full suite passes.
