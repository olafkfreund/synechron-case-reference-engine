---
status: approved
issue: 98
spec: spec/2026-10-09-98-ci-actions-node24.md
---

# Plan: CI on Node 24 actions and a pinned runner image

Approved decisions, copied from the spec:

- Edit only the `test` job in `.github/workflows/ci.yml`. No other files.
- Runner: `ubuntu-latest` → `ubuntu-24.04`. That's the image `ubuntu-latest`
  points to today, so the host stays the same. Moving to 26.04 is a later
  change of its own.
- `actions/checkout@v4` → `@v7`, so checkout runs on Node 24. Pin by major
  tag, as the file does now. No commit SHAs.
- Checkout gets no new inputs. `fetch-depth`, `persist-credentials`,
  `fetch-tags` and `allow-unsafe-pr-checkout` keep their defaults. The
  breaking changes in v5–v7 don't affect this job:
  - the triggers are only `push` and `pull_request`;
  - no git command runs after checkout;
  - no container action is used;
  - `.dockerignore` keeps `.git` out of the build context.
- The docker actions are already on Node 24 and stay as they are:
  `setup-docker-action@v5`, `setup-buildx-action@v4` and `build-push-action@v7`.
- No Dependabot or `persist-credentials: false` here. Those are separate issues.

## Steps

1. `.github/workflows/ci.yml:8`: change `runs-on: ubuntu-latest` to
   `runs-on: ubuntu-24.04`.
   `.github/workflows/ci.yml:13`: change `- uses: actions/checkout@v4` to
   `- uses: actions/checkout@v7`.
   Verify: `actionlint .github/workflows/ci.yml` prints nothing and exits 0.
   `git diff --stat` shows one file with 2 insertions and 2 deletions.
   Traps:
   - Don't touch the cache-to expression on line 28 or the
     `COMPOSE_PROJECT_NAME` comment.
   - Keep the two-space YAML indent.
   - Don't add `with:` to checkout.
   - Run nothing on GitHub: Actions is blocked by billing.

## Tests

- `actionlint .github/workflows/ci.yml`: no output, exit 0. It passes on the
  current file.
- No app code changes, so the local pytest suite isn't needed. Run
  `docker compose build app && docker compose run --rm app timeout 900 pytest`
  only if the merge gate asks for it. The result should be the same count as
  main.
- After merge, once Actions billing is fixed, one green `ci` run. Post these
  on #98:
  - the run URL;
  - the runner image and version, from "Set up job";
  - the checkout version it resolved to;
  - that there's no Node 20 deprecation annotation;
  - the job duration;
  - whether the build step hit the cache;
  - pytest's passed and failed counts.
- #98 closes after that run, not when the PR merges. Keep `Closes #98` out of
  the PR body and write `Refs #98` instead.

## Rollback

Revert the commit. Or set line 8 back to `ubuntu-latest` and line 13 back to
`actions/checkout@v4`. Neither needs any state cleanup.
