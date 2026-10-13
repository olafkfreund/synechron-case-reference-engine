---
status: approved
issue: 23
spec: spec/2026-10-13-23-neutral-industry-wording.md
---

# Plan: The industry context wording claims no more than the quote

Branch `feat/23-neutral-industry-wording`.

## Approved decisions

- Each industry statement reads `<publisher> states: “<quote>” — <url>, retrieved <date>`.
  On slides it reads `<publisher> states: “<excerpt>” — <date>`. The publisher
  appears once per line.
- `<publisher>` is the stored value, which is the page's host name (for example
  `learn.microsoft.com`). It is never mapped to an organisation name.
- The claim type stays in research and storage. In rendering it is only a
  filter: a claim with an unknown type is still dropped. No AI-chosen label
  ("Supported out of the box", "Available through configuration", "Common
  industry practice", "Vendor statement") reaches any output.
- The publisher and URL are never rewritten. Only the quote is anonymised.
  `blocked()` still checks quote, publisher, URL and date per claim.
- Outputs generated before this change are not regenerated.
- Out of scope: the research prompt, `CLAIM_TYPES` in `app/research.py`, and
  the claim JSON shape.

## Steps

1. **`app/render.py`: build the line from the publisher.**
   - `:45-46`: replace the `PHRASES` dict with
     `CLAIM_TYPES = frozenset({"out_of_the_box", "configuration", "industry_practice", "vendor_claim"})`.
     Its comment: "the type filters claims; it never reaches an output (#23)".
   - `:55`: `if c.get("type") not in CLAIM_TYPES:`.
   - `:63-65`: `phrase = f"{safe['publisher']} states"`. `text` becomes
     `f"{phrase}: “{safe['quote']}” — {safe['url']}, retrieved {safe['date']}"`.
   - `:77-80` (`slide_statements`): the line becomes
     `f"{i['phrase']}: “{excerpt(...)}” — {i['date']}"`. The
     docstring says the source comes first and the quote stays next to it.
   - `:370` (Markdown template): drop `{{ i.publisher | md }}, ` after the dash.
   - Leave `:239-243` alone. The fallback layout's "Sources: …" list of
     publishers stays.
   → verify: `grep -n "PHRASES\|i.publisher | md }}, \|\['publisher'\]}, " app/render.py`
   prints nothing.
   Traps: the Word output (`brand/reference.docx`) renders `{{ i.text }}`, so it
   follows `text` with no template edit. Do not touch the docx. Escaping is
   unchanged: `phrase` is escaped by `| md` in Markdown and by autoescape in
   docx and HTML.

2. **`app/templates/output_preview.html:21`: drop the second publisher.** The
   `<li>` becomes
   `{{ i.phrase }}: &ldquo;{{ i.quote }}&rdquo; &mdash; {{ i.url }}, retrieved {{ i.date }}`.
   → verify: `grep -n "i.publisher" app/templates/output_preview.html` prints
   nothing.
   Traps: none.

3. **`tests/test_industry.py`: assert the new wording.**
   - Add `OLD = ("Supported out of the box", "Available through configuration", "Common industry practice", "Vendor statement")`
     near the `claim()` helper.
   - `test_markdown_section_wording` (`:73-85`): replace the `PHRASES` loop
     with `assert not [p for p in OLD if p in sec]` and
     `assert "docs.vendor.io states:" in sec`. Change the line assertion to
     `"learn.microsoft.com states: “{Q1}” — https://learn.microsoft.com/entra, retrieved 2026-10-01"`.
   - `test_every_fixed_string_avoids_first_person_and_delivery_wording`
     (`:87-90`): `*render.PHRASES.values()` becomes `"states"`.
   - `test_docx_and_pdf_have_the_quote_and_source` (`:93-100`):
     `"Supported out of the box"` becomes `"learn.microsoft.com states:"`. Add
     `assert t.count("learn.microsoft.com") == 2`: one from "states:" and one
     from the URL, so the publisher is not repeated. Add
     `assert not [p for p in OLD if p in t]`.
   - `test_pptx_industry_slide_keeps_the_quote_next_to_the_phrase`
     (`:103-113`): the expected starts become
     `("learn.microsoft.com states:", "docs.vendor.io states:")`.
   - `:141` and `:195`: `startswith("Supported out of the box")` becomes
     `startswith("learn.microsoft.com states:")`.
   - `test_preview_includes_industry_and_keeps_the_ack`: add
     `"learn.microsoft.com states:" in` the preview HTML, if it does not
     already check the line.
   → verify: `docker compose run --rm app pytest tests/test_industry.py` passes.
   Traps: `test_citation_is_never_rewritten_and_client_site_is_dropped`
   (`:213`) must still pass unchanged. A protected client's site as the
   publisher is still dropped, and its host must not leak via "states:".

4. **`plan/2026-10-06-1-reference-engine.md:690-699`: record the decision.**
   Replace the fixed-phrase bullet and "We kept the approved type phrases…" with:
   "Each item is the source's host, then `states:`, then the VERBATIM quote,
   URL and date (#23: neutral wording replaced the AI-typed phrases, which
   could overstate the quote). The claim type only filters claims."
   → verify: `grep -n "Supported out of the box" plan/2026-10-06-1-reference-engine.md`
   only shows lines that describe the history, if any.
   Traps: do not rewrite other steps.

## Tests

```bash
docker compose run --rm app pytest tests/test_industry.py
docker compose run --rm app pytest
```

Both should pass, with no AWS or Bedrock involved.

## Rollback

Revert the branch's code commits (steps 1-3). The step 4 plan note goes back
with them. Nothing is stored differently, so no data migration is needed.
