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
docker compose up web worker    # starts db, idp and s3 too
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

## Local demo

A demo with made-up data (fictional companies, invented numbers) that survives restarts:

```sh
scripts/demo.sh up       # build, start, seed, print the URL and users
scripts/demo.sh down     # stop, keep the data
scripts/demo.sh reset    # wipe the demo database and start again
```

It uses its own compose project, `refsdemo`, on ports 8000 and 8080, so stop any other stack on those
ports first. Log in as in "Local login" above, but with the demo's document groups in the claims, so each user
sees the demo sources:

| Username | Claims |
| --- | --- |
| `admin` | `{"name": "Test Admin", "groups": ["refs-admins", "sales", "delivery"]}` |
| `reviewer` | `{"groups": ["refs-reviewers", "sales", "delivery"]}` |
| `sales` | `{"groups": ["refs-users", "sales"]}` (no `delivery`: the Delivery archive stays hidden) |
| `nobody` | `{}` |

The seed adds two
sources ("Demo: Bid library" for `sales`, "Demo: Delivery archive" for `delivery`), ten clients and 14 cases
(9 approved, 5 waiting for review, including a Fabrikam statement of work and change order to merge).
Running `up` again leaves existing rows alone. The S3 originals are in memory and are put again each time.

**Needs a model:** live upload extraction, drafting and online research. Without Ollama on
`localhost:11434` (`qwen3:14b`, see below), `up` says so and starts without a model; the seeded cases, search
and review still work. Rows are made with no LLM call.

Walkthrough:

1. **Sales:** log in as `sales`, search for a bid text (try "claims" or "payments"), open a case, pick cases and draft an output.
2. **Reviewer:** as `reviewer`, open the review list. Fix the case with an outcome named "metric", look at the unsourced field, and approve one.
3. **Admin:** as `admin`, look at sources and clients, and merge the two Fabrikam cases.
4. **Live upload:** as `admin`, upload a document to "Demo: Bid library" and watch it extract (needs a model).
5. **Nobody:** log in as `nobody` and see that there is no access.

## Revoke a user's sessions

A session cookie lasts 8 hours and is signed, so ending it needs a server-side cutoff (#47). Logging out
sets that cutoff, so it ends **that user's sessions on every device**. To end someone's sessions without
their help (a leaver, a stolen laptop), run the revoke command with their `sub`.

- Find the `sub`: search the web logs for `login sub=... name=<name>`, or look at `generations.user_id`,
  `research.created_by` and the `changed_by` columns.
- Local: `docker compose run --rm app python -m app.revoke_sessions <sub>`
- AWS: `aws ecs run-task` on the `crawl` task definition, with the network configuration from the
  `migrate_task` output (`infra/outputs.tf`) and the command overridden:

  ```sh
  aws ecs run-task --cluster <cluster> --task-definition <crawl family> --launch-type FARGATE \
    --network-configuration 'awsvpcConfiguration={subnets=[<subnets>],securityGroups=[<security_group>]}' \
    --overrides '{"containerOverrides":[{"name":"crawl","command":["python","-m","app.revoke_sessions","00000000-aaaa-bbbb-cccc-000000000000"]}]}'
  ```

  It prints `revoked sessions of <sub>` per user. The user's next request goes to `/login`.
- **Deploy order:** one `terraform apply` rolls out web and the migrate task definition together, and
  `/healthz` doesn't touch the database, so the rollout completes before the table exists. Every logged-in page
  then fails until migrate runs. So, for this release:
  1. `terraform apply -target='aws_ecs_task_definition.app["migrate"]'` with the new `image_tag`;
  2. run the `migrate` task (`migrate_task` output) and wait for it to finish;
  3. the full `terraform apply`.

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
