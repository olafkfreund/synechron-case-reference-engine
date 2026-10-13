---
status: draft
issue: 235
intent: intent/2026-10-13-235-library-in-bids.md
---

# Spec: The Library works for you during a bid

## Design

Two PRs:
- **PR 1:** suggestions, research from the Library, and browse. These are
  read and UI only, with no change to outputs.
- **PR 2:** library items in bid downloads. This touches `render.build()`,
  the templates and every format.

PR 1 is useful on its own and low risk. PR 2 carries the output-safety
review.

### 1. Suggestions in Find references (PR 1)

- **`library.suggest(bid_text, filters, clients) -> list[dict]`** in
  `app/library.py`. There is no model call.
  - **Keywords:** words of 4 or more letters from the bid text, lowercased,
    minus a short fixed stoplist (`SUGGEST_STOP`, for example "will",
    "with", "that", "from", "this", "their", "have", "should"), plus the
    non-empty filter values. At most 30 keywords.
  - **Candidates:** the newest 300 `library_items`, the same table reads as
    `index()`. For each, take `_visible_text()` (topic, plus quotes and brief
    text after `apply()` and `blocked()`), the #214 visible-text rule, so a
    withheld item or quote never matches.
  - **Score:** the number of distinct keywords found in the visible text as
    whole words. An item needs at least 2, or 1 when there are 2 or fewer
    keywords. Results are sorted by score and then newest, and capped at 5
    (`SUGGEST_MAX`).
  - **What it returns** per item: id, the topic as shown (or `[withheld]`),
    kind, number of visible sources, age, the stale flag, and the score.
- **`search.py` `search_page`** (the POST with bid text) calls `suggest()`
  and passes `library=` to `search.html`. The browse GET (no bid text) gets
  no suggestions; the suggestions answer a bid.
- **`search.html`:** a "From the library" card above "Other matches" lists
  each item:
  - a link to `/library/{id}`
  - a kind badge, the number of sources, the age and a re-check badge
  - an "Include" checkbox (`library_ids`) on the shared "Download all"
    form, and the per-case forms (PR 2 wires these up; in PR 1 the
    checkboxes aren't rendered)
- **The bid text stays in the POST.** `suggest()` reads it in memory and
  never logs or stores it.
- `shortcut: candidates are the newest 300 items, scored in Python; move to a
  stored tsvector over the visible text once the library passes a few
  thousand items.`

### 2. Research from the Library (PR 1)

- **The Library page** gets a "Research a new topic" card: the same
  `<form method="post" action="/research">` with a `question` textarea as
  `research.html`. Research then runs its normal query preview and send. No
  new route.
- **`research_view.html`:** the existing "Library topic" input is pre-filled
  with the research query (`value="{{ query }}"`). The query has already been
  scrubbed of client names by `build_query`/`finalize`. Saving goes to the
  existing `/library/save`, which redirects to the new item.
- There's no return-to-library flag: Save to library already lands you on the
  item. One word less of state.

### 3. Browse by topic (PR 1)

- **`index()`** already loads up to 1,000 rows and computes each shown topic.
  It also builds `topics`, the case-insensitive count of shown topics,
  excluding `[withheld]`, top 20 by count then name. They render as chips
  that link to `/library?topic=<topic>`.
- **New GET filters**, all optional and combined with `q`:
  - `topic`: an exact, case-insensitive match on the shown topic
  - `kind`: `brief` or `quotes`
  - `due=1`: the stale (re-check) items only

  Ordering stays newest first.
- **Saved by:** `library_items` gets
  `created_by_name text not null default ''` (`add column if not exists`).
  `/library/save` stores `user.name`, the display name the header already
  shows. The list and item pages show "Saved by <name>", or "Saved by a
  colleague" for older rows that have no name. Display names are internal
  and already visible to every signed-in user in this portal's design.

### 4. Library items in bid downloads (PR 2)

- **Form:** `/preview` and `/generate` take `library_ids: list[int] =
  Form([])`, at most 3, deduplicated. The ids come from the form, so the
  server loads each row and returns 404 for an unknown id. Every user may read
  every library item (the #214 decision), so no ownership check is needed.
- **`render.build(conn, case_ids, research_id, ack, user, fmt=None,
  library_ids=())`:**
  - **Quote items:** `research.visible_claims(body["quotes"], clients)`
    joins `merged` after the attached sources and the research claims, with
    the same dedupe on `(url, quote)`. They render in the existing grouped
    and cited industry section, with no template change.
  - **Brief items:**
    - `brief_section(body["brief"], body["quotes"], clients)` produces a
      section dict, as the library page and download already do.
    - The dicts are collected in `industry["briefs"]`, or in `briefs` when
      there's no industry section.
    - A `Withheld` brief is dropped and counted in the industry note
      ("1 library brief omitted: it named a protected client").
  - **Without a case:** library items alone count like `research_id`: an
    output with no case needs `industry_context_ack`, and an output with
    nothing usable gives 400.
- **Rendering briefs:**
  - Markdown, the preview and both docx templates (`reference.docx`,
    `case_study.docx`, regenerated by `scripts/make_reference_template.py`)
    get a loop after the industry section. Each brief shows:
    - its title
    - the disclaimer
    - the key takeaways
    - its themes, each with its own `[n]` citations
    - its own numbered source list

    This is the same structure as `brand/brief.docx`, so citations stay
    local to their brief and no renumbering is needed.
  - **pptx:** shows each brief's title and takeaways on the industry slide
    (or the fallback layout), capped like the other slide lines.
  - **PDF:** follows docx.
- **Preview:** it renders exactly the same `industry` and `briefs`, from the
  same `build()` call. The preview's download buttons carry the
  `library_ids` hidden inputs, so download equals preview.
- **Search card:** the "Include" checkboxes are rendered on the forms, and
  the preview and download buttons send them.
- **Audit:** the `generations` row stores the library ids in a new
  `library_ids bigint[]` column (`add column if not exists`), next to
  `research_id`.

## Alternatives rejected

- **A model to match library items to the bid.** That's slow on the local
  model and calls a model for every search. The intent forbids it.
- **Merging brief citations into the industry section's numbering.** It
  would need remapping every `[n]` in the brief's text, which is error-prone.
  Separate brief blocks keep each brief's own numbering, as in its
  standalone download.
- **A return-to-library flag on research.** Save to library already lands
  on the item.
- **Showing saved-by from `created_by` (the sub).** It's an opaque id with
  no user table to resolve it; storing the display name at save is one
  column.
- **Suggestions on the browse GET.** They're there to answer a bid; browsing
  already has the Library page.

## Risks

- **Another user's research query** isn't exposed: suggestions show the
  topic and counts, never the stored query. The #214 creator-only re-check
  rule is unchanged.
- **Protected names:** matching uses the visible text only, so a name that is
  withheld now can't produce a hit. Rendering re-runs `apply()` and
  `blocked()` per item, and a withheld brief is dropped and counted. The bid
  text is never shown back except in its own form, as today.
- **Latency:** `suggest()` scans at most 300 items. `_visible_text()` on a
  brief runs `brief_section`, which runs `apply()` and `blocked()` per
  sentence. The PR measures it on 300 items with `explain`-style timing in a
  test-client run; if it exceeds about 150 ms, cache visible text per item
  for the request, or lower the candidate cap.
- **Mixing delivery and public content:** library items render only inside
  the industry part, under the disclaimer, after the cases, and never in a
  case's own sections.
- **Template drift:** both docx templates are regenerated; `brief.docx` must
  come out unchanged (the rebuild-and-restore trap from #212 E).

## Verification

- **PR 1:**
  - `suggest()`: a quote item matching two bid words is suggested, while one
    matching only a stoplist word isn't. A client protected after saving
    stops a match. The cap is 5. No model is called (call recorder).
  - The search POST shows the "From the library" card; the browse GET
    doesn't.
  - Library page: topic chips with counts, and the `topic`, `kind` and `due`
    filters. Saved-by shows the name, or "a colleague" for old rows.
  - The research view pre-fills the topic with the query.
- **PR 2:**
  - A quote item in a docx, md or pptx download appears in the cited
    section. A brief item renders with its own sources.
  - The preview equals the download.
  - A withheld brief is dropped and counted.
  - An unknown id gives 404, and more than 3 ids give 400.
  - Library items alone, without the acknowledgement, give 400.
  - The audit row has the `library_ids`.
- **Full suite:** `docker compose build app && docker compose run --rm app
  pytest`.
- **By hand in the demo:** save a library item, search with a matching bid
  text, include it, and download Word.
