---
status: approved
issue: 117
intent: intent/2026-10-10-117-research-view-leak.md
---

# Spec: Research view shows protected client names in claims and sources

## Design

The approved answers are:

- **Claims:** apply the download's rule to each claim, then drop any claim that
  still names a protected client.
- **Source pages:** drop any source page whose publisher or URL names a
  protected client, and count it with the skipped sources.

All the filtering happens in `research_view` (`app/research.py:163-182`), at
view time, using the `clients` it already loads at `:169`. No other module
changes. The template stays as it is, except for one new note.

1. **Filtering claims.** A new helper `visible_claims(claims, clients)` sits
   next to `research_view`. It returns the safe claims and the number dropped.
   ```python
   def visible_claims(claims, clients) -> tuple[list[dict], int]:
       """The download's rule (render.industry_section) for the screen: apply(), then drop what blocked() still finds."""
       out = []
       for c in claims:
           c = {**c, "quote": anonymise.apply(c["quote"], clients), "statement": anonymise.apply(c["statement"], clients)}
           if not anonymise.blocked("\n".join(str(c.get(k, "")) for k in ("quote", "statement", "publisher", "url")), clients):
               out.append(c)
       return out, len(claims) - len(out)
   ```
   - The publisher and URL are checked but never rewritten. Rewriting them
     would forge the citation, the same rule as in `industry_section`.
   - The statement is checked as well. The download does not print it, but the
     view does.
2. **Wiring.** `claims, omitted = visible_claims(row[3].get("claims", []), clients)`
   replaces `:174`. `groups` is then built from the filtered list, so the
   Comparison table only ever sees safe claims.
3. **Source pages.** Each page whose publisher or URL names a protected client
   is dropped:
   ```python
   pages = [p for p in row[3].get("pages", [])
            if not anonymise.blocked(f"{p['publisher']}\n{p['url']}", clients)]
   ```
   The number dropped is added to the skipped count. `skipped` becomes
   `row[3].get("skipped", []) + [{}] * dropped`, so the template's
   `skipped | length` counts them. The stored entries stay as they are, and
   the template only reads the length.
4. **Note.** When `omitted` is set, the template shows
   `<div class="note">{{ omitted }} statement(s) not shown: they named a protected client.</div>`.
   It is the same wording as the download's note.

## Alternatives rejected

- **B: drop every claim that names a protected client, without `apply()`
  first.** Answer 1 chose the download's rule, so the screen and the download
  agree.
- **Filter when research is stored, in the worker.** The registry can change
  after the research ran, and old results would not be re-checked. The view
  already re-checks case access on every load for the same reason.
- **Call `render.industry_section` from the view.** It drops claim types that
  are not in `PHRASES`, which the view shows. It does not check the statement.
  And it builds download text the view does not need.
- **A Jinja filter that runs `apply()` in the template.** That would rewrite
  the publisher and URL, which forges the citation, and it does not drop
  anything.

## Risks

- **Over-dropping.** A claim whose publisher is a protected client's own site
  disappears from the screen. This is intended, and it already happens in the
  download.
- **Cost.** `apply()` and `blocked()` run per claim on each view, with a few
  dozen claims at most. They are milliseconds, the same cost the download pays.
- **No host impact.** The change is app code only.

## Verification

- New tests in `tests/test_research_claims.py`, using its `sent`/`finish`
  helpers and the `reg` fixture:
  - A claim whose quote names the protected client is shown with the label in
    its place.
  - A claim whose publisher names the client is not shown, and the note says
    "1 statement(s) not shown".
  - A page whose URL names the client is gone from Sources, and the skipped
    count goes up by 1.
  - The protected name appears nowhere in the response.
- The leak tests fail on main.
- The full suite passes.
