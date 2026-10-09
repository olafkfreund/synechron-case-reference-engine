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
