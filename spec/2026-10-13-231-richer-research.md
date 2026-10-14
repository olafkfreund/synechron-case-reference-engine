---
status: approved
issue: 231
intent: intent/2026-10-13-231-richer-research.md
---

# Spec: Research output with substance, visuals and clean formatting

The work ships in four phases, (a) to (d), each a PR of its own and useful
alone. (a) is a small fix. (b) to (d) build on it.

## Design

### (a) Plain-text quotes (display only)

- **`plain(s)`** is new in `app/schema.py`, next to `quote_in`, because it is
  pure and every module already imports schema. In order, it:
  - turns `![alt](url)` into `alt`, and `[text](url)` or `[text][ref]` into
    `text`;
  - turns a bare `<https://…>` into the URL itself;
  - drops `**`, `__`, and a single `*` or `_` used as emphasis around a word;
    drops backticks;
  - drops leading `#` or `>` markers;
  - turns `""x""` into `"x"`, and strips one layer of quote marks wrapping
    the whole quote (the output adds its own “ ”);
  - collapses whitespace.

  A table of about 15 inputs and their outputs pins it down in the tests.
- **Where it runs: exactly two places,** so every view and output gets it.
  - `research.visible_claims()`: the research page, export, library and
    attach.
  - `render.industry_section()` and `render.brief_section()`: every
    download, the preview and the slides.

  In both, `plain()` runs **before** `anonymise.apply()` and `blocked()`.
  This way the protected-name check sees the text the reader sees, so a
  client name split by `**` markup can't slip through.
- **The stored quote and `quote_in()` don't change.** Claims stay verbatim
  in `research.results` and `cases.public_sources`, and the page check
  still matches the raw Markdown.
- Technical notes from (b) are **never** passed through `plain()`, because
  code must stay exact.

### (b) Longer, ranked quotes and technical notes

**Passage length.**
- `CLAIMS_SYSTEM` allows "one verbatim passage of 4 to 80 words (a
  sentence or a short paragraph)".
- `MAX_QUOTE_WORDS = 80` is enforced in `extract_claims()`. A longer quote is
  dropped, not cut, because cutting would no longer be verbatim.
- `MIN_QUOTE_WORDS` stays 4.

**Context budget (this also fixes #217).**
- `PAGE_CHARS` becomes a function, the way `extract.chunk_chars()` is: with
  Ollama and `num_ctx` set it is
  `min(6000, ((num_ctx − MAX_TOKENS − 2000) × 3 − len(system)) // MAX_RESULTS)`,
  else 6000.
- At 24,576 tokens that gives about 5,600 characters per page, which fits
  8 pages with no silent cut.
- Longer quotes cost output tokens, not input, so `MAX_TOKENS` (8,192) still
  holds about 15 claims of 80 words.

**Ranking.**
- `Claim` gains `relevance: int`, defined in the prompt as "1 = tangential,
  2 = relevant, 3 = directly answers the query". Values outside 1 to 3 are
  clamped.
- **Dropped:** claims with relevance 1, plus a cheap check that the quote
  shares no word of 4 or more letters with the query (after stop words).
  A claim is dropped if either holds.
- **Sorted** by `(source_rank, −relevance, type)`.
- **Capped:** at most 15 claims per research, and at most 4 from one page.
- The cap and threshold are constants in one place.

**Technical notes** are deterministic; there's no model call.
- `notes_from(markdown, query)` reads each fetched page's Markdown (the
  same text claims are checked against) and takes:
  - fenced code blocks (```…```), which covers code, CLI and config;
  - runs of three or more numbered list items (steps).
- **Relevance:** a block is kept only if it, or the nearest heading above
  it, shares a word of 4 or more letters with the query.
- **Caps:** 1,500 characters per note (a longer block is skipped, not cut),
  at most 2 per page and 8 per research.
- **Stored** in `results["claims"]` as claims with
  `type: "technical_note"`, `statement: ""`, `quote: <block text>`, the
  page's url, publisher, title and date, and `rank` from `source_rank`. A
  note *is* its quote, so `quote_in` holds by construction.
- **They reuse the whole claims path:** select, export, brief citations
  `[n]`, library save, attach to a case, `visible_claims`, `apply()`,
  `blocked()`, and the per-page grouping and source list.
- `render.CLAIM_TYPES` and the research page accept `technical_note`.
- **Rendering:** notes show as monospace blocks, not in “ ”:
  - research page and preview: `<pre>`, autoescaped;
  - Markdown: a fence one backtick longer than any run in the note;
  - docx: a monospace run (Consolas) per line;
  - pptx: the note's first line plus "(see source [n])".
- **In briefs,** a note can be cited, and `check_prose` checks names and
  numbers against it as against a quote.

### (c) Images from public pages, only with confirmed rights

**Fetching** happens in `run()`, after a page's HTML is fetched. PDFs are
skipped.
- **Finding candidates:** `html.parser` from the standard library finds
  `<img src>` and `<figure><img>`, with `alt` and any `<figcaption>`.
  Candidates come in page order and are skipped when they are:
  - `data:` URIs;
  - SVG, or `.svg` URLs;
  - images with `width` or `height` attributes under 400;
  - more than 3 per page or 9 per research.
- **Downloading:** each image goes through the existing SSRF-safe
  `Fetcher._get()`. Every hop is vetted, there are at most 3 redirects, a
  public IP is pinned, robots.txt is checked, and the one-request-per-
  second-per-host rule applies. On top of that:
  - the byte limit is `diagrams.MAX_BYTES` (5 MB);
  - an `Accept: image/png,image/jpeg` header is sent;
  - the content type must be PNG or JPEG.
- **Host:** any public host is allowed, since images often live on CDNs. The
  vetting is what makes this safe; a same-origin rule would add nothing.
- **Decoding:** `diagrams._decode()` re-encodes to PNG, which refuses bombs
  and drops EXIF, ICC and text chunks. Images under 400 px on the short
  side are dropped as icons.
- **Storage:** a new table `research_images(id, research_id → research on
  delete cascade, page_url, image_url, alt, png, width, height, created_at,
  rights_by, rights_at, rights_basis)`.
- **Alt text and captions** are cleaned with `plain()` and capped at 200
  characters.
- **Never in the cache:** images are never copied into a cached research
  row. A cache hit refetches nothing and has no images.

**Showing images.**
- On the research page, the owner sees thumbnails, served with
  `PNG_HEADERS`, each with its page link and alt text.
- Alt text goes through `apply()`/`blocked()`. If it's blocked, or the page
  or URL is blocked, the image isn't shown.
- Every thumbnail says "Copyright of its owner. Not for outputs until a
  reviewer confirms rights."

**Rights.** This is the only way an image reaches an output.
- Images reach outputs **only through a case.** A reviewer attaching the
  research to a case (the #214 step-22 picker) can tick images too. For
  each one they must:
  - tick "I confirm we have the right to use this image in customer
    material";
  - fill a required basis line, such as "vendor media kit, reuse with
    credit" or "CC BY 4.0";
  - tick "it shows no client name or logo".
- The server records `rights_by`, `rights_at` and `rights_basis` on the
  image row, and copies the image into the case as a diagram through the
  #200 path. `_decode` runs again, `MAX_PER_CASE` applies, and the caption
  is anonymised or refused.
- **Credit:** the diagram carries `credit = "Image: <page title>, <host>,
  <url>, retrieved <date>"`. It renders under the image in every format, and
  the case study's Architecture section shows it. The output never names
  the reviewer; only the audit page does.
- **Briefs and the library** stay link-only. Their outputs gain an
  "Images on the cited pages" list of links (page title and host), never the
  pixels.
- **Plain users** can't confirm rights. The checkbox and route are
  reviewer-only, and anyone else gets 403.

### (d) Generated diagrams from cited facts

**The graph spec:** `Graph{nodes: [{id, label, cite}], edges: [{src, dst,
label, cite}]}`.
- Limits: at most 8 nodes and 10 edges, and labels of at most 40
  characters.
- Every node and edge has a `cite` (a quote number `[n]` of the brief), or
  it is dropped.

**Drafting.**
- One extra `DRAFT_MODEL` call when a brief is written (`brief.run`), using
  the same numbered `<quotes>` input and `data_class="public"`, with a short
  `DIAGRAM_SYSTEM`. It is stored as `body["diagram"]`.
- A failure leaves no diagram and a class-only note; it never fails the
  brief.

**Checks.**
- Each label is checked with `check_prose(label, cited_quote, all_quotes)`
  (#212 B, with #214's sentence-start rule) and `blocked()`.
- A flagged node or edge is listed with the brief's flags, so it blocks the
  download like any other flag.
- Edges to unknown nodes are dropped.

**Editing,** on the brief page.
- A text box holds one line per edge, as `A -> B : label [n]`, and lone
  nodes as `A [n]`. The page parses it, applies the same caps and checks,
  and redraws.
- Clearing the box removes the diagram.

**Drawing.**
- `diagrams.draw(graph) -> png` uses Pillow, which is already installed, so
  nothing new is added.
- The layout is a layered left-to-right layout by longest path from the
  sources, giving at most 4 columns. Nodes are rounded boxes, with edges as
  straight arrows plus labels, and node labels wrap at 18 characters.
- Output is 1200×600 at most. It's drawn at render time and never stored,
  and is deterministic for the same graph.
- One `shortcut:` comment notes that crossing edges aren't avoided. That's
  acceptable at 8 nodes, and a real layout engine is for a later issue.

**Where it goes.**
- Brief docx and PDF: an image under "Diagram", with "Drawn from sources
  [n]…" underneath. Markdown gets the edge list as text, since it has no
  image.
- Library brief items carry it.
- When a library brief item is attached to a case (#214 step 22), its
  diagram is offered as a **case diagram candidate** (#212 C), and the
  reviewer chooses **Use**, which goes through the #200 path. That's how it
  reaches a case study, as our own artwork with its sources listed.

## Alternatives rejected

- **Clean the stored quote.** That breaks `quote_in` and the verbatim rule,
  so cleaning is for display only.
- **Re-fetch pages with a headless browser for images.** It's heavy, and a
  new attack surface. `html.parser` on the HTML we already fetched is enough.
- **Embed page images in briefs and the library.** It spreads third-party
  copyright with no reviewer in the loop. Links only.
- **SVG images.** Script and external-reference risk, plus a new converter.
  Out of scope.
- **Mermaid or Graphviz for diagrams.** A new runtime or binary in the
  worker image. Pillow is already there.
- **Model-written technical notes.** They wouldn't be verbatim, so they
  can't be checked. Deterministic extraction is both safer and cheaper.

## Risks

- **Copyright and brand.**
  - Image rights rely on a reviewer's honest confirmation. Mitigations: a
    recorded basis, a credit line, reviewer-only rights, link-only
    elsewhere, and an audit row listing every confirmation.
  - A confirmed image could still show a logo. The checkbox and the
    reviewer's eye are the control.
- **SSRF through image URLs.** Each image hop goes through `vet_url`,
  `public_ip` pinning and the redirect cap, as pages do. There's no new
  fetch path. A test serves a page whose image points at 169.254.169.254
  and at a redirect to 10.0.0.1, and both must be refused.
- **Fetch time.** Up to 9 more requests per research, each limited to one
  per second per host and 10 seconds. A research job can take about 30
  seconds longer.
- **Local context.** Passages raise output tokens. The 15-claim cap and the
  page budget keep input plus output inside `num_ctx`. Diagram drafting is a
  separate small call.
- **Size.**
  - `research_images` holds up to 9 × 5 MB per research row. The cleanup
    rides on research deletion (cascade).
  - Notes add at most 8 × 1.5 KB to `results`.
  - Briefs store graph JSON only.
- **Ranking can drop a useful claim.** The model's relevance score is
  noisy. Only clearly tangential claims (relevance 1, or no shared word)
  are dropped, and the constants are tunable.
- **`plain()` changes what readers see.** Link targets disappear from the
  quote text. The source list still carries the page URL, and the stored
  quote keeps them.

## Verification

**Unit tests:**
- `plain()`'s table, including a client name split by `**` that
  `blocked()` must still catch after `plain()`.
- The `MAX_QUOTE_WORDS` drop.
- The page budget at a `num_ctx` of 24,576 (fits) and with none (6,000).
- The relevance drop and sort.
- `notes_from()`:
  - fenced code is kept;
  - a 3-step list is kept and a 2-item list isn't;
  - the size and count caps;
  - an off-topic block is dropped.
- The image parser:
  - skips `data:`, SVG and small images, and stops at the cap;
  - an SSRF image URL is refused;
  - a non-PNG content type is refused.
- Graph parsing, caps, flagged labels and drawing, which is deterministic:
  the same graph gives the same bytes.

**Route tests:**
- The research page shows plain quotes, notes in `<pre>`, and thumbnails.
- Only reviewers can confirm rights; anyone else gets 403.
- A confirmed image reaches the case study with its credit, and the
  reviewer's name never appears in the output.
- Briefs and the library list image links but never embed images.
- A brief diagram renders in docx and PDF, a flagged label blocks the
  download, and the edit box round-trips.

**Eval:** re-run the MLflow query from the screenshot in the demo. Expect:
- no Markdown syntax in the preview;
- fewer, longer quotes with no dependency trivia;
- at least one technical note;
- image candidates on the research page;
- a diagram in the brief.

**Full suite:** `docker compose build app && docker compose run --rm app pytest`.

## Decisions for approval

1. Images reach outputs **only through a case**, with reviewer-confirmed
   rights (a basis line plus a "no client logo" tick) and a credit line.
   Briefs and the library are **link-only** for images.
2. Technical notes are **deterministic** (fenced code and step lists), with
   no model call.
3. The ranking keeps relevance 2 and 3 only, with at most 15 claims per
   research and 4 per page. Quotes are at most 80 words.
4. The per-page context budget comes from `num_ctx`, which also closes #217.
5. Diagrams are drawn with Pillow from a cited graph and edited as plain
   text lines. They reach case studies only as a diagram candidate the
   reviewer chooses to use.
6. The phase order is (a), (b), (c), (d), each its own PR.

## Revision 2026-10-14: phase (d) reuses the architecture renderer (approved by the user 2026-10-14)

**Why.** Phase (d) was designed before #246 shipped `app/archdiagram.py`,
the customer-grade renderer behind case architectures:
- zones, kinds and icons
- a grid router
- SVG, PNG, native PowerPoint shapes and Mermaid output

The approved (d) design adds a **second** drawer: Pillow boxes and arrows,
4 columns, 1200×600. The user rejected that kind of output for customers
on 2026-10-13 ("just boxes without any substance").

**Change.** A brief's diagram becomes an `Architecture`-shaped object and
is drawn by `archdiagram`. There is no new drawer.
- **What the model returns.** `components` (name, role, zone, kind, cite)
  and `flows` (src, dst, label, mode, cite), from the brief's numbered
  `<quotes>`. It uses the same `ARCH_SYSTEM` caps (6–12 components, at most
  14 flows) and the same rule: names copied from the cited quote.
- **Checks.** `check_architecture` and `prune_architecture` are reused,
  with the cited **public quotes** as the evidence, together with
  `blocked()`. The flags block the download, as the plan says.
- **Editing.** The same line editor as the case architecture (`title:` /
  `component:` / `flow:`), parsed by the existing `parse_architecture`.
- **Outputs.**
  - Brief docx and PDF: a PNG via `archdiagram.to_png`, with "Diagram
    drawn by Synechron from sources [n]…" under it.
  - Markdown: the Mermaid text.
  - The library brief carries the diagram.
  - Attaching it to a case offers it as a `document_images` candidate
    with a null document. It reaches the case only through the reviewer's
    **Use**, as approved.
- **What doesn't change.** `DRAFT_MODEL`, `data_class="public"`, the
  class-only failure note, the owner-only route, the credit text, and the
  reviewer-only path into a case.

**Rejected.** The approved Pillow drawer. It would duplicate layout, text
wrapping and routing, and it gives the look the user turned down.

