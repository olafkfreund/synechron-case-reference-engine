# Customer Reference Engine

Internal AWS portal that crawls our case documents (SharePoint, Confluence, file
shares) and turns them into approved, branded customer references (Word,
PowerPoint, PDF, text) for bids, with cited online research for industry
context.

## Links

- [Kickoff brief](https://claude.ai/code/artifact/c1d1bd28-762e-4d95-8070-501bef7e656d): meeting brief for engineering and sales
- [Portal mockup](https://claude.ai/artifact/51aJ7CQx77Wynaukd9N2Fj): clickable mockup with illustrative sample data

Both are private until shared from their Share menu.

## Design artifacts

- [Intent](intent/2026-10-06-1-reference-engine.md): why (approved)
- [Spec](spec/2026-10-06-1-reference-engine.md): what (approved)
- [Plan](plan/2026-10-06-1-reference-engine.md): how (approved)

## Local development with Ollama

Extraction works with a local model, so real documents never leave the workstation.

```sh
ollama pull qwen3:14b          # 13 GB, fits a 20 GB GPU
export EXTRACT_MODEL=ollama_chat/qwen3:14b
export EXTRACT_MODEL_OPTIONS='{"think": false, "num_ctx": 24576, "repeat_penalty": 1.05}'
```

Inside Docker the app reaches the host's Ollama through `host.docker.internal`, which is not loopback, so
the destination must be stated or it is inferred `third-party` and refused:
`{"think": false, "num_ctx": 24576, "repeat_penalty": 1.05, "api_base": "http://host.docker.internal:11434", "destination": "local"}`.
`DRAFT_MODEL_OPTIONS` works the same way. Bedrock needs no options.

Do not use the 27B or 26B models: they spill from the GPU to the CPU and freeze the workstation, and Gemma 4
runs mostly on the CPU on this AMD card.

**Data policy.** Every source has a data class: `confidential` (the default), `sanitised` or `public`.
Confidential documents go only to `local` (Ollama on loopback) or `our-cloud` (Bedrock) models; anything
else, and any model with "cloud" in its name, is `third-party`. An admin can approve one exact model id
for confidential data, for at most 12 months, at `/admin/models`; approvals and source class changes are
audited at `/admin/audit`.

**Ollama Cloud** is for sanitised or public sources only. Export the key from agenix as `OLLAMA_CLOUD_KEY`
and set `"api_base": "https://ollama.com"` in the options. Do not use `OLLAMA_API_KEY`: LiteLLM sends it
to every Ollama host.

**Compare models** on a folder of real documents, kept outside this repository (the script refuses a path
inside it). It prints metrics only, never document text. It refuses a `destination` in the options, so in
Docker run it with `--network host`:

```sh
python scripts/eval_extraction.py --docs ~/presale --models ollama_chat/qwen3:14b [--data-class confidential]
```
