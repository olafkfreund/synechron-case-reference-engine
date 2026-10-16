---
status: approved
issue: 23
intent: intent/2026-10-13-23-neutral-industry-wording.md
---

# Spec: The industry context wording claims no more than the quote

## Design

Put the source first and drop the AI-chosen label. Every format uses the same
line shape:

```text
before: Supported out of the box: “<quote>” — learn.microsoft.com, https://…, retrieved 2026-10-01
after:  learn.microsoft.com states: “<quote>” — https://…, retrieved 2026-10-01
slides: learn.microsoft.com states: “<quote excerpt>” — 2026-10-01
```

**Decision 1: the publisher as stored, which is a host name.** The `publisher`
on a claim is the page's host (`research.py:462`, `:478`). Reviewer-attached
sources (#199) copy it from the claim (`review.py:375`, `:414`). So the line
reads "learn.microsoft.com states:", not "Microsoft states:". That is accurate,
and the reader can see the domain at a glance. It replaces "X" in the intent's
example.

**Decision 2: the claim type stays, but it no longer reaches an output.**
`research.py` still asks for a type and uses it to drop malformed claims
(`:470`) and to sort them (`:479`). `industry_section()` still drops a claim
with no known type (`render.py:55`), which also covers claims stored before
this change. Only the wording changes.

Changes, all in rendering:

- **`app/render.py:45-46`**: replace `PHRASES` (type → label) with
  `CLAIM_TYPES = frozenset({...the same four keys...})`. That is used only for
  the filter at `:55`.
- **`app/render.py:63-65`**: `phrase = f"{safe['publisher']} states"`. `text`
  drops `{publisher}, ` after the dash, so the publisher appears once.
- **`app/render.py:80`** (`slide_statements`): drop `{i['publisher']}, ` after
  the dash, so slides become `… — {date}`.
- **`app/render.py:370`** (Markdown template): drop `{{ i.publisher | md }}, `
  after the dash.
- **`app/templates/output_preview.html:21`**: drop `{{ i.publisher }}, ` after
  the dash.
- Word and PDF use `i.text` or the same items. Check the docx template while
  planning and change it there if it builds its own line.

The publisher sits in `phrase` before it is escaped (`| md` in Markdown, Jinja
autoescape in HTML), so escaping is unchanged.

Docs: plan step 20 (`plan/2026-10-06-1-reference-engine.md:690-699`) records
"neutral `<publisher> states:` chosen (#23)" in place of "We kept the approved
type phrases…".

## Alternatives rejected

- **Map hosts to organisation names ("Microsoft states:").** This needs a
  maintained host → organisation table, and any host outside it falls back to
  the host anyway. A wrong mapping would put a name on a claim the source did
  not make. The host is exact.
- **Drop claim typing entirely** (prompt, model, sort, stored rows). That
  changes research behaviour, the LLM schema and stored data for no change in
  output. It's out of scope; open a follow-up if the type turns out to be
  useless.
- **Keep the labels but soften them** (for example "Vendor describes as…").
  These are still AI-chosen strength claims, which is the problem #23 is about.
- **No lead-in, quote first.** This loses the "who said it" up front. The
  disclaimer alone does not attribute each line.

## Risks

- **Old outputs** keep the old labels. They are files that have already been
  generated, and nothing re-renders them (intent).
- **Docx or pptx templates that hard-code the old line shape** would show the
  publisher twice. Planning greps the templates, and the docx and pptx tests
  check the line.
- **A blank publisher** would render " states:". Every claim's publisher comes
  from a fetched page's host, so it is never blank, and `blocked()` already
  reads it. The plan adds no fallback unless a code path without a publisher
  turns up.
- No security change: the publisher and URL were already in the line and
  already checked by `blocked()` (`render.py:60`).

## Verification

- `tests/test_industry.py`:
  - `test_markdown_section_wording` asserts the line
    `learn.microsoft.com states: “<Q1>” — https://learn.microsoft.com/entra, retrieved 2026-10-01`,
    with no old label anywhere in the section.
  - `test_every_fixed_string_avoids_first_person_and_delivery_wording` drops
    `PHRASES` and adds `"states"`.
  - The docx, pdf and pptx tests assert the publisher appears once per line
    and no old label appears.
- A claim with an unknown type is still dropped (the existing test).
- The full suite stays green: `docker compose run --rm app pytest`.
