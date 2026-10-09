---
status: draft
issue: 98
intent: intent/2026-10-09-98-ci-actions-node24.md
---

# Spec: CI on Node 24 actions and a pinned runner image

## Design

Two edits to `.github/workflows/ci.yml`, the `test` job, nothing else:

```diff
 jobs:
   test:
-    runs-on: ubuntu-latest
+    runs-on: ubuntu-24.04
     env:
       # fixes the compose image name for `app` to refs-ci-app (compose names it <project>-<service>)
       COMPOSE_PROJECT_NAME: refs-ci
     steps:
-      - uses: actions/checkout@v4
+      - uses: actions/checkout@v7
```

Decisions taken from the approved intent:

- **Runner:** `ubuntu-24.04`, the image `ubuntu-latest` resolves to today, so
  the host does not change. Moving to 26.04 is a later one-line change.
- **Pinning:** major tags, as the file does now. No SHAs.
- **Scope:** only checkout moves. The docker actions (`setup-docker-action@v5`,
  `setup-buildx-action@v4`, `build-push-action@v7`) are already current and on
  Node 24. Dependabot is a separate issue.

### Checkout v4 → v7: breaking changes and whether they apply

Read from the release notes and the v7.0.1 README.

| Version | Change | Applies here? |
| ------- | ------ | ------------- |
| v5.0.0 | Runs on `node24`; needs Actions Runner ≥ v2.327.1. | Yes, it is the point of the change. GitHub-hosted runners are past that version. |
| v6.0.0 | `persist-credentials` writes the token to a separate file under `$RUNNER_TEMP` (wired in with `includeIf`), not `.git/config`. Authenticated git inside a Docker container action needs runner ≥ v2.329.0. | No. The job runs no git command after checkout and uses no container action. The build context excludes `.git` (`.dockerignore`), so the token never entered the image under v4 either. Default `persist-credentials` stays. |
| v6.0.2 | Tag fetch keeps annotations; explicit `fetch-tags` respected. | No. Default `fetch-depth: 1`, no tags used. |
| v7.0.0 (also back-ported as v4.4.0 / v5.1.0 / v6.1.0) | Refuses to check out fork PR code under `pull_request_target` or `workflow_run` unless `allow-unsafe-pr-checkout: true`. | No. Triggers are `push` and `pull_request` only. Fork PRs on `pull_request` check out as before. |
| v7.0.0 | Action moved to ESM, dependency updates. | No. Internal. |

No inputs are added: `fetch-depth`, `persist-credentials`, `fetch-tags` and
`allow-unsafe-pr-checkout` all stay at their defaults.

## Alternatives rejected

- **`ubuntu-26.04` now.** Two variables in one change that cannot be tested
  until billing works. Rejected in the intent.
- **Keep `ubuntu-latest`.** The image would change on GitHub's date
  (19 Oct – 19 Nov 2026), not ours.
- **`actions/checkout@v5` or `@v6`.** Both run on Node 24, but v7 is the
  current major; stopping short means a second bump later for no gain.
- **Pin by commit SHA.** Departs from the file's convention and needs a bot to
  stay current. Separate issue with Dependabot if wanted.
- **`persist-credentials: false`.** Good hygiene, but outside this issue's
  scope and nothing here needs it changed.

## Risks

- **`ubuntu-24.04` retires later.** GitHub will deprecate it in time; the pin
  then fails loudly with a deprecation notice, not silently. Mitigation: the
  26.04 move is its own one-line change.
- **gha build cache.** Buildx cache keys come from layer content, not the host
  image or checkout version, and the host image is unchanged. The first run
  after merge confirms cache hits.
- **No CI signal until billing is fixed.** The change can merge on review and
  actionlint; the issue stays open until one green run.

## Verification

1. `actionlint` on the changed `ci.yml` reports nothing (it passes on the
   current file).
2. After Actions billing is fixed, one green `ci` run on the change (PR or
   `main`). Post on #98: run URL, runner image and version from "Set up job",
   checkout version resolved, no Node 20 deprecation annotation, job
   duration, cache hit or miss on the build step, and pytest's
   passed/failed counts.
3. #98 closes after that run, not at merge.
