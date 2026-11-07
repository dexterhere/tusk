---
name: uat-init
description: Stage 1 of UAT Swarm. Scans the repo, recommends how many tester agents and which free tools fit, asks the user a short set of questions, then builds a scrubbed UAT environment under .uat/. Use before /uat-plan.
disable-model-invocation: true
---

# /uat-init: size the swarm, ask, build the UAT environment

`S=${UAT_HOME:-$HOME/Work/uat-swarm}`. All state lives in `<repo>/.uat/`. Chat in caveman style; files you write stay normal prose.
You are the Elephant: you hold context and decide. Testers are blind. Test-only: you never change the user's source here.

## 1. Preflight (stop and report on failure; never block silently)
- `python3 --version`, `git --version`, `docker info` (docker is optional; only docker-based tools need it).
- Agent CLIs: `claude --version`, `codex --version`, `opencode --version`. Missing family = that family is dropped, tell the user.
- `jg doctor` (retrieval). If it fails, fall back to Grep/Glob and say so.
- Ping each configured cheap model (cheap, 90 s): `python3 $S/scripts/run_agent.py --ping --family codex --model gpt-6-luna,gpt-5.6-luna,gpt-reserve` and the same for opencode models. Keep only models that answered OK; record them in `.uat/uat.json` under `models`.
- Disk and RAM: read `disk_free_gb` / `mem_available_gb` from detect. If disk < 10 GB or RAM < 4 GB, warn and lower `max_parallel`.

## 2. Check out the whole repository
1. `jg files .` to see what retrieval may read; flag `.env`, keys, dumps, customer data as excluded. Nothing secret goes to Codex/OpenCode/`jg`.
2. `python3 $S/scripts/uat_detect.py --root .` writes `.uat/detect.json`: size, stack tags, areas, applicable tools (with installed flag), `recommended_agents`, `recommended_parallel`.
3. `python3 $S/scripts/uat_env.py guard --root .`. If it reports production-looking hosts or live keys, stop: show them and ask the user to fix the config or confirm they are fake. Never start an env with a hit.
4. Add `.uat/` to `.gitignore` (ask first if the file is tracked).

## 3. Ask the user (AskUserQuestion, up to 4 per call, 2 rounds max)
Round 1:
- **Scope**: whole system / selected areas (offer the top areas from detect) / one flow.
- **Swarm size**: recommended N (from detect, a multiple of 3) / smaller / larger / "adaptive: start at 3 and grow by yield". State the cheap-model plan and that unused agents cost nothing.
- **Free tools** (multi-select, only those applicable): core (Playwright, Hurl, Schemathesis, Hypothesis/fast-check) / security leads (Semgrep, gitleaks, Trivy, ZAP) / resilience (k6, Toxiproxy, WireMock/Mockoon, libfaketime) / quality (axe, pa11y, Lighthouse, coverage, mutation). Mention LocalStack needs account terms checked; default off.
- **Budget**: wall-time cap (default 2 h) and max rounds (default 4).
Round 2 (plain chat text, open-ended): how to **install deps**, **start** the app, its **health URL** and **base URL**, which **paths hold state** (db files, upload dirs), optional **snapshot/restore commands** for databases, and test accounts/roles with FAKE credentials. Offer sensible guesses from detect (e.g. `npm ci`, `npm run dev`) for the user to confirm.

## 4. Write config and build
1. Write `.uat/uat.json` (overrides over `$S/uat.defaults.json`): chosen models, `swarm.max_agents`, `swarm.max_parallel` (= recommended_parallel), `env.*`, `tools.enabled`, `stop.*`. Put fake env vars under `env.vars`; never real secrets.
2. Show the user the plan: agent count, per-family models, tools to install, estimated disk. Get a yes (AskUserQuestion, one question).
3. Install tools (isolated in `.uat/tools`, docker images pull on first use): `python3 $S/scripts/uat_tools.py install <names> --root .`. `system` tools (hurl, libfaketime) are manual: print the exact command for the user. Playwright is already present.
4. `python3 $S/scripts/uat_env.py workspace --root .` (scrubbed copy, runs `setup_cmd`, git baseline for tamper detection).
5. `python3 $S/scripts/uat_env.py up --root .` then `snapshot baseline`. Smoke test the base URL.
6. Write `.uat/state.json`: `{"phase":"initialized","created":"<iso>","agents":N,"parallel":P}`.

## 5. Finish
Print: N agents (per-family models), tools ready/missing, base URL, the `.uat/` path, and next step `/uat-plan`. If the env would not start, show the last 20 lines of `.uat/logs/app.log` and stop; do not retry more than twice.
