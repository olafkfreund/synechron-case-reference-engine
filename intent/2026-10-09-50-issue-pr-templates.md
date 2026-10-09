---
status: approved
issue: 50
author: olafkfreund
---

# Intent: Issue and PR templates

## Problem

The repo has no issue or PR templates: `.github/` holds only
`workflows/ci.yml`; there is no `.github/ISSUE_TEMPLATE/` and no
`pull_request_template.md`.

New work follows intent → spec → plan, but nothing in GitHub says so. The
shape of issues and PRs exists only by habit:

- Issues use `## Context`, `## Done when` (a checklist) and
  `## Plan reference` (#43, #50, #64), plus `## Depends on` for
  verification issues (#35). Bugs add the evidence: what was run, on which
  model and date, what went wrong (#53).
- PRs start with `Closes #N`, link the intent, spec and plan files on the
  branch, list changes with the plan step each came from, say which steps
  the `coder` agent did, and give the verification (PRs #73 and #74).
- The managed instructions require that the PR links all three files and
  says which steps the coder did, and that review checks the diff against
  `plan/`.

Anyone who opens an issue or PR by hand, or an agent without that habit,
gets a blank box. Labels are also set by hand: the repo has `type:bug`,
`type:feature`, `type:verification`, `type:follow-up`, `priority:p1..p3`,
`area:*` and `owner:*`.

## Proposed outcome

- "New issue" offers three forms: bug, feature and verification, each
  with the sections those issues already use and its `type:` label set.
- A new PR opens pre-filled with: the closed issue; intent, spec and plan
  links; changes by plan step; which steps the `coder` agent did; and
  verification.
- A PR for exempt work (typo, lock bump, one-line config) can say so
  instead of linking three files.
- `gh issue create` and `gh pr create` (used by the agents) still work
  with `--body`, as they do today.

## Affected users and systems

- New files under `.github/` only. No app code, Dockerfile, compose or CI
  change.
- Everyone opening issues or PRs on GitHub, and the Claude Code session and
  `coder` agent that write PR descriptions.

## Constraints

- Must match the workflow in the managed instructions and the
  `artifact-workflow` skill (file names `intent|spec|plan/YYYY-MM-DD-<issue>-<slug>.md`);
  must not invent a second process.
- Use labels that already exist; don't create new ones in this task.
- Must not block blank issues for follow-ups opened from the CLI (most
  issues here are `type:follow-up` created that way).
- No secrets or internal URLs beyond the repo's own.

## Open questions

1. **Blank issues.** Keep "Open a blank issue" (`blank_issues_enabled:
   true` in `config.yml`) or force a form? Lean: keep it; follow-ups and
   epics have no form.
2. **A fourth form for follow-ups.** Issue #50 asks for three; follow-up
   is the most common type here. Lean: no, stay with the three asked for;
   add it later if the blank issue proves too loose.
3. **Priority and area in the form.** A dropdown that only fills the body
   (forms can't set labels from a dropdown), or leave labels to triage?
   Lean: leave to triage; forms set only the `type:` label.
4. **PR template links.** Full `blob/<branch>/` URLs as in #73/#74, or
   repo-relative paths? Lean: the URL form with placeholders, since
   relative links in a PR body don't resolve to the branch.
5. **Enforcement.** Add a CI check that the PR body links three files?
   Lean: no; review already checks it, and it would fail exempt PRs.
