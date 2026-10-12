---
status: approved
issue: 197
author: olafkfreund
---

# Intent: Preview a case output before downloading it

## Problem

On Find references (`templates/search.html`), each case card offers only
download buttons: Word, PowerPoint, PDF, PDF slides and Markdown. They post
to `/generate` (`app/render.py`), which builds the file and logs a row in
`generations`.

To check what a client would see, the user must download a file and open it
in Word or PowerPoint. The card does not show the output's content: the
"Suggested wording" box is AI-tailored text that is *not* in the downloads,
and the card shows only a few fields.

## Proposed outcome

- From a case card (and from a research result with a case), the user can
  open a preview page that shows exactly the content the download will
  contain: the same approved fields, anonymisation and "withheld" rules
  (`section()` then `protect()`), and the industry-context section when
  research is included.
- The preview page has the same download buttons, so the user reviews first
  and downloads from there.
- What the preview shows never differs from what the download contains.

## Affected users and systems

- Bid and sales users (role `user`).
- `app/render.py` (`/generate`, `section`, `protect`), `templates/search.html`,
  a new preview template, `docs/user-guide/`.

## Constraints

- Same access checks as `/generate`: approved, in date, visible to the user,
  at most 3 cases, the industry-context acknowledgement.
- No JavaScript (the app is server-rendered Jinja).
- No model call.
- The preview shows content, not the branded layout: it is not a rendering of
  the Word or PowerPoint template.

## Open questions

1. Should opening a preview be logged in `generations` (the audit of what
   left the system)? Recommendation: no, because nothing leaves the system.
   Log only the download, as today.
2. Show the content only (recommended: one HTML page built from the same
   sections as the Markdown output)? Or a true visual preview of the Word or
   PDF file? That would mean converting with LibreOffice on every view, which
   costs seconds per page and runs only in the worker image.
