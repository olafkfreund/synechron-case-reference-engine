---
status: approved
issue: 50
spec: spec/2026-10-09-50-issue-pr-templates.md
---

# Plan: Issue and PR templates

## Approved decisions (self-contained)

- **Five new files under `.github/`. No existing file changes.** No app,
  Dockerfile, compose or CI change.
- **Three issue forms:** `bug.yml`, `feature.yml` and `verification.yml`.
  Each sets the existing label `type:bug`, `type:feature` or
  `type:verification`.
  - The labels exist; check with `gh label list --limit 100`. The default
    limit of 30 hides `type:bug` and `type:feature`, which are in use on
    #53 and #52.
  - No labels are created.
- **Sections copy what issues already use:**
  - Context, Done when, Plan reference (#53, #64);
  - Evidence for bugs (#53);
  - Depends on for verification issues (#35).
  "Done when" is required in every form.
- **Blank issues kept:** `config.yml` with `blank_issues_enabled: true`.
  Follow-ups and epics are opened that way or from the CLI.
- **Not included:**
  - priority or area fields (left to triage);
  - a follow-up form;
  - a check on the PR body.
- **PR template:**
  - It follows the shape of PRs #73 and #74: `Closes #`, intent, spec and
    plan as full `blob/<branch>/` URLs, Changes by plan step, which steps
    the `coder` did, and Verification.
  - Exempt work replaces the three links with "Exempt: <reason>".
  - Guidance goes in HTML comments, which don't render.
- **`gh issue create --body` and `gh pr create --body` skip templates.**
  Agents are unaffected.
- **GitHub reads forms and the PR template only from the default branch.**
  The branch can't show them, so review reads the files against this plan,
  and the live check comes after merge.
- **The post-merge check opens each form without submitting it.**
  Creating test issues needs the user's go-ahead.
- **Coder handoff:** 2 editing steps, 5 files touched, which meets the
  threshold (3 files). Steps 1 and 2 go to the `coder` agent, started with
  this plan's path and step 1, with step 2 sent by `SendMessage`. The
  session model commits, runs step 3, and has a fresh Opus agent review
  the diff against this plan.

## Steps

1. **Issue forms: four new files in `.github/ISSUE_TEMPLATE/`**, with
   exactly this content.

   `.github/ISSUE_TEMPLATE/config.yml`:

   ```yaml
   blank_issues_enabled: true
   ```

   `.github/ISSUE_TEMPLATE/bug.yml`:

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

   `.github/ISSUE_TEMPLATE/feature.yml`:

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

   `.github/ISSUE_TEMPLATE/verification.yml`:

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

   → verify by
   `nix-shell -p python3Packages.pyyaml --run "python3 -c \"import yaml,glob;[print(f, yaml.safe_load(open(f)).get('labels')) for f in sorted(glob.glob('.github/ISSUE_TEMPLATE/*.yml'))]\""`.
   It prints four files, with labels `['type:bug']`, `['type:feature']`
   and `['type:verification']`, and `None` for `config.yml`.

   Traps:
   - **Trailing spaces.** `- [ ] ` in the `value:` blocks keeps its space
     so the cursor lands after the box. An editor that strips trailing
     whitespace must not change it.
   - **Label names exactly as listed**, with no new labels.
   - **The `id`s must be unique** within each form, or GitHub rejects it.
   - **Commit** as `chore(github): issue forms for bug, feature and
     verification (plan #50 step 1) (#50)`.

2. **PR template: `.github/pull_request_template.md` (new)**, with exactly
   this content:

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

   → verify by `grep -c "blob/<branch>/" .github/pull_request_template.md`
   (3) and `grep -n "coder" .github/pull_request_template.md` (it matches).

   Traps:
   - **The file name is lowercase** `pull_request_template.md`, directly
     in `.github/`. Not in a `PULL_REQUEST_TEMPLATE/` folder, which only
     works with a query parameter.
   - **Commit** as `chore(github): PR template linking intent, spec and plan
     (plan #50 step 2) (#50)`.

3. **PR and post-merge check (session model).** Open the PR with a
   hand-written body in the template's shape, because the template isn't
   live until merge. It links this intent, spec and plan and says the
   `coder` did steps 1–2. After merge:
   - open
     `https://github.com/olafkfreund/synechron-case-reference-engine/issues/new/choose`.
     It lists Bug, Feature, Verification and "Blank issue".
   - open each form without submitting it. The sections show in order,
     and "Done when" is marked required.
   - open the compare view for any branch,
     `https://github.com/olafkfreund/synechron-case-reference-engine/compare/main...<branch>?expand=1`,
     without creating a PR. The body is pre-filled with the template.

   → verify by those page checks. Record the result in this plan (a
   follow-up commit on `main`) or as a comment on #50.

   Traps: don't submit any form and don't create a PR or issue for the
   check without the user's go-ahead.

## Tests

- The YAML parse command in step 1 prints the four files with the
  expected labels.
- `git diff --stat origin/main` shows exactly five added files under
  `.github/`, plus this plan.
- After merge: the chooser lists the three forms plus a blank issue, each
  form renders with "Done when" required, and the compare view pre-fills
  the PR body.

## Rollback

Revert the step 1 and step 2 commits, or delete `.github/ISSUE_TEMPLATE/`
and `.github/pull_request_template.md`. Nothing else references them.
