---
status: draft
issue: 53
intent: intent/2026-10-07-53-llm-local-models.md
---

# Spec: Extraction that works with local models and survives long summaries

## Design

### Evidence this design rests on (2026-10-07, real presale documents, local only)

| Request shape | Result |
| ------------- | ------ |
| Enforced nested schema, 27B models | Title only (constrained decoding collapses) |
| Same, `$defs` inlined | Title only |
| JSON mode + nested schema in prompt, `qwen3.8:27b` | 21 items, 15 sourced; but the 27B model overflows the 20 GB GPU and freezes the workstation |
| JSON mode + nested schema in prompt, 4–14B models | All fail validation (wrong shapes) |
| **JSON mode + flat item list, assembled by code**, 4–14B models | **Works on all three.** `qwen3:14b`: 5–9 sourced items per document, 14–30 s, 13.2 GB, fully on GPU |

So: small models extract reliably when asked for a **flat list of facts**,
and our code builds the strict `ReferenceCase` from it.

### 1. Model profiles (`app/llm.py`)

Each alias (`EXTRACT_MODEL`, `DRAFT_MODEL`) gets an optional JSON options
variable (`EXTRACT_MODEL_OPTIONS`, `DRAFT_MODEL_OPTIONS`):

```json
{"destination": "local", "think": false, "num_ctx": 24576, "mode": "json", "api_base": "http://localhost:11434"}
```

- **`mode`**: `schema` (enforced structured output, today's behaviour) or
  `json` (JSON mode, with the shape described in the system prompt).
  - Ollama models default to `json`; everything else to `schema`.
  - #26 decides whether Bedrock should switch too.
- **`think`** and **`num_ctx`** are passed through only to providers that
  accept them (Ollama).
- **`destination`**: `local`, `our-cloud` or `third-party`. If unset it is
  inferred, failing closed:
  - `bedrock/...` → `our-cloud`
  - `ollama*/...` with a localhost or loopback `api_base` and no `-cloud` in
    the name → `local`
  - anything else, including any `-cloud` model and `api_base=https://ollama.com`
    → `third-party`
- **Ollama Cloud** uses `api_base` `https://ollama.com` and the key from
  `OLLAMA_API_KEY`, provided by the existing agenix secret on dev machines.
- **Local default for development:** `ollama_chat/qwen3:14b` with
  `think: false` and `num_ctx: 24576`. It fits fully in 20 GB of VRAM.
  - The 27B and 26B models are not recommended on this workstation: they spill
    onto the CPU and freeze it.
  - Gemma 4 ran mostly on the CPU on this AMD card.

### 2. Data policy (enforced before every AI call)

- **Sources:** an additive column
  `sources.data_class text not null default 'confidential'`, with the check
  `confidential | sanitised | public`. It is set on `/admin/sources`; existing
  sources become `confidential`.
- **`complete_json(alias, system, user, model_cls, *, data_class)`:**
  `data_class` is **required, with no default**, so every caller has to
  declare what it is sending.
- **Rule:**
  - `public` and `sanitised` may go to any destination.
  - `confidential` goes to `local` or `our-cloud` only, unless there is a
    valid approval.
  - Anything else raises `PolicyError` before the request is built. Only the
    model alias and data class go in the message, never content.
- **Approvals:** a table
  `model_approvals(model, data_class, approved_by, approved_at, expires_at, note)`.
  - Managed on a small admin page (`/admin/models`, admin role).
  - Each approval names one exact model id and expires after at most
    12 months. *Default answer to the intent's open question; change at
    spec review.*
  - Every approval is shown in the audit view.
- **Data class per caller:**

| Call | `data_class` |
| ---- | ------------ |
| Triage, extraction (`ingest.py`, `extract.py`) | the document's source |
| Bid search pick (`search.py`) | `confidential` (case records) |
| Research query rewrite (`research.py:83`) | `confidential` (free text a user typed) |
| Research claims (`research.py:335`) | `public` (the scrubbed query plus fetched public pages) |

### 3. Flat extraction format (`app/extract.py`, `app/schema.py`)

- **What the LLM sees:** a new small model,
  `Extraction{items: list[Item], summary: str}`, where
  `Item{field, value, quote}` and `field` is one of the `ReferenceCase` field
  names in singular form (`capability`, `technology`, `outcome`,
  `organisation`, plus the scalars).
- **Building the record:** `assemble(extraction) -> ReferenceCase` in code.
  - Items that are malformed or have an unknown field are skipped and counted
    in `needs_attention` (e.g. "3 extracted items were malformed and ignored").
  - The first value wins for each scalar field.
  - List fields collect every item.
  - Integers are parsed from digits.
  - Outcomes are split on `"metric: value"`.
- **Prompt:** asks for every capability, technology and outcome as a
  **separate** item (the experiment got exactly one of each), with a verbatim
  quote of at least 4 words, and no guessing. The document stays wrapped as data.
- **Unchanged:** `check()`, `sourced()`, `summary_sourced()`, the review
  flow, and the stored `ReferenceCase` shape. Assembly is the only new step.
- **Both modes:** the flat format applies in `schema` mode too, with the
  `Extraction` schema enforced. A flat schema is simpler for any provider
  and removes the nested-`$defs` dependency.

### 4. Over-long summary (`app/schema.py`)

The 80-word validator **trims** to the first 80 words and records
`needs_attention: "summary trimmed to 80 words"`, instead of raising. Trim
rather than blank: it keeps something usable, and `summary_sourced()` still
blanks it if it uses unsourced numbers. *Default answer; change at review.*

### 5. Development evaluation (`scripts/eval_extraction.py`)

- **In the repo**, as a tool. *Default answer.*
  - `--docs <dir>` points at a folder outside the repo (never committed: the script refuses a path inside the repo).
  - `--models` takes a list of aliases or model ids.
  - It runs triage plus extraction per document and prints **metrics only**:
    fields filled, items sourced/total, malformed items, time, VRAM used
    (from Ollama `/api/ps`). No document text.
- The data policy applies here too:
  - the folder is `confidential` unless `--data-class sanitised|public` is
    given;
  - a third-party model on a confidential folder fails with `PolicyError`.

## Alternatives rejected

- **Enforced nested schema with local models:** extraction collapses to the
  title.
- **27B and 26B models locally:** good quality, but they overflow 20 GB of
  VRAM and freeze the workstation. They stay available as options, not the
  default.
- **Lenient coercion of malformed nested JSON** (wrap a plain string into
  `{value, source_quote: ""}`): the quotes would be lost, so every field would
  end up unsourced.
- **Hard-banning cloud models:** the user wants external models possible in
  future scenarios. A recorded, expiring, model-specific approval keeps that
  open without accidents.
- **Per-call `data_class` defaulting to confidential:** silent defaults hide
  mistakes. Required parameters make every caller visible in review.

## Risks

- **A wrong `destination` inference sends confidential text off the machine.**
  Mitigations: inference fails closed (unknown → third-party), a test covers
  every provider prefix, and an explicit `destination` in the options
  overrides inference.
- **JSON mode output is less constrained.** The `Extraction` model still
  validates the envelope, and code-side assembly skips bad items, so nothing
  unvalidated reaches the record.
- **Bedrock behaviour with the flat format is unverified.** #26 runs it on
  Claude in both modes before staging.
- **Small models extract less.** That's acceptable for development; production
  quality is judged on Bedrock (#26, #28).

## Verification

- **`pytest`:**
  - destination inference for each prefix and `api_base`, with an explicit
    override;
  - the policy matrix (3 classes × 3 destinations, with and without a valid,
    expired or other-model approval);
  - `PolicyError` carries no content;
  - every `complete_json` call site passes `data_class` (a test greps for it);
  - `assemble()` with good, malformed and unknown items, multiple list items,
    digit parsing and outcome splitting;
  - an over-long summary is trimmed and flagged, and the case survives;
  - think, num_ctx and mode reach LiteLLM only for Ollama;
  - the `/admin/models` page is admin-only, and approvals expire.
- **Evaluation:** `scripts/eval_extraction.py --docs <presale folder> --models
  ollama_chat/qwen3:14b` on the 12 real documents. Every document is
  extracted with no failures; at least 3 sourced items per document on
  average; at least 2 technologies listed where the document names several;
  VRAM stays below 16 GB.
