---
status: approved
issue: 264
intent: intent/2026-10-14-264-stale-arch-note.md
---

# Spec: Architecture notes that go away once the diagram is fixed

## Design

**One helper in `app/review.py`:**

```python
ARCH_NOTES = ("architecture has ", "architecture proposal failed", "architecture: ")

def set_arch_note(case) -> None:
    """Drop every earlier architecture note; if the current architecture is flagged, add one fresh count (#264)."""
```

1. It removes every `needs_attention` entry that starts with one of the
   `ARCH_NOTES` prefixes. These are the only producers:
   - `writeup.py:215`, `:222`
   - `review.py:614`, `:616`
2. If `case.architecture` exists and has flags, it appends exactly
   `f"architecture has {len(flags)} unsupported item(s); edit it before
   approval"`. This is the same wording as `writeup.py:222`, which now uses
   the same helper, so the text lives in one place.

**Called from:**

| Route | When the helper runs |
|---|---|
| `save_architecture` (`app/review.py:576`) | after `check_architecture`, on a successful save. A failed save changes nothing. |
| `regenerate_architecture` (`:597`) | first, then the regenerate's own "no new proposal" or "nothing supported" note is appended as today. A failed attempt still says so, and the stale count from the earlier diagram is gone. |
| `remove_architecture` (`:624`) | after removal, with no architecture, so every architecture note is dropped |
| `writeup.propose` (`app/writeup.py:205-222`) | replaces its own append for the count. The "proposal failed" note stays as it is. |

**What doesn't change:** other notes, flag computation, the approval gate,
and the `split_notes` classification (architecture notes stay action
notes).

**Existing stale notes** clear on the next save, regenerate or remove.
There is no migration.

## Alternatives rejected

- **Computing the note at render time from `architecture.flags`, never
  storing it.** That is cleaner, but `needs_attention` is also read by the
  queue counts, MCP and the outputs' notes, so all of those would have to
  change. One helper at the write points is smaller.
- **A one-off SQL clean-up of existing notes.** Unneeded; the next save
  fixes each case.

## Risks

- **A note from another source that starts with "architecture"** would be
  removed. Today there are only the four producers above. The prefixes
  are explicit, and the test pins them.

## Verification

- **Tests in `tests/test_review*.py`:**
  - Save a clean architecture over a case with "architecture has 7…":
    the note is gone.
  - Save a flagged one: exactly one count note, with the right number.
  - Regenerate that proposes nothing: the old count is gone, and "the
    model proposed nothing…" is present once.
  - Remove: no architecture notes remain.
  - Other notes, for example "Re-extracted…", are untouched in every case.
- **Negative control:** remove one call and its test fails.
- **Full suite:**
  `docker compose build app && docker compose run --rm app pytest`.
- **Reviews:** Opus, Codex and agy, read-only.
