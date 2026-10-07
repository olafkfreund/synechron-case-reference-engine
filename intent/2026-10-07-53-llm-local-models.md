---
status: draft
issue: 53
author: olafkfreund
---

# Intent: Extraction that works with local models and survives long summaries

## Problem

The first run on real presale documents used local Ollama models
(`qwen3.8:27b`, `gemma4:26b`) on 2026-10-07. It showed that the LLM layer
only works in the shape our mocked tests assume:

- **Enforced structured output collapses extraction.** With the JSON schema
  enforced, both models returned almost empty records: a title and little
  else, from SOWs that plainly list scope, technology and team. Given the
  schema in the prompt instead, with JSON mode on, the same model on the same
  SOW extracted 7 fields, 4 capabilities, 8 technologies and 2 outcomes; 15 of
  those 21 items passed our quote check.
- **Local models think silently, and get a small context window by default.**
  The thinking used up the output budget, and the default context cut the
  5,000–10,000-word documents short. Turning thinking off and setting a 32K
  context fixes both, but the code can't pass those settings today.
- **Small models can't produce our nested record.** Asked for the full record
  (every field an object with a value and a quote), `gemma4:e4b`, `gemma4:12b`
  and `qwen3:14b` all returned malformed shapes, such as a title as plain text
  or extra keys. Validation rejects them, so nothing is extracted. Large models
  (27B) manage the shape, but they overflow the 20 GB GPU onto the CPU and
  freeze the workstation.
- **One over-long summary loses the whole case.** A summary over 80 words
  fails validation, and every other field extracted from that document is
  thrown away with it.

Until this is fixed, nothing can be evaluated on real documents without
Bedrock, including the SOW change in #52.

## Proposed outcome

- Each model alias can carry its own settings: thinking on or off, context
  size, and how structured output is requested (enforced schema, or JSON mode
  with the schema in the prompt). That lets the same code run on local Ollama
  models for development and on Bedrock for production.
- An over-long summary is trimmed or blanked and flagged for the reviewer; the
  rest of the case survives.
- Development runs on **small models that fit entirely in GPU memory**
  (about 12 GB or less, with their context), so the workstation stays usable.
  Extraction must work with them, for example by asking for a simpler format
  that our code assembles into the strict record.
- **A data policy decides which model may see which document.** It is
  enforced in code before every AI call, and fails closed:
  - Each source has a data class: `confidential` (the default), `sanitised`
    or `public`.
  - Each model alias declares where data goes: `local` (Ollama on this
    machine), `our-cloud` (Bedrock in our AWS account) or `third-party`
    (Ollama Cloud, Anthropic API, others).
  - Confidential documents go to local or our-cloud models only.
    Third-party models get sanitised and public documents.
  - External models stay possible for confidential data in future scenarios,
    but only when an admin explicitly approves that model for confidential
    data. The approval is recorded in the audit trail, where a data-policy
    sign-off plugs in.
  - Today's decision (2026-10-07): Ollama Cloud for sanitised or synthetic
    documents only. The Ollama API key comes from the existing agenix
    secret.
- A repeatable development evaluation, run on a local model, reports per
  document: fields filled, items sourced, time.

## Affected users and systems

- Engineers running the pipeline locally; reviewers (fewer lost cases).
- `app/llm.py`, `app/schema.py`, `app/extract.py`, configuration.

## Constraints

- All existing guarantees stay:
  - every field is checked against the document text (`sourced()`)
  - the AI can never mark a field sourced
  - validation errors never carry document text
  - no logging of document text
- JSON mode is weaker than an enforced schema, so the reply must still be fully
  validated against `ReferenceCase`. Invalid output fails the job; it is never
  partially trusted.
- Real client documents only go to local or our-cloud models unless a
  recorded approval says otherwise. An unmarked source counts as
  confidential.
- Production on Bedrock is unchanged unless #26 shows that Claude
  under-extracts with enforced schemas too.

## Open questions

- Who may approve a third-party model for confidential data (role or person),
  and must the approval name a specific model and expire?

- Should the over-long summary be trimmed to 80 words, or blanked and
  flagged? Trimming keeps something usable but may cut mid-sentence.
- Should the development evaluation live in the repo (`scripts/`) as a tool,
  with its document folder passed in at run time and never committed? Or stay
  outside the repo?
