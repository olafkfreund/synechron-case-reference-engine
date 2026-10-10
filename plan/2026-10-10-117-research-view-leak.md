---
status: approved
issue: 117
spec: spec/2026-10-10-117-research-view-leak.md
---

# Plan: Research view shows protected client names in claims and sources

The research page prints web claims and source pages unfiltered. These
decisions are copied from the spec:

- **Claims:** use the same rule as the download. Run `apply()` on each claim's
  quote and statement. Then drop the claim if `blocked()` still finds a
  protected name in the quote, statement, publisher or URL. Never rewrite the
  publisher or URL, because that would forge the citation.
- **Source pages:** drop a page whose publisher or URL names a protected
  client. Add the dropped pages to the skipped count.
- **Note:** show "N statement(s) not shown: they named a protected client"
  when any claim is dropped.
- **Where:** filter at view time, in `research_view` only. Stored results, the
  worker, `render` and `anonymise` are not changed.

## Steps

1. `app/research.py`:
   - **New helper.** Add `visible_claims(claims, clients) -> tuple[list[dict], int]`
     just above `research_view` (`:162`), as written in the spec. It copies each
     claim with `apply()` on `quote` and `statement`, then keeps it only if
     `anonymise.blocked("\n".join(str(c.get(k, "")) for k in ("quote", "statement", "publisher", "url")), clients)`
     is empty. It returns the kept claims and the number dropped.
   - **`:174`.** Replace it with
     `claims, omitted = visible_claims(row[3].get("claims", []), clients)`.
     The `groups` loop below then uses the filtered list.
   - **Before the `return`.** Add:
     ```python
     pages = [p for p in row[3].get("pages", []) if not anonymise.blocked(f"{p.get('publisher', '')}\n{p.get('url', '')}", clients)]
     skipped = row[3].get("skipped", []) + [{}] * (len(row[3].get("pages", [])) - len(pages))
     ```
   - **The `page(...)` call.** Pass `pages=pages, skipped=skipped, omitted=omitted`.

   Verify by running `pytest -q tests/test_research_claims.py tests/test_research.py`.
   The existing tests pass.

   Traps:
   - Stored claims always have `statement` and `quote`, but older rows may not
     have every key. Use `c.get(k, "")` for the blocked string, and
     `c.get("statement", "")` inside `apply`.
   - `clients` is loaded inside the `with` block at `:169`, and it is a list
     that is still valid after the block ends.

2. `app/templates/research_view.html`: after the `{% if note %}` line (`:9`), add
   `{% if omitted %}<div class="note">{{ omitted }} statement(s) not shown: they named a protected client.</div>{% endif %}`.

   Verify by running the same tests.

   Traps: Jinja autoescape is on for this template. Leave it as is.

3. `tests/test_research_claims.py`:
   - **`finish()` (`:135-139`).** Give it a `pages=()` keyword and store
     `list(pages)`.
   - **New test** after `test_view_is_private_and_escapes_claims`:
     `test_view_hides_protected_names_in_claims_and_sources(reg)`. It uses
     `as_user("u1")`, `sent(c, None)`, and `finish` with three claims:
     1. Quote `f"{reg} runs this nightly in production"`, publisher
        `v.example`. This one is kept, and the label replaces the name.
     2. Publisher `f"{reg}.example"`. This one is dropped.
     3. A clean claim, which is kept.

     It also passes two pages: one with URL `f"https://{reg}.example/x"`,
     which is dropped, and one clean.

     The test asserts:
     - `reg` (the protected name) is not in the response;
     - "1 statement(s) not shown" is in it;
     - "1 source(s) skipped." is in it;
     - the clean claim's quote is in it.

   Verify that it fails on main: `git stash` the app changes, rebuild, and run
   the test. Then restore them and rebuild.

   Traps:
   - `reg` is the protected client's name, and the fixture deletes it.
   - Use only made-up publisher domains (`.example`).
   - The `rank` and `retrieved_at` keys must be present, because the template
     slices `retrieved_at[:10]`.

## Tests

`docker compose build app && docker compose run --rm app timeout 900 pytest -q -p no:cacheprovider`
must pass in full, and the new test must fail on main.

## Rollback

Revert the commits. Nothing is stored or migrated.

## Deviations

- **Review fix (should-fix):** the query line is the one other field the view
  prints. It is now shown as "[withheld]" when `blocked()` finds a protected
  name in it, for example when a client was made protected after the research
  was sent.
- **Review fix (nit):** `apply()` gets `str(c.get(k) or "")`, so a stored
  `null` quote doesn't raise. This is the same as the download.
- **Test:** the name check is case-insensitive, and a fourth claim names the
  client only in its URL. That makes "2 statement(s) not shown".
