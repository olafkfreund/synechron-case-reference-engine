---
status: approved
issue: 231
spec: spec/2026-10-13-231-richer-research.md
---

# Plan: Research output with substance, visuals and clean formatting

Branch `feat/231-richer-research`, worktree `../refs-engine-231`. Four PRs,
in order: (a), then (b), then (c), then (d). Each PR rebases on main after
the previous one merges.

Line numbers are as of main `012beb3`. Match edits by their text, not by
the number.

## Approved decisions (from the spec)

1. **Plain-text display.** `plain()` (in `schema.py`) runs only at display
   and render time, in `research.visible_claims()`, `render.industry_section()`
   and `render.brief_section()`. It always runs **before** `anonymise.apply()`
   and `blocked()`. The stored quotes and `quote_in()` don't change.
   Technical notes are never passed through `plain()`.
2. **Quotes.**
   - 4 to 80 words (`MAX_QUOTE_WORDS = 80`). A longer quote is dropped, never
     cut.
   - `Claim.relevance` takes 1 to 3 (clamped). A claim is dropped at
     relevance 1, or when it shares no query word of 4 or more letters
     (stop words aside).
   - Sorted by `(rank, -relevance, type)`, with at most 15 per research and
     4 per page.
3. **Page budget.** `page_chars()` is derived from `num_ctx` for Ollama:
   `min(6000, ((num_ctx - MAX_TOKENS - 2000) * 3 - len(system)) // MAX_RESULTS)`,
   else 6,000. This closes #217.
4. **Technical notes** are deterministic, with no model call.
   - `notes_from()` takes fenced code blocks and runs of three or more
     numbered steps.
   - It keeps them only if the block, or the nearest heading above it,
     shares a query word.
   - Caps: 1,500 characters per note (longer is skipped), 2 per page and 8
     per research.
   - Notes are stored as claims with `type="technical_note"` and
     `statement=""`, and the quote is the block. They reuse every claim path.
   - Display: monospace (`<pre>` on pages; a fence one backtick longer than
     any run in the note in Markdown; Consolas runs in docx; first line plus
     "(see source [n])" in pptx).
5. **Images.**
   - Found with the standard library's `html.parser` in fetched HTML. Skipped:
     `data:`, SVG, and width or height under 400. Caps: 3 per page and 9 per
     research.
   - Fetched through the existing `Fetcher._get()` with a 5 MB limit; only
     PNG and JPEG content types; any public host.
   - Decoded with `diagrams._decode()`. Images under 400 px on the short side
     are dropped.
   - Stored in a new `research_images` table that cascades with the research
     row. Never copied on a cache hit.
   - Thumbnails are shown to the owner only, with a "Copyright of its owner…"
     line. Alt text passes `plain()`, `apply()` and `blocked()`.
6. **Rights.**
   - Images reach outputs only through a case: a reviewer attaches the
     research to a case and ticks images.
   - For each image the reviewer must tick the rights box, write a basis
     line, and tick "no client name or logo". `rights_by`, `rights_at` and
     `rights_basis` are recorded.
   - The image becomes a case diagram through the #200 path, with
     `credit = "Image: <title>, <host>, <url>, retrieved <date>"` rendered
     under it.
   - The reviewer's name is never in an output.
   - Briefs and the library are link-only: an "Images on the cited pages"
     list.
   - Anyone who isn't a reviewer gets 403.
7. **Generated diagrams.**
   - `Graph{nodes:[{id,label,cite}], edges:[{src,dst,label,cite}]}`, with at
     most 8 nodes, 10 edges and 40-character labels. Anything without a
     `cite` is dropped.
   - Drafted by one extra `DRAFT_MODEL` call in `brief.run`, using the same
     `<quotes>` and `data_class="public"`. Stored as `body["diagram"]`. A
     failure gives a class-only note and never fails the brief.
   - Labels are checked with `check_prose` and `blocked()`. A flag joins the
     brief's flags and blocks the download.
   - Edited as `A -> B : label [n]` and `A [n]` lines; an empty box removes
     the diagram.
   - Drawn with Pillow in `diagrams.draw()`: a layered left-to-right layout,
     at most 4 columns and 1200×600, drawn at render time and deterministic.
   - It goes in brief docx and PDF; Markdown gets the edge list.
   - It reaches a case study only as a #212 C diagram candidate when a
     library brief is attached, through the reviewer's **Use**.
8. **Phase order:** (a) → (b) → (c) → (d), one PR each.

## Steps

### Phase (a): plain-text quotes (PR 1)

**a1. `app/schema.py`, after `quote_in` (:23): `plain(s: str) -> str`.**
- Implements the spec's ordered rules:
  - links and images become their text;
  - `<url>` becomes the URL;
  - emphasis, backticks, and leading `#` or `>` markers are dropped;
  - `""x""` becomes `"x"`, one layer of wrapping quote marks is stripped,
    and whitespace collapses.
- → verify: `docker compose build app && docker compose run --rm app pytest tests/test_schema.py -k plain`.
- Traps:
  - Pure regex. Never touch text inside a technical note, so callers decide.
  - `**Zor**pale` must become `Zorpale`.

**a2. `app/research.py:170` `visible_claims()`.** Set
`c["quote"] = plain(quote)` before `apply()`, but only when
`c.get("type") != "technical_note"`. Do the same for `title`.
- → verify: `pytest tests/test_research_claims.py`.
- Traps: `picked_claims(verbatim=True)` callers that store rows (#214 step
  22) must keep the verbatim stored quote. Only the returned display copy is
  cleaned. Check every caller with
  `grep -n "visible_claims\|picked_claims" app`.

**a3. `app/render.py:59` `industry_section()` and `:99` `brief_section()`.**
Apply `plain()` to `quote` and `title` before `CTRL.sub`, `apply()` and
`blocked()`, skipping notes.
- → verify: `pytest tests/test_industry.py tests/test_brief.py`.
- Traps: `brief.check()` (`brief.py:59`) compares prose against
  `quote_texts()`. Feed it plain text too, through `quote_texts()`, so a
  brief citing a cleaned quote isn't falsely flagged.

**a4. Tests.**
- A `plain()` table of 15 cases.
- A client name split by `**` is caught by `blocked()` after `plain()`, on
  the page and in a download.
- The stored row still holds the raw Markdown, and `quote_in` still passes.
- The Markdown, docx and preview outputs have no `](`, `**` or backticks.
- Then the full suite: `docker compose build app && docker compose run --rm app pytest`.

### Phase (b): longer, ranked quotes and technical notes (PR 2)

**b1. `app/research.py:483` `Claim`:** add `relevance: int = 2`.

**b1, `:496-497`:**
- `MAX_QUOTE_WORDS = 80`, `MAX_CLAIMS = 15`, `MAX_PER_PAGE = 4`.
- Replace the constant `PAGE_CHARS` with `page_chars()`, modelled on
  `extract.chunk_chars()`. It reads `profile("DRAFT_MODEL")`, uses the
  `num_ctx` formula only for Ollama, and otherwise returns 6,000.

**b1, `CLAIMS_SYSTEM`:** use "one verbatim passage of 4 to 80 words", and
add the relevance definition.

- → verify: `pytest tests/test_research_claims.py`.
- Traps:
  - Every `PAGE_CHARS` reference must call `page_chars()` once per run and
    reuse the value for both the prompt and `quote_in`. Find them with
    `grep -n PAGE_CHARS app tests`.
  - `len(system)` includes the appended JSON schema, so compute it from the
    final system string.

**b2. `extract_claims()` (:518):**
- Drop claims whose quote is over `MAX_QUOTE_WORDS`.
- Clamp `relevance` to 1–3. Drop relevance 1, and drop claims with no
  shared query word (4 or more letters, a small stop-word set).
- Sort by `(rank, -relevance, CLAIM_TYPES.index(type))`, then apply the
  per-page cap of 4 and the total cap of 15.
- → verify: a test with a stub reply of 20 claims covering every drop
  reason.
- Traps: keep the existing numbers rule and the seen-set.

**b3. `notes_from(markdown, query)`.**
- Fenced blocks are matched with a regex on ```…```. Step runs are three or
  more consecutive lines matching `^\s*\d+[.)]\s`.
- The relevance check uses the block plus the nearest preceding `#` heading.
- Caps: 1,500 characters, 2 per page, 8 per research.
- In `run()` (:543), where pages are appended (around :570), notes become
  claims with `type="technical_note"`, `statement=""`, `quote=block`, the
  page fields, and `rank=source_rank(...)`. They're appended after the model
  claims, and the cache path computes them too.
- → verify: `notes_from` unit tests.
- Traps:
  - A note's quote must satisfy `quote_in(page_markdown, quote)` by
    construction. Assert it in the test.
  - The total cap applies to notes separately from claims.

**b4. Display, in `render.py`.**
- `render.CLAIM_TYPES` (:48) gains `"technical_note"`.
- Notes keep their raw text, with no `plain()`.
- Formats:
  - Markdown `MD` (:423) and `BRIEF_MD` (:499): a fence one backtick longer
    than the longest run in the note.
  - docx: a monospace run per line, via a `mono` flag on the item that the
    template checks. Regenerate `brand/reference.docx` and
    `brand/case_study.docx` with `scripts/make_reference_template.py`.
  - pptx, in `slide_statements` (:164): the first line plus "(see source
    [n])".
  - Pages: `research_view.html` and `output_preview.html` render notes as
    `<pre>`, autoescaped.
- → verify: `pytest tests/test_industry.py tests/test_research_claims.py`.
- Traps:
  - Regenerating templates may touch `brand/brief.docx` timestamps. Restore
    it unless a note change needs it.
  - `check_prose` treats a note as a quote in briefs, and `brief.check()`
    already gets it through `quote_texts()`.

**b5. Tests:** the page budget at 24,576 and with none, the drops and caps,
`notes_from`, notes in every format, and notes through export, brief,
library and attach. Then the full suite.

**b6. Eval, counts only, with the local model.**
- Run research end to end in the demo stack for three fixed public
  queries:
  - "MLflow model registry and experiment tracking"
  - "ServiceNow CMDB health for ITAM"
  - "Kafka exactly-once delivery configuration"
- Report per query: claims, words per quote (mean and max), notes, dropped
  as relevance 1, dropped for no shared word, and whether any page was cut.
- Compare against the same queries before b1, from a run on main.
- → verify: the counts are in the PR description.
- Traps: Brave and the fetched pages vary between runs. Report both runs
  side by side and don't chase single-digit differences.

### Phase (c): images with confirmed rights (PR 3)

**c1. `sql/schema.sql`, after `research` (:111):** add
`research_images(id bigserial primary key, research_id bigint not null references research(id) on delete cascade, page_url text not null, image_url text not null, alt text not null default '', png bytea not null, width int not null, height int not null, created_at timestamptz not null default now(), rights_by text, rights_at timestamptz, rights_basis text)`,
plus an index on `research_id`.
- → verify: `pytest tests/test_db.py`. Add the table to its `TABLES`.

**c2. `app/research.py`.**
- `ImageFinder(html.parser.HTMLParser)` collects `(src, alt, figcaption)`,
  resolves relative URLs, and skips `data:`, SVG and small images by their
  attributes.
- In `run()`, after an HTML page is fetched (not a PDF), up to 3 candidates
  go through `fetcher._get(url, diagrams.MAX_BYTES)` with the image `Accept`
  header. The content type must be PNG or JPEG.
- Each image goes through `diagrams._decode()`, is dropped under 400 px,
  and has its alt cleaned with `plain()` and capped at 200. It is inserted
  into `research_images` after the row update. 9 per research at most.
- Never on a cache hit.
- → verify: tests with the fake web.
- Traps:
  - No new fetch path. `_get` vets every hop, pins the public IP, caps
    redirects, applies robots.txt and the per-host pacing.
  - A failure is skipped silently and never fails the research.

**c3. The research page.**
- `GET /research/{rid}/images/{iid}.png` is owner-only, sends
  `PNG_HEADERS` (`review.py:448`), and returns 404 otherwise.
- `research_view.html` shows the thumbnails with their page link, alt text
  and the copyright line. Alt text, page and URL pass `apply()` and
  `blocked()`; a blocked image is hidden.

**c4. Rights through attach, in `app/review.py:561` `_attach()`.**
- It takes optional `img[]` with `rights_ok`, `basis` (required, at most 200
  characters) and `no_logo`.
- It's reviewer-only, which the attach route already enforces; everyone
  else gets 403.
- It records `rights_by`, `rights_at` and `rights_basis`, then inserts each
  image as a pending case diagram through the shared `_queue_diagram()`
  (#212 C4). `MAX_PER_CASE`, the anonymised or refused caption and
  `_decode` all apply.
- `Diagram` (`schema.py:90`) gains `credit: str = ""`. It renders under the
  image in the bid docx, the case-study Architecture section, the preview
  and Markdown.
- The picker UI on the research page gets the image checkboxes and fields.
- Traps:
  - The credit never contains the reviewer.
  - The image must belong to the posted `research_id`, which the user owns.
    Check this on the server.

**c5. Briefs and the library.** Their outputs list "Images on the cited
pages" as links (title and host) built from `research_images` of the
research and the cited pages. Never pixels.

**c6. Tests:**
- SSRF: an image at 169.254.169.254, and a redirect to 10.0.0.1. Both are
  refused.
- A non-PNG content type, an SVG, `data:`, and the size and count caps.
- Owner-only thumbnails.
- 403 for anyone who isn't a reviewer on rights.
- A confirmed image in the case study with its credit and no reviewer name.
- The library and brief are link-only.
- A cache hit has no images.

Then the full suite.

### Phase (d): generated diagrams (PR 4)

**d1. `app/diagrams.py`.**
- `parse_graph(text) -> Graph`, for the edit lines, and
  `graph_from_model(obj)`. Both enforce the caps and drop items without a
  cite, or with an unknown node.
- `draw(graph) -> bytes`: Pillow, a layered left-to-right layout by longest
  path, at most 4 columns and 1200×600, labels wrapped at 18 characters,
  rounded boxes and arrows.
- Add the `shortcut:` comment on edge crossings.
- → verify: tests that the same graph gives the same bytes and that the
  caps are applied.

**d2. `app/brief.py:98` `run()`.**
- After the brief, one `DRAFT_MODEL` call with a `DIAGRAM_SYSTEM` prompt,
  the same numbered `<quotes>`, and `data_class="public"`. Stored as
  `body["diagram"]`.
- On failure, a class-only note.

**d2, `check()` (:59):** check each node and edge label with `check_prose`
against its cited quote and all the quotes, plus `blocked()`. Flags are
listed with a `where` like `diagram: <label>`.

- Traps: diagram flags block the download like the others. The prompt says
  labels must use words from the cited quote.

**d3. The brief page.** Show the drawn PNG through an owner-only route,
plus a text box of edge lines. Saving re-parses and re-checks; an empty box
removes the diagram.

**d4. Outputs.**
- Brief docx and PDF: a "Diagram" image with "Drawn from sources [n]…"
  under it, via `InlineImage` in `brand/brief.docx`, regenerated.
- Markdown: the edge list.
- Library brief items carry the diagram.
- `_attach()` of a library brief item inserts the drawn PNG as a
  `document_images`-style candidate on the case. The plan uses a new nullable
  `research_images.case_id`, or reuses `document_images` with a null
  document. Pick the smaller one; don't add a second candidate path.
- Traps:
  - A candidate reaches the case study only through the reviewer's **Use**
    (#212 C4).
  - The credit reads "Diagram drawn by Synechron from sources [n]…".

**d5. Tests:**
- Parse, caps and draw.
- A flagged label blocks the download.
- The edit box round-trips.
- The brief docx and PDF contain the image.
- An attached library brief offers the candidate, and Use puts it in the
  case study.

Then the full suite. **Eval:** re-run the MLflow query in the demo and
check the brief has a diagram, counts only.

## Deviations

- Phase (a):
  - `plain()` collapses whitespace, so the CSV export of a quote with
    leading whitespace or a newline is now normalised (it's still
    formula-prefixed).
  - Digit-only `[n][m]` citations are kept.
  - The brief's own prose is not passed through `plain()`; only quotes and
    titles are.
  - A single `*` or `_` is removed only when it wraps words.
  - Outer quote marks are removed only when the same mark doesn't also
    appear inside the quote.
- Phase (a) review fixes:
  - `plain()` is linear: links and references can't nest, and it repeats
    until stable, so no link survives. Over 2,000 characters it skips the
    Markdown rules (whitespace only), so it never cuts a quote and can't
    split a protected name.
  - Only real page titles are cleaned, not the host or publisher fallback.
  - Brief, library and attach-from-library store the plain()+apply() text,
    as they already stored the apply() text.

## Tests

```bash
docker compose build app && docker compose run --rm app pytest
```

The suite must pass after each phase, and each PR body lists that phase's
fail-without-fix checks.

## Rollback

Revert each phase's merge. After (c), `drop table research_images`. Briefs
with `body["diagram"]` keep working once (d) is reverted, because the key is
ignored. Claims of type `technical_note` stored by (b) are dropped by the
old `CLAIM_TYPES` filter, so they're harmless after a revert.
