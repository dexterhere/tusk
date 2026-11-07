# Fix brief: $cid $title

Goal: make the acceptance command exit 0 without breaking anything else.
Non-goals: refactors, unrelated cleanups, new dependencies.
Work in: `$worktree` (branch `$branch`). Everything outside it is off limits. No push.

## The bug
- Severity / area: $severity / $area  (rule: $rule)
- Expected: $expected
- Actual: $actual
- Evidence: $evidence
- Suspect location: $suspect

## Acceptance
- `$acceptance`
- Then the project's own test command must still pass: `$test_cmd`
- Add a regression test in the project's own test suite if one exists for this area; the UAT repro stays in `$regression_dir`.

## Rules
Follow the repo's AGENTS.md/CLAUDE.md. No secrets in files or logs. Small diff, root cause not symptom: check every caller of the code you change.
Output: branch name, files changed, acceptance and test output.
