---
status: approved
issue: 142
intent: intent/2026-10-10-142-crawl-button-disabled.md
---

# Spec: "Crawl now" is shown only on enabled sources

## Design

There is one template change, in `app/templates/sources.html`, on the
`<form class="inline" ... action="/admin/sources/{{ id }}/crawl">` line
inside the source row loop.

- **The change:** wrap that form in `{% if enabled %}...{% endif %}`, the
  same guard the Upload control on the next line already uses
  (`{% if kind == 'upload' and enabled %}`).
- **The route stays as it is.** `crawl_now` (`app/sources.py`) keeps its
  `where id=%s and enabled` check and its 404 for a disabled source. The
  button is only a display hint, and the server still enforces the rule.

## Alternatives rejected

- **Show the button disabled (`disabled` attribute) with a reason.** That adds
  markup and help text for an action the admin can't take. The row already
  shows a "Disabled" badge.
- **Make the route redirect back with a notice instead of a 404.** That
  changes server behaviour for a request the UI no longer sends. The intent
  keeps the route as it is.

## Risks

None of note. The button disappears for disabled sources only. Enabling a
source in its Edit form brings the button back on the next page load.

## Verification

New test in `tests/test_sources.py`, which must fail on main:

- `tests/test_sources.py::test_crawl_now_shown_only_on_enabled_sources`
  - It adds one enabled and one disabled source, then GETs `/admin/sources`
    as admin.
  - It asserts that `action="/admin/sources/<enabled id>/crawl"` is in the
    page, and `action="/admin/sources/<disabled id>/crawl"` is not.

The existing `test_sources_admin` still sees a POST to a disabled source's
crawl return 404. The full suite stays green.
