---
name: uat-fix
description: Stage 5 of UAT Swarm. Turns the UAT report into fixes using the repo's agent-orchestration rules: one worktree per bug cluster, a different agent reviews, every fix is proven by its pinned repro plus the full regression run. Never pushes without approval.
disable-model-invocation: true
---

# /uat-fix: fix, prove, regress

`S=${UAT_HOME:-$HOME/Work/uat-swarm}`. Requires phase `reported` and `.uat/report/findings.json`. Follow `agent-orchestration` (routing, author never reviews own change, security-sensitive work stays with Claude) and the repo's `AGENTS.md`/`CLAUDE.md` (git flow, test command) first. Caveman chat; commits and PR text normal prose.

## 1. Select and plan
AskUserQuestion (one): which findings: all / blocker+major / pick ids. Group by `suspect.file` and `area` so one fix covers one root cause; check every caller of the code to change (root cause, not symptom). Show the fix plan (cluster -> owner -> branch) and get a yes.

Routing (from `agent-orchestration`): auth, crypto, input/SQL handling, permissions, secrets, migrations -> Claude implements, Codex reviews. Bounded, clear-spec fixes -> Codex implements (`codex:rescue`), Claude reviews. Test-only or mechanical changes -> OpenCode (`opencode-bridge:rescue`). Delegate fails twice -> Claude takes it. Load `ponytail:ponytail` before writing code; `frontend-design` for UI fixes.

## 2. Per cluster (up to `max_parallel` clusters at once, one git worktree each)
1. `git worktree add .uat/fix/<cid> -b uat-fix/<cid>` from the repo's base branch (not the UAT workspace).
2. Render the brief from `$S/resources/fix-brief-template.md` using the cluster's `findings.json` entry (goal, bug, acceptance, test command, regression dir). Never paste credentials into it.
3. Owner implements in the worktree, small commits. Delegates run sandboxed, no push.
4. **Prove**: `python3 $S/scripts/uat_env.py sync .uat/fix/<cid> --root .` (copies the fix over the UAT workspace and restarts), then `python3 $S/scripts/replay.py --root . --only <cid> --passing`. It must exit 0 (repro passes on every run). Then run the project's own test command in the worktree. Both must pass.
5. **Review**: a different agent than the author reviews the diff (`codex` review mode or `opencode-bridge:review`, else `code-review`; `security-review` for security clusters). Claude triages every finding: fix, reject with reason, or ask the user. Reject out-of-scope changes.
6. Failure after the review loop: at most 2 retries, then Claude takes it, then ask the user. Do not loop.

## 3. Full regression
After all selected clusters are merged into one integration branch `uat-fix/all` (ask before creating): `uat_env.py sync` that tree, then `replay.py --root . --passing` over everything accepted (all pinned repros), then the project's full tests. Anything red goes back to step 2 for that cluster.

## 4. Close
- `verify-and-stop` before saying done. Re-run `/uat-execute` only on the areas touched if the user wants a second pass (re-plan not needed).
- Report: per cluster status (fixed + proven / rejected / needs human), the branch names, diffs summary, anything skipped. Be honest about unverified items.
- **Never push, merge or deploy without the user's explicit approval.** Offer the PR text (normal prose) only when asked.
- Set phase `fixed` only for clusters proven. Keep the pinned repros in `.uat/regression/` (offer to copy them into the repo's own test suite).
