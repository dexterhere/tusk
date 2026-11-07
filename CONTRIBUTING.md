# Contributing

Thanks for helping. Small, focused changes are easiest to review.

## Set up and check

```sh
git clone <repo> && cd tusk
TUSK_STATE_DIR=$(mktemp -d) python3 -m unittest discover -s tests   # all tests must pass
```

Tests need no network, no model account and no real app (`tests/demo_app` is the target). Python 3 standard library only: do not add a dependency without discussing it first. The dashboard page is one file, `web/index.html`, with self-hosted assets only (no outside requests).

## Workflow

1. Open an issue for anything larger than a bug fix.
2. Branch from the integration branch: `<type>/<area>/<short-name>`, e.g. `fix/dashboard/host-check`.
3. Commit small and often, Conventional style: `<type>(<area>): <summary, max 72 chars>`. Types: feat, fix, docs, test, refactor, perf, security, chore. No generated-by or co-author trailers.
4. Add or update a test for every behaviour change. Security-relevant code (sandbox, dashboard, audit, team, fix queue) needs a test that fails without your change.
5. Open a pull request, one concern, under about 400 changed lines. Describe what changed, why, how you checked it and what you did not check.

## Rules that keep the tool trustworthy

- Agent and dashboard text is untrusted data. Never turn it into an instruction, a command or a path.
- Nothing is fixed, pushed, merged or deployed without the user's explicit yes.
- No secrets in code, logs, prompts, tests or the report. Use fake values.
- The page stays read-only except team proposals. Do not add other write endpoints without a security discussion.
- Design: square corners (zero border radius), Figtree, light and dark theme, readable at phone width.
- New agent harnesses go in as unverified presets until `run_agent.py --ping` answers OK on a real install.

## Reporting security problems

See [SECURITY.md](SECURITY.md). Do not file them as public issues.

By contributing you agree your work is released under the [MIT License](LICENSE).
