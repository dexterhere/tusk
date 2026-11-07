# uat-swarm

Cheap-model tester swarm that tries to break a system and writes a fix-ready report.
Inspired by EGM (fresh-context reviewers + a context-holding orchestrator), applied to testing.

| Command | Does |
|---|---|
| `/uat-init` | Scan repo, recommend swarm size + free tools, ask questions, build scrubbed UAT env in `.uat/` |
| `/uat-plan` | graphify + jg, extract business rules (the oracle) with sources, static/coverage leads, charters, user approves |
| `/uat-execute` | Adaptive waves of blind testers (Claude Haiku, Codex luna, OpenCode free). Script validates, replays 3x, dedupes |
| `/uat-feedback` | Verifier judges only disputed clusters; builds `report.md`, `findings.json`, `junit.xml`, pins regression tests |
| `/uat-fix` | Worktree per bug, author != reviewer, proven by the pinned repro + full regression; never pushes unasked |
| `/uat-status` | Status, ledger, resume, `down`, `clean` |

Key ideas: findings are executable repros (exit non-zero = bug, 0 = fixed); a script, not a model, decides real vs flaky vs false;
test-only scope is enforced by sandbox + git tamper check; swarm size is computed, 9 is only the reference.

Layout: `skills/` (commands), `agents/` (personas), `resources/` (schema, tool catalog, brief templates), `scripts/` (stdlib Python), `tests/` (demo app with 5 seeded bugs).
Config: `uat.defaults.json`, overridden per project by `<repo>/.uat/uat.json`.

Test: `python3 -m unittest discover -s tests -v`
Calibrate a model: run the swarm against `tests/demo_app` (start: `python3 tests/demo_app/app.py 8080`, rules in `rules.json`), then
`python3 scripts/calibrate.py --seeded tests/demo_app/seeded_bugs.json`.
Install: `ln -s <this>/skills/uat-* ~/.claude/skills/`
