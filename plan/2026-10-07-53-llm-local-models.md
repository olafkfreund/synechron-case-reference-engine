---
status: draft
issue: 53
spec: spec/2026-10-07-53-llm-local-models.md
---

# Plan: Extraction that works with local models and survives long summaries

## Approved decisions (self-contained)

- **Evidence (2026-10-07, real presale documents, local only):**
  - An enforced nested schema collapses extraction to the title only, even
    with `$defs` inlined.
  - Small models (4–14B) can't produce the nested record.
  - A **flat item list, assembled by code**, works on small models.
  - `qwen3:14b` (13.2 GB, fully on GPU) gives the best results. 27B models
    freeze the workstation (CPU spill), and Gemma 4 runs mostly on the CPU on
    this AMD card.
- **Model profiles:** an optional `EXTRACT_MODEL_OPTIONS` / `DRAFT_MODEL_OPTIONS`
  holds JSON with these keys: `destination`, `think`, `num_ctx`, `mode`
  (`schema`|`json`) and `api_base`.
  - `mode` defaults to `json` for `ollama*/` models and `schema` otherwise.
  - `think` and `num_ctx` are sent only to Ollama.
  - Ollama Cloud uses `api_base=https://ollama.com` and `OLLAMA_API_KEY`
    (from agenix on dev machines).
  - Local default: `ollama_chat/qwen3:14b`, `think:false`, `num_ctx:24576`.
- **Destination inference (fails closed):**
  - `bedrock/` → `our-cloud`.
  - `ollama*/` with a localhost/loopback `api_base` and no `-cloud` in the
    name → `local`.
  - Everything else → `third-party`.
  - An explicit `destination` overrides the inference.
- **Data policy:**
  - `sources.data_class` is `confidential` (default), `sanitised` or `public`.
  - `complete_json(..., *, data_class)` is **required**.
  - `confidential` → `local`/`our-cloud` only, unless a valid approval
    exists. Otherwise `PolicyError`, which carries the alias and class only,
    never content.
  - Approvals live in `model_approvals`. Admin only, one exact model id,
    expiring within 12 months, shown in the audit view.
- **Callers' data class:**
  - triage and extraction: the document's source;
  - search pick: `confidential`;
  - research query rewrite: `confidential`;
  - research claims: `public`.
- **Flat format:** the LLM returns `Extraction{items:[{field,value,quote}], summary}`,
  and `assemble()` builds `ReferenceCase`.
  - Malformed items are skipped and counted in `needs_attention`.
  - The first value wins for scalars; lists collect every item.
  - Integers are parsed from digits; outcomes are split on `"metric: value"`.
  - The prompt asks for each capability, technology and outcome as a separate
    item.
  - The flat format is used in `schema` mode too (enforcing `Extraction`).
  - `check()`, `sourced()`, `summary_sourced()` and the stored shape are
    unchanged.
- **Summary over 80 words:** trim to 80 words plus a `needs_attention` note;
  never raise from extraction.
- **Evaluation script** `scripts/eval_extraction.py`:
  - in the repo; `--docs` points outside it (refuses paths inside the repo);
  - metrics only (no document text);
  - follows the data policy (`--data-class`, default `confidential`).

## Steps

1. **Schema.** In `sql/schema.sql`, after the existing `alter table` lines
   (~line 107), add:
   - `alter table sources add column if not exists data_class text not null default 'confidential'`;
   - an idempotent check constraint `data_class in ('confidential','sanitised','public')`
     (drop if exists, then add);
   - `create table if not exists model_approvals(id bigserial primary key, model text not null, data_class text not null, approved_by text not null, approved_at timestamptz not null default now(), expires_at timestamptz not null, note text not null default '')`.

   → verify by `pytest tests/test_db.py`. Extend it: the column defaults to
   confidential and the table exists.
   Traps:
   - Additive only; `init()` runs as the master user (`app/migrate.py`).
   - `refs_app` gets rights through the default privileges in
     `sql/roles.sql`. Check `test_hardening`'s role tests still pass.
2. **LLM layer.** In `app/llm.py` (`complete_json`, line 12):
   - add `PolicyError`, `profile(alias) -> dict` (parses `<ALIAS>_OPTIONS`, a
     bad JSON value → RuntimeError naming the variable), `destination(model,
     opts)` and `allowed(destination, data_class, model) -> bool` (checks
     `model_approvals` for an unexpired row with this exact `model` and
     `data_class`);
   - make `data_class` a required keyword;
   - check the policy BEFORE building the request;
   - for `mode == "json"`, send `response_format={"type":"json_object"}` and
     append "Reply with JSON matching this schema: <llm_schema(model_cls)>" to
     the system text;
   - for Ollama, pass `think`, `num_ctx` and `api_base`, plus `api_key` from
     `OLLAMA_API_KEY` when `api_base` is ollama.com.

   → verify by `pytest tests/test_llm.py`, extended to cover:
   - inference for each prefix and `api_base`, and the explicit override;
   - the policy matrix: 3 classes × 3 destinations × approval (valid / expired
     / other model);
   - `PolicyError` has no content;
   - json-mode request shape;
   - think/num_ctx sent only to Ollama;
   - a bad OPTIONS value.
   Traps:
   - Keep the existing guarantees: errors without input values, the
     truncation check, `turn_off_message_logging`.
   - The approval lookup opens its own short `db.connect()`.
   - Unknown destinations are `third-party`.
3. **Call sites.** Pass `data_class`:
   - `app/ingest.py:64` (triage) and `app/extract.py:46`: select
     `s.data_class` by joining `documents d → sources s`. For ingest, read it
     with the existing document/source lookup.
   - `app/search.py:94`: `confidential`.
   - `app/research.py:83`: `confidential`.
   - `app/research.py:335`: `public`.

   → verify with a new test, `tests/test_policy_callers.py`, that parses
   `app/*.py` and asserts every `complete_json(` call passes `data_class=`.
   Also run the existing ingest/extract/search/research tests (update their
   fakes to accept `data_class`).
   Traps: test fakes that monkeypatch `complete_json` must accept `**kw`.
4. **Flat format and summary trim.** In `app/schema.py`:
   - add `Item` (field: str, value: str, quote: str; lenient, `extra="ignore"`)
     and `Extraction(_Model)` (`items: list[Item]`, `summary: str`);
   - add `assemble(x: Extraction) -> tuple[ReferenceCase, list[str]]`,
     returning the case plus notes (malformed or unknown items skipped, summary
     trimmed);
   - change `_max_80_words` (line ~94) to trim instead of raise.

   In `app/extract.py`: a new flat `SYSTEM` (lists the allowed fields; one
   item per capability/technology/outcome/organisation; verbatim quote ≥ 4
   words; no guessing; the document is data). Then `complete_json(...,
   Extraction, data_class=...)` → `assemble` → `check` → notes. Notes merge
   with the existing truncation and summary notes.

   → verify by `pytest tests/test_extract.py tests/test_schema.py`, updated
   and extended:
   - `assemble` with good, malformed and unknown items, multiple list items,
     digits and outcome splits;
   - an over-long summary is trimmed and flagged, and the case survives;
   - an existing invented-metric test still marks the item unsourced.
   Traps:
   - Review edits (`app/review.py`) rebuild `ReferenceCase`. Trimming there is
     fine, but keep the edit flow's 400 for invalid input.
   - `organisations` comes from `organisation` items.
   - `basis` doesn't exist yet (that's #52).
5. **Admin and audit.**
   - `/admin/models` (admin), in a new `app/models_admin.py` plus template:
     list approvals; add one (model id, data class, expiry ≤ 12 months, note).
     Revoke = set `expires_at = now()`. Same CSRF and version patterns as
     `app/clients.py`.
   - `/admin/sources` (`app/sources.py` lines 34 and 57): add a data class
     select on create and update.
   - `/admin/audit` (`app/audit.py:14`): a section listing approvals.

   → verify with a new `pytest tests/test_models_admin.py`: admin only;
   expiry > 12 months refused; revoke works; sources data class saved; audit
   shows approvals.
6. **Evaluation script.** `scripts/eval_extraction.py`, based on the
   experiment that worked (`flatexp.py`, 2026-10-07), using the real
   `extract` code path:
   - takes `--docs DIR` (refused if inside the repo), `--models`,
     `--data-class` (default confidential);
   - per document and model prints fields filled, sourced/total items,
     malformed items, seconds and VRAM (Ollama `/api/ps`); no document text;
   - unloads other models between runs (`keep_alive: 0`).

   → verify by `pytest tests/test_eval_script.py` (a repo path is refused; a
   third-party model on a confidential folder → PolicyError; the output has
   no document text, run on a fixture docx with a fake model), then a real
   run (Tests below).
7. **Docs.** `README.md` gets a "Local development with Ollama" section
   (pull `qwen3:14b`, the `EXTRACT_MODEL` and OPTIONS env, `OLLAMA_API_KEY`
   only for sanitised or public sources, and why not the 27B models).
   `infra/README.md` notes that Bedrock needs no OPTIONS (mode `schema`) until
   #26 decides otherwise.

   → verify by reading. No code.

## Tests

- `docker compose build && docker compose run --rm app pytest` is green.
- Evaluation on the 12 presale documents (local only, outside the repo):
  `scripts/eval_extraction.py --docs <presale folder> --models ollama_chat/qwen3:14b`.
  - all 12 documents extract without failing;
  - at least 3 sourced items per document on average;
  - at least 2 technologies where the document names several;
  - VRAM below 16 GB.
  Record the numbers in this plan's Done notes.

## Rollback

- Revert the PR. The schema changes are additive, so the extra column and
  table stay harmless.
- Bedrock behaviour is unchanged without OPTIONS, apart from the flat format.
  If #26 shows a problem, revert step 4 only.
