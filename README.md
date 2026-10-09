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

## Limits

- Crawled files larger than `S3_MAX_BYTES`, `SHAREPOINT_MAX_BYTES` or `CONFLUENCE_MAX_BYTES` (50 MB by default)
  are skipped before download and counted as `skipped_too_large` on the sources page.
- Only `docx`, `pptx` and `pdf` are read; other files are counted as `skipped_type`. For S3 and SharePoint
  sources, `include_ext` in the source config widens the list. Legacy `.doc` is not supported.
- Uploads are capped by `UPLOAD_MAX_BYTES` (50 MB by default) and go through the S3 crawler, so keep it at or
  below `S3_MAX_BYTES`.

## Dependency lock

Every image installs `requirements.lock` with hashes. After editing `pyproject.toml`, re-lock:

```sh
docker run --rm --user "$(id -u):$(id -g)" -e HOME=/tmp -e UV_CACHE_DIR=/tmp/uv -v "$PWD:/w" -w /w ghcr.io/astral-sh/uv:0.12.23-python3.12-trixie-slim \
  uv pip compile pyproject.toml --extra dev --torch-backend cpu --python-version 3.12 \
  --python-platform x86_64-manylinux_2_28 --generate-hashes \
  --custom-compile-command "see README: Dependency lock" \
  -o requirements.lock
```

- Add `--upgrade-package X` or `--upgrade` to move versions.
- A dependency that isn't in the lock fails the build at `pip check`.
- The lock is x86_64 only: for ARM64, re-lock with `--python-platform aarch64-manylinux_2_28`.

## Local login (test users)

```sh
docker compose up web worker    # starts db and idp too
```

Open http://localhost:8000. Login goes to a mock provider at `idp.localhost:8080`: type a username and
paste the claims JSON from the table.

| Username | Claims | Gets |
| --- | --- | --- |
| `admin` | `{"name": "Test Admin", "groups": ["refs-admins"]}` | admin, reviewer, user |
| `reviewer` | `{"groups": ["refs-reviewers"]}` | reviewer, user |
| `sales` | `{"groups": ["refs-users", "sales"]}` | user |
| `nobody` | `{}` | no role |

- `sales` sees a source only if it has the access group `sales`: as `admin`, give a source that group first.
- If `idp.localhost` doesn't resolve in your browser, add `127.0.0.1 idp.localhost` to `/etc/hosts`.
- Port 8080 appears in three places: the `idp` port, its `SERVER_PORT`, and `OIDC_METADATA_URL` in
  `docker-compose.yml`. Change all three together.
- Check all four logins with `scripts/check_local_login.sh` (`BASE` overrides `http://localhost:8000`).
- Uploads go to an in-memory S3 (`s3`), lost when it restarts; `docker compose up -d s3-init` recreates the bucket.
  As admin, add an upload source with config `{"bucket": "refs-local"}` first.
- **Local only:** this provider logs anyone in as anything. Never expose it.

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
Compose passes `EXTRACT_MODEL`, `EXTRACT_MODEL_OPTIONS`, `DRAFT_MODEL`, `DRAFT_MODEL_OPTIONS` and `BRAVE_API_KEY` from your shell to `web` and `worker`.

Do not use the 27B or 26B models: they spill from the GPU to the CPU and freeze the workstation, and Gemma 4
runs mostly on the CPU on this AMD card.

**Data policy.** Every source has a data class: `confidential` (the default), `sanitised` or `public`.
Confidential documents go only to `local` (Ollama on loopback) or `our-cloud` (Bedrock) models; anything
else, and any model with "cloud" in its name, is `third-party`. An explicit `destination` covers the Docker
case above, but `local` is refused for a "cloud" model or `ollama.com`. An admin can approve one exact model id
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
