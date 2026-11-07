---
name: uat-init
description: Stage 1 of UAT Swarm. Asks WHERE to run (this computer, or a remote host over SSH), scans the repo, recommends swarm size and free tools, then builds a scrubbed UAT environment with isolated lanes under .uat/. Use before /uat-plan.
disable-model-invocation: true
---

# /uat-init: size the swarm, ask, build the UAT environment

`S=${UAT_HOME:-$HOME/.tusk}`. All state lives in `<repo>/.uat/`. Chat in caveman style; files you write stay normal prose.
You are the Elephant: you hold context and decide. Testers are blind. Test-only: you never change the user's source here.

## 0. Where does UAT run? (ask first, AskUserQuestion)
- **This computer**: app, lanes, tools and tester CLIs run here. `target.mode = "local"`.
- **Remote host over SSH** (a VPS, lab box, any machine you can `ssh` into): the workspace, app lanes, tools, tester CLIs and replay run THERE; this computer keeps the orchestrator, dashboard and reports and mirrors `.uat/` back. `target.mode = "ssh"`. Ask for the ssh destination (offer `Host` names from `~/.ssh/config`; key auth only, no password prompts), and a run dir (default `~/uat-runs/<project>`). Then `ssh -o BatchMode=yes <host> true`; on failure stop and show the error.
Write `target` into `.uat/uat.json`. From here every host-side script is called as `python3 $S/scripts/uat.py <script> ... --root .`, which does the right thing for both modes.

## 1. Preflight (stop and report on failure; never block silently)
- `python3 $S/scripts/uat.py hostinfo --root .` prints the capabilities of the machine that runs UAT (local or the ssh host): python, git, rsync, **bwrap**, docker, `claude`/`codex`/`opencode`, cpus, free RAM and disk, `recommended_parallel`.
- **bubblewrap is required** (sandbox for agent-written commands). If missing, tell the user to install it (`pacman -S bubblewrap` / `apt install bubblewrap`). Only if they refuse, set `sandbox.require=false` and say plainly that tester commands then run unconfined; the report will record it.
- Agent CLIs missing on the host = that family is dropped, tell the user. On an ssh host each CLI must be logged in there too (`ssh host claude --version` etc.).
- `jg doctor` locally (retrieval); fall back to Grep/Glob and say so.
- **Verify model names, do not trust the defaults**: `opencode models` / the codex model list, then ping each candidate (90 s) through `python3 $S/scripts/uat.py run_agent --ping --family codex --model <m> --root .`. Keep only models that answered OK; write them to `models` in `.uat/uat.json`.
- Disk < 10 GB or RAM < 4 GB on the executing machine: warn and lower `max_parallel`.

## 2. Check out the whole repository
1. `jg files .` to see what retrieval may read; flag `.env`, keys, dumps, customer data as excluded. Nothing secret goes to Codex/OpenCode/`jg`/the ssh host.
2. `python3 $S/scripts/uat_detect.py --root .` writes `.uat/detect.json` (size, stack, areas, tools). Swarm size = min(detect `recommended_agents`, what the **executing** machine can run: hostinfo `recommended_parallel`).
3. `python3 $S/scripts/uat.py uat_env guard --root .`. Production-looking hosts or live keys: stop, show them, ask to fix or confirm fake. Never start an env with a hit.
4. Add `.uat/` to `.gitignore` (ask first if tracked).

## 3. Ask the user (AskUserQuestion, up to 4 per call, 2 rounds max)
Round 1:
- **Scope**: whole system / selected areas / one flow.
- **Swarm size**: recommended N (multiple of 3) / smaller / larger / "adaptive: start at 3, grow by yield". Say how many run in parallel (= lanes).
- **Free tools** (multi-select, applicable only): core (Playwright, Hurl, Schemathesis, Hypothesis/fast-check) / security leads (Semgrep, gitleaks, Trivy, ZAP) / resilience (k6, Toxiproxy, WireMock/Mockoon, libfaketime) / quality (axe, pa11y, Lighthouse, coverage, mutation).
- **Budget**: wall-time cap (default 2 h), max rounds (default 4).
Round 2 (plain chat): target **kind**: `service` (HTTP app: give start command, health URL, base URL; the start command must take its port from `{port}` or `$UAT_PORT` so each parallel tester gets its own copy) or `cli` (library/CLI/desktop app: no server, testers run commands in their private workspace copy; give build/setup command and which dirs hold build output = `env.writable_paths`). Install deps command, state paths (db files, upload dirs), restore/lane-setup commands for external DBs (use `{lane}` in names so lanes do not share a DB), FAKE test accounts. Offer guesses from detect.

## 4. Write config and build
1. Write `.uat/uat.json` (overrides over `$S/uat.defaults.json`): `target`, verified `models`, `swarm.max_agents`, `swarm.max_parallel`, `env.*` (incl. `kind`, `base_port`), `sandbox`, `tools.enabled`, `stop.*`. Fake env vars under `env.vars`; never real secrets. Write `.uat/orchestrator.json` `{"name":"Elephant","model":"<your model id>"}` so the dashboard says who orchestrates.
2. Show the user the plan: agent count, per-family models, tools to install, estimated disk. Get a yes (AskUserQuestion, one question).
3. Install tools (isolated in `.uat/tools`, docker images pull on first use): `python3 $S/scripts/uat_tools.py install <names> --root .`. `system` tools (hurl, libfaketime) are manual: print the exact command for the user. Playwright is already present.
4. `python3 $S/scripts/uat.py uat_env workspace --root .` (scrubbed golden copy, runs `setup_cmd`, git baseline for tamper detection; in ssh mode it is pushed to the host first).
5. `python3 $S/scripts/uat.py uat_env lanes up --root .` (one isolated copy + port per parallel tester) and `lanes status`. Smoke test one lane URL.
6. Start the dashboard (`/uat dashboard`) and give the user the link.
7. Write `.uat/state.json`: `{"phase":"initialized","created":"<iso>","agents":N,"parallel":P}`.

## 5. Finish
Print: N agents (per-family models), tools ready/missing, base URL, the `.uat/` path, and next step `/uat-plan`. If the env would not start, show the last 20 lines of `.uat/lanes/L1/app.log` and stop; do not retry more than twice.
