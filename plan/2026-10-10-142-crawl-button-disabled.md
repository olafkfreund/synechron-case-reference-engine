---
status: draft
issue: 142
spec: spec/2026-10-10-142-crawl-button-disabled.md
---

# Plan: "Crawl now" is shown only on enabled sources

The approved decisions:

- **Template only.** In `app/templates/sources.html`, the "Crawl now" form in
  the source-row loop is wrapped in `{% if enabled %}...{% endif %}`. This is
  the same guard the Upload control on the next line already uses
  (`{% if kind == 'upload' and enabled %}`).
- **No route change.** `crawl_now` in `app/sources.py` keeps its
  `where id=%s and enabled` check and its 404 for a disabled source. The
  button is only a display hint, and the server still enforces the rule.
- **Rejected:** a disabled button with a reason, and a redirect with a notice.

Two steps, two files. That is below the coder threshold, so the session model
implements it.

## Steps

1. **`app/templates/sources.html:45`.** Wrap the line
   `<form class="inline" method="post" action="/admin/sources/{{ id }}/crawl">...Crawl now</button></form>`
   as `{% if enabled %}<form ...>...</form>{% endif %}` on the same line. Leave
   the markup inside it as it is.
   - Verify with step 2's test.
   - Traps:
     - `enabled` is the loop variable from line 37
       (`{% for id, kind, name, config, acl, enabled, ... in sources %}`). Don't
       rename it.
     - No JavaScript. Templates are Jinja2 only.

2. **`tests/test_sources.py`, after `test_sources_page_upload_control_and_notices`.**
   Add `test_crawl_now_shown_only_on_enabled_sources(env)`, with
   `# noqa: F811` like its neighbours:
   - `db.init()`.
   - Insert two sources of kind `s3`, one enabled and one disabled. Use
     `name=uuid.uuid4().hex` and `config=Jsonb({"bucket": "b"})`, the same
     insert as the neighbouring test, and collect both ids.
   - In `try`, GET `/admin/sources` with `client([ADMIN])`. Assert that
     `f'action="/admin/sources/{on}/crawl"'` is in the text and
     `f'action="/admin/sources/{off}/crawl"'` is not.
   - In `finally`, `delete from sources where id = any(%s)`.
   - Verify with:
     `docker compose build app && docker compose run --rm app timeout 900 pytest -q -p no:cacheprovider tests/test_sources.py::test_crawl_now_shown_only_on_enabled_sources`.
     It passes on the branch and fails on main's template.
   - Traps:
     - There is no bind mount, so always build before running.
     - Never run `docker compose up` or `down`.
     - The repo is public, so use random or made-up names only.
     - The test must fail on main. Check this with `scratchpad/check.sh`.

## Tests

- **Fails on main:**
  `tests/test_sources.py::test_crawl_now_shown_only_on_enabled_sources`.
- **Must still pass:** `test_sources_admin`, where a POST to a disabled
  source's crawl still returns 404.
- **Full suite:** `docker compose build app && docker compose run --rm app timeout 900 pytest -q -p no:cacheprovider`
  is green.

## Rollback

Revert the step commits. No data or migration is involved.
