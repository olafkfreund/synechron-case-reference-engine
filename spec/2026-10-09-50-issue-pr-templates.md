---
status: draft
issue: 50
intent: intent/2026-10-09-50-issue-pr-templates.md
---

# Spec: Issue and PR templates

## Design

Five new files under `.github/`. No existing file changes.

The approved decisions:
1. Three issue forms (bug, feature, verification), each setting its
   existing `type:` label.
2. Blank issues kept.
3. No priority or area fields; those are left to triage.
4. A PR template with full `blob/<branch>/` URLs.
5. No CI check on the PR body.

The labels exist (`gh label list --limit 100`, 2026-10-09; the default limit of 30 cuts off `type:bug` and `type:feature`, which are 43rd and 44th; they are in use on #53 and #52): `type:bug` ("Something built
that does not work as intended"), `type:feature` ("New or changed product
behaviour") and `type:verification` ("Run something already built against
real services"). The sections copy what issues already use. Bug and
feature issues have Context, Done when and Plan reference (#53, #64).
Verification issues add Depends on (#35). Bugs also carry the evidence:
what was run, where and when (#53).

`gh issue create --body` and `gh pr create --body` skip templates, so agents
are unaffected. `gh pr create` without `--body` opens the PR template in the
editor.

### `.github/ISSUE_TEMPLATE/config.yml`

```yaml
blank_issues_enabled: true
```

### `.github/ISSUE_TEMPLATE/bug.yml`

```yaml
name: Bug
description: Something built that does not work as intended
labels: ["type:bug"]
body:
  - type: textarea
    id: context
    attributes:
      label: Context
      description: What was run, where (local, staging, AWS), when, and with which model or data. What happened, and what should have happened.
    validations:
      required: true
  - type: textarea
    id: evidence
    attributes:
      label: Evidence
      description: Commands, log lines, error text. No secrets or customer documents.
      render: text
  - type: textarea
    id: done
    attributes:
      label: Done when
      value: |
        - [ ] 
        - [ ] A test that fails before the fix
    validations:
      required: true
  - type: input
    id: plan
    attributes:
      label: Plan reference
      description: Link to the plan step this came from, or "New task".
      placeholder: https://github.com/olafkfreund/synechron-case-reference-engine/blob/<branch>/plan/<slug>.md
```

### `.github/ISSUE_TEMPLATE/feature.yml`

```yaml
name: Feature
description: New or changed product behaviour
labels: ["type:feature"]
body:
  - type: markdown
    attributes:
      value: A feature goes through intent → spec → plan before any code. Describe the need here, not the solution.
  - type: textarea
    id: context
    attributes:
      label: Context
      description: Who needs this and why. What happens today.
    validations:
      required: true
  - type: textarea
    id: done
    attributes:
      label: Done when
      value: |
        - [ ] 
    validations:
      required: true
  - type: input
    id: plan
    attributes:
      label: Plan reference
      description: Link to the plan step this came from, or "New task".
      placeholder: https://github.com/olafkfreund/synechron-case-reference-engine/blob/<branch>/plan/<slug>.md
```

### `.github/ISSUE_TEMPLATE/verification.yml`

```yaml
name: Verification
description: Run something already built against real services
labels: ["type:verification"]
body:
  - type: textarea
    id: context
    attributes:
      label: Context
      description: What to run, the exact command, and against which environment.
    validations:
      required: true
  - type: textarea
    id: done
    attributes:
      label: Done when
      description: The pass condition, and where the result is recorded (usually `plan/`).
      value: |
        - [ ] 
    validations:
      required: true
  - type: textarea
    id: depends
    attributes:
      label: Depends on
      description: Issues that must be closed first, one per line.
      placeholder: "- #20"
  - type: input
    id: plan
    attributes:
      label: Plan reference
      placeholder: https://github.com/olafkfreund/synechron-case-reference-engine/blob/<branch>/plan/<slug>.md
```

### `.github/pull_request_template.md`

The shape of PRs #73 and #74. HTML comments don't show in the rendered body.

```markdown
Closes #<issue>.

<!-- Full blob URLs: relative links in a PR body don't resolve to the branch.
     Exempt work (typo, lock bump, one-line config): delete the three links and say "Exempt: <reason>". -->
- Intent: [intent/<slug>.md](https://github.com/olafkfreund/synechron-case-reference-engine/blob/<branch>/intent/<slug>.md)
- Spec: [spec/<slug>.md](https://github.com/olafkfreund/synechron-case-reference-engine/blob/<branch>/spec/<slug>.md)
- Plan: [plan/<slug>.md](https://github.com/olafkfreund/synechron-case-reference-engine/blob/<branch>/plan/<slug>.md)

## Changes
<!-- One bullet per change, ending with the plan step it implements: (Step N).
     Anything beyond the plan says so and who approved it. -->
- 

<!-- Which steps the coder agent did, and what the session model did (commits, runtime checks, review fixes). Or "No coder handoff". -->
The `coder` agent did steps .

## Verification
<!-- Commands run and their results, the manual check, and the review outcome. -->
- `docker compose build app && docker compose run --rm app pytest`: 
- Review by a fresh Opus agent: 
```

## Alternatives rejected

- **Markdown issue templates** (`.md` in `ISSUE_TEMPLATE/`). They can set
  labels, but they can't mark a field required, so "Done when" can be
  deleted. Forms can require it.
- **A follow-up form.** Not asked for in #50. Follow-ups are opened from
  the CLI with `--body`, and the blank issue stays for them.
- **Priority and area dropdowns.** A form dropdown fills only the body,
  not labels. Labels are left to triage.
- **Repo-relative links in the PR template.** In a PR body they resolve
  against the default branch, so the files 404 until the merge.
- **A CI check that the PR body links three files.** Review checks this
  already, and exempt PRs would fail it.

## Risks

- **Forms on a private repo.** The repo is private. Issue forms work on
  private repos, but if the form picker doesn't appear, the fallback is
  the same content as Markdown templates.
- **Label application.** A form's `labels` are applied only if the label
  exists. All three exist; renaming one silently drops it from new issues.
- **Template drift.** If the workflow in the managed instructions changes,
  these files go stale. They are plain files and easy to edit.
- **Hosts:** GitHub only. No app, image, CI or AWS change.

## Verification

- **YAML parses:**
  `nix-shell -p python3Packages.pyyaml --run "python3 -c \"import yaml,glob;[yaml.safe_load(open(f)) for f in glob.glob('.github/ISSUE_TEMPLATE/*.yml')]\""`
  exits 0.
- **Before merge:** GitHub reads issue forms and the PR template only from
  the default branch, so the branch cannot show them. Review reads the
  files in the diff against this spec.
- **After merge, the forms appear:**
  `https://github.com/olafkfreund/synechron-case-reference-engine/issues/new/choose`
  lists Bug, Feature, Verification and "Blank issue". Open each form
  without submitting it. It shows its sections in order, with "Done when"
  marked required. The `type:` label each sets is checked in the YAML
  under review. Creating test issues needs the user's go-ahead, so this
  step doesn't create any.
- **After merge, the PR template:** the next PR opened in the web UI, or
  with `gh pr create` without `--body`, starts from the template. This
  task's own PR is written by hand in the same shape.
