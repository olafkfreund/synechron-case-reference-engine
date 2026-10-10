---
status: approved
issue: 127
intent: intent/2026-10-10-127-confluence-space-scope.md
---

# Spec: Confluence page moved to an out-of-scope space stays live with the source's groups

## Design

The approved intent has no open questions. The check goes in `allowed()`
(`app/crawl.py:370-386`), which `do_page` (`:398`) and the live-document
re-check (`:485`) both call. Every way a page gets in therefore goes through
it:

- search hits;
- the page an attachment hit sits on (`:457`);
- `retry_ids` (`:469`);
- the re-check, which also covers attachments, because their page is checked.

1. **The scope check.** It runs first in `allowed()`, before any restriction
   lookup, so an out-of-scope page costs no API call:
   ```python
   in_scope = {k.casefold() for k in spaces}  # once, next to `spaces` (:356)

   def allowed(pid, page):
       ...
       if (page.get("space") or {}).get("key", "").casefold() not in in_scope:
           return False  # moved out of the configured spaces (#127): its space permissions are not the source's groups
   ```
   A page with no `space` in its response is out of scope, so the check fails
   closed. A rejected page is handled exactly like a restricted one:
   - `do_page` counts it as `skipped_restricted` and withdraws the page and
     its attachments;
   - the re-check withdraws it and counts it as `withdrawn_on_recheck`.
2. **Ask for `space` in every page fetch.** Confluence v1 only returns
   `space` when it is expanded, and both Cloud and Data Center return
   `space.key` that way. `space` is added to four fetches:
   - the search, `:450`: `"body.storage,version,ancestors,container,space"`;
   - the by-id fetches at `:457` and `:469`:
     `?expand=body.storage,version,ancestors,space`;
   - the re-check, `:484`: `?expand=ancestors,space`.

   The attachment check at `:487` needs no change, because the attachment's
   page is already checked.

There is no schema change and no new counter.

### Test fake

`Conf.page()` (`tests/test_crawl_confluence.py:26-31`) gains `space="ENG"`
and stores `"space": {"key": space}`. Without it, every existing test fails
closed. The fake search at `:49-58` also filters hits by the space keys in
`space in (...)`, so a moved page drops out of CQL the way it does in real
Confluence. The fake ignores `expand` and always returns `space`, which is
fine for these tests.

## Alternatives rejected

- **Fetch space permissions and compare them with the source's groups.**
  Confluence groups are not portal groups, so no comparison is possible. The
  configured space list is the scope the admin mapped.
- **Check scope only in the re-check.** `retry_ids` and the attachment page
  fetch would still ingest a moved page.
- **A separate `skipped_out_of_scope` counter.** It has no reader. The issue
  is about access, and `skipped_restricted` already means "not ingested
  because of access".

## Risks

- **Case of space keys.** An admin can type `eng` for space `ENG`. CQL
  treats space keys case-insensitively, so an exact-match check would
  withdraw everything. That's why the check compares case-folded keys.
- **Personal spaces.** Their keys look like `~user`. They compare by the same
  rule.
- **Rebase overlap with #129.** #129 changes the `except` clauses on the same
  by-id fetch lines, `:457-458` and `:469-470`, where this spec edits the
  `expand`. Whichever merges second rebases with a small, line-level
  conflict.
- **Missing `space` in the response.** If an API ever stops returning
  `space` despite the expand, everything is withdrawn: it fails loudly and
  closed, and nothing leaks. The tests pin that the expand is sent.
- **No host impact.**

## Verification

New tests in `tests/test_crawl_confluence.py`:

- `test_page_moved_out_of_scope_is_withdrawn`: a page and its attachment are
  ingested in ENG. The page then moves to `HR`, still `current` with no
  restrictions. The next crawl withdraws `page:p1` and `att:p1:a1`. On main
  both stay live with `["g-conf"]`.
- `test_retry_of_moved_page_is_not_ingested`: `p1` fails, so it is in
  `retry_ids`, then it moves to HR. The next crawl doesn't ingest it, and
  `page:p1` is not live. On main it is ingested.
- `test_space_key_matches_case_insensitively`: with config `["eng"]` and a
  page in `ENG`, the page is ingested.
- `test_page_fetches_expand_space`: the search and the by-id requests carry
  `space` in `expand`. The fake records query strings for this.

The full suite passes.
