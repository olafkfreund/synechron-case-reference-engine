---
status: approved
issue: 264
spec: spec/2026-10-14-264-stale-arch-note.md
---

# Plan: Architecture notes that go away once the diagram is fixed

Worktree `../refs-engine-264`, branch `fix/264-stale-arch-note`. It is small
(two code files), so the session implements it and no coder is handed the
work.

## Approved decisions (from the spec)

1. **One helper, `set_arch_note(case)`:**
   - it drops every `needs_attention` entry that starts with
     "architecture has ", "architecture proposal failed" or
     "architecture: "
   - it then appends one "architecture has N unsupported item(s); edit it
     before approval" if the current architecture has flags
2. **Called** after a successful save, at the start of regenerate's
   outcome (the regenerate's own "architecture: …" note is added after),
   after remove, and in `writeup.propose` in place of its count append.
3. **Nothing else changes:** other notes, flags, the gate and
   `split_notes`. There is no migration.

## Steps

1. **`app/writeup.py`.**
   - Add `ARCH_NOTES` and `set_arch_note(case)` next to `propose`.
     `review.py` imports `writeup`, so the helper lives in `writeup.py`
     with no cycle.
   - In `propose` (`:219-222`), replace the count append with
     `set_arch_note(case)`.
   - Keep the "proposal failed" append (`:215`).
2. **`app/review.py`:**
   - `save_architecture` (`:576`): call `set_arch_note(case)` after
     `arch.flags = check_architecture(case)` and before `save`.
   - `regenerate_architecture` (`:597`), after `case.architecture` is
     decided:
     1. call `set_arch_note(case)`
     2. append the `work.needs_attention` notes that are not count notes,
        that is the "architecture: …" and "proposal failed" notes,
        de-duplicated
   - `remove_architecture` (`:624`): call `set_arch_note(case)` after
     clearing it.
3. **Tests** (`tests/test_review.py`): the five cases in the spec's
   verification. Negative control: drop the call in `save_architecture`,
   and the clean-save test fails.
4. **Full suite, then the reviews and the PR:**
   `docker compose build app && docker compose run --rm app pytest`,
   then Opus, Codex and agy.

## Tests

As above.

## Rollback

Revert the merge commit. Stored notes are left as written.

## Deviations

None in the code.
- **Tests:** `test_architecture_notes_follow_the_current_diagram` (save,
  regenerate, remove) and `test_set_arch_note_counts_a_flagged_architecture_once`.
- **Negative control:** without the save call, the stale "proposal failed"
  note survives and the test fails.
- **Full suite:** 1444 passed.
