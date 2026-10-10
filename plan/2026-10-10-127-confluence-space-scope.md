---
status: approved
issue: 127
spec: spec/2026-10-10-127-confluence-space-scope.md
---

# Plan: Confluence page moved to an out-of-scope space stays live with the source's groups

The Confluence crawler checks page restrictions, but not whether a page is
still in one of the configured spaces. A page moved to another space keeps its
id. It then stays live, or is re-ingested, under the source's groups.

These decisions are copied from the approved spec:

- **Where the check goes.** It runs first in `allowed()`. Every path a page
  can come in by calls `allowed()`:
  - a search hit;
  - an attachment's page;
  - `retry_ids`;
  - the live re-check, which also covers attachments through their page.

  A page whose `space.key` is not in `config.spaces` is not allowed. A page
  with no `space` is out of scope too, so the check fails closed.
- **Case.** Space keys are compared case-folded, because CQL treats them as
  case-insensitive.
- **Expand.** Each of the four page fetches asks for `space`: the search, the
  two by-id fetches and the re-check.
- **What happens to a rejected page.** It is handled the same way as a
  restricted page: `skipped_restricted` with a withdraw in `do_page`, and
  `withdrawn_on_recheck` in the re-check. There is no new counter.
- **Scope.** No schema change, and no other crawler changes.

**Size:** 2 file-editing steps in 2 files, `app/crawl.py` and
`tests/test_crawl_confluence.py`. That is below the coder threshold.

**Overlap with #129:** #129 widens the `except` on the by-id fetches at
`:457-458` and `:469-470`. This plan edits the `expand` string on `:457` and
`:469`. Whichever merges second rebases and keeps both changes.

## Steps

1. `app/crawl.py`, in `crawl_confluence`:
   - **After `:356`** (the `spaces` validation): add
     `in_scope = {str(k).casefold() for k in spaces}`.
   - **First line of `allowed()`'s body, after the docstring (`:374`):**
     ```python
     if str((page.get("space") or {}).get("key", "")).casefold() not in in_scope:
         return False  # moved out of the configured spaces (#127): its space permissions are not the source's groups
     ```
   - **`:450`:** change the search expand to
     `"body.storage,version,ancestors,container,space"`.
   - **`:457` and `:469`:** change to
     `?expand=body.storage,version,ancestors,space`.
   - **`:484`:** change to `?expand=ancestors,space`.

   Verify: `grep -n "expand=" app/crawl.py`. Every Confluence page fetch shows
   `space`, and the attachment fetch at `:487` (`expand=container`) is
   unchanged.

   Traps:
   - The Cloud branch of `allowed()` gets ancestors from v2. The space check
     must run before that branch, so an out-of-scope page costs no API call.
   - `spaces` can contain keys that need quoting, like the fixture's `'A"B'`.
     Compare the raw key and don't parse the CQL.
   - Don't touch the attachment-container check at `:487`.

2. `tests/test_crawl_confluence.py`:
   - **Fake data.**
     - `Conf.page()` (`:26-31`) gains `space="ENG"` and stores
       `"space": {"key": space}`.
     - The search branch (`:58-61`) also keeps only hits whose space key is
       in the CQL's `space in (...)` list. Parse it with
       `re.findall(r'"((?:[^"\\]|\\.)*)"', re.search(r"space in \(([^)]*)\)", q["cql"])[1])`,
       unescaping `\"`, and compare case-folded.
     - An attachment hit takes its page's space (`self.pages[a["container"]["id"]]["space"]`).
     - Record each request's `q.get("expand")` in a new `self.expands` list.
   - **New tests**, after `test_attachment_moved_to_another_page_is_withdrawn`:
     - `test_page_moved_out_of_scope_is_withdrawn`:
       1. Create `page("p1")` and `attach("p1","a1")`, then crawl. Both are
          live.
       2. Set `cf.pages["p1"]["space"] = {"key": "HR"}` and crawl again.
          `live(cf.sid) == {}`.
     - `test_retry_of_moved_page_is_not_ingested`:
       1. Make `p1` fail once, using the 500 restriction queue as in
          `test_failure_withdraws_and_is_retried_next_run` (`:293`), so
          `retry_ids == ["p1"]`.
       2. Clear the queue, move `p1` to `HR`, and crawl. `"page:p1"` is not
          live.
     - `test_space_key_matches_case_insensitively`: set the source config
       `spaces` to `["eng"]` with
       `update sources set config = config || '{"spaces":["eng"]}'`. A page in
       `ENG` is ingested.
     - `test_page_fetches_expand_space`: after a crawl with a page and an
       attachment, every recorded expand that contains `ancestors` also
       contains `space`.

   Verify:
   - `pytest -q tests/test_crawl_confluence.py`: all pass, old tests
     included.
   - Then check that each new test except the case-insensitive one fails on
     main. To do that, `git stash push app/crawl.py`, rebuild, run the new
     tests, `git stash pop`, and rebuild.

   Traps:
   - If the fake search doesn't filter by space, the first test still passes
     on main through the re-check. The by-space filter is what makes a moved
     page drop out of CQL, as it does in real Confluence.
   - The `cf` fixture's spaces are `["ENG", 'A"B']`, so the default `"ENG"`
     must stay in scope.
   - Use made-up names only.

## Tests

`docker compose build app && docker compose run --rm app timeout 900 pytest -q -p no:cacheprovider`:
the full suite passes. The new tests that need to fail on main do.

## Rollback

Revert the commits. Withdrawn documents come back on the next crawl if they
are still in scope.
