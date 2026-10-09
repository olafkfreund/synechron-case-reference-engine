---
status: approved
issue: 98
author: olafkfreund
---

# Intent: CI on Node 24 actions and a pinned runner image

## Problem

`.github/workflows/ci.yml` has two things that will change under us:

- `actions/checkout@v4` runs on Node 20. Node 20 is deprecated on GitHub
  runners, which force it onto Node 24 and annotate every run. The current
  major is `v7` (v7.0.1, `runs.using: node24`).
- `runs-on: ubuntu-latest` moves to Ubuntu 26.04 in a rollout starting
  19 October 2026 and finishing by 19 November 2026
  (actions/runner-images#14748). The job image would change on some date in
  that window that we do not choose.

The three docker actions are already on their latest majors and run on
Node 24: `docker/setup-docker-action@v5` (v5.5.0),
`docker/setup-buildx-action@v4` (v4.4.1), `docker/build-push-action@v7`
(v7.4.0). Only checkout is behind.

Raised in the #43 review.

## Proposed outcome

- Every action in `ci.yml` is on its current major and runs on Node 24, so
  runs show no Node 20 deprecation annotation.
- The runner image is a choice written in `ci.yml`, not a label that changes
  on GitHub's schedule.
- CI is green on the change, once Actions billing is fixed.

## Affected users and systems

- `.github/workflows/ci.yml`, the `test` job only (the only workflow).
- Everyone opening a PR or pushing to `main`: the job is the PR check.
- The `type=gha` build cache: a different image or checkout version should
  not invalidate it, but the first run after the change is the check.

## Constraints

- Must keep the #43 build setup as is: containerd image store, buildx
  `docker` driver, `refs-ci-app:latest` tag, main-only cache writes,
  `COMPOSE_PROJECT_NAME: refs-ci`.
- Must not change what the job tests (`docker compose run --rm app pytest`).
- CI cannot verify anything while Actions are blocked by account billing.
  The change can merge on review, but "done" needs one green run after
  billing is fixed.
- Checkout v4 → v7 skips majors; their breaking changes must be read in the
  spec, not assumed.

## Open questions

1. **Runner image: pin `ubuntu-24.04`, or pin `ubuntu-26.04` now?**
   - Pin `ubuntu-24.04`: no change to the image, nothing to test beyond
     checkout; we move later on purpose.
   - Pin `ubuntu-26.04`: we take the move now, at a time we choose, and stop
     tracking it. It cannot be tested until billing works.
   - Keep `ubuntu-latest`: least edit, but the date is GitHub's.
   - Lean: **pin `ubuntu-24.04`**. The job does all its work inside Docker,
     so the host OS barely matters, and one variable at a time is easier to
     check once CI runs again. Moving to 26.04 is a later one-line change.
2. **Actions: pin by commit SHA, or by major tag?**
   - SHA: immune to a moved or hijacked tag; needs a bot (Dependabot) or
     manual bumps to stay current.
   - Major tag: matches the file today, gets fixes automatically.
   - Lean: **major tag**, the repo's current convention. SHA pinning plus
     Dependabot is its own issue if wanted.
3. **Add Dependabot for `github-actions`** so this does not recur?
   Lean: not in this issue; raise separately if wanted.
