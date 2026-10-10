---
status: approved
issue: 164
author: olafkfreund
---

# Intent: Renaming a protected client, or removing an alias, drops protection for documents that use the old name

## Problem

`update` (`app/clients.py:69-87`) lets an admin change a non-referenceable
client's name or aliases freely. Whatever name is removed stops being
replaced by `apply()` and caught by `blocked()`, everywhere and at once.

Example:

1. The protected client "Zorp Bank" rebrands, and the admin edits its name to
   "Zorp Financial".
2. Every document and approved case that still says "Zorp Bank" now shows
   the name in downloads, search and research.

The page's help text tells admins to add former names as aliases
(`app/templates/clients.html:7`), but nothing enforces it. `delete`
(`:90-103`) already refuses to delete a non-referenceable client because "its
names must stay hidden". A rename is the same loss by another route.

## Proposed outcome

- Saving a non-referenceable client never silently drops a name. A name or
  alias that was protected before the save is still protected after it,
  unless the admin explicitly chooses otherwise.
- The admin sees which names are kept.
- Referenceable clients are unchanged. Their names aren't hidden, so there is
  nothing to keep.

## Affected users and systems

- `app/clients.py` (`update`), `app/templates/clients.html`.
- Admins who maintain the client registry.

## Constraints

- `clean()`'s checks still run on the final name and alias list: no label
  contains a protected name, and no name is shared with another client.
- Making a client referenceable is a deliberate decision, the client's
  permission, and stays allowed. This issue covers renames and alias
  removals on a protected client.
- No JavaScript, as for every portal page.

## Open questions

1. **What happens to a removed name?**
   - **A. Keep it as an alias automatically.** On save, any name or alias the
     client had before and doesn't have now is added back to its aliases, and
     the page shows that. It is simple and fails closed. The cost: a mistaken
     alias that over-replaces an ordinary word ("Apex") can't be removed while
     the client is protected.
   - **B. Refuse the save with a 400** that lists the names being dropped. The
     admin must add them back as aliases themselves. It is explicit, and has
     the same cost as A.
   - **C. A, plus a "stop hiding these names" checkbox** for deliberately
     removing an alias. It can always be corrected, but it adds UI and a way
     to drop protection.

   **Recommendation: A.** A rename is the common case, and A handles it with
   no extra step. If removing a wrong alias proves to be a real need, C can be
   added later as its own change.

**Decision (approved by olafkfreund, 2026-10-10): A.**
