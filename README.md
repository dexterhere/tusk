<div align="center">

<img src="web/logo.svg" alt="Tusk logo: the orchestrator elephant" width="110" height="110">

# Tusk

**User-acceptance testing by a herd of AI agents.**<br>
Cheap, blind testers try to break your app. A script proves what is real. You decide what gets fixed.

![License: MIT](https://img.shields.io/badge/license-MIT-black)
![Platform: Linux](https://img.shields.io/badge/platform-linux-black)
![Python: standard library only](https://img.shields.io/badge/python-stdlib%20only-black)
![Nothing fixed without your yes](https://img.shields.io/badge/fixes-only%20with%20your%20yes-black)

[Install](#install-in-one-line) · [First run](#your-first-run) · [Pick your task](#what-do-you-want-to-do) · [How it works](#how-it-works) · [Trust](#how-trust-works) · [Docs](docs/INSTALL.md)

</div>

---

## Install in one line

Pick one. Each ends the same way: the `/uat` commands appear in Claude Code.

<details open>
<summary><b>curl (no Node needed)</b></summary>

```sh
curl -fsSL https://raw.githubusercontent.com/dexterhere/tusk/main/install.sh | sh
```
It clones Tusk to `~/.local/share/tusk`, checks your machine, and links the commands. Prefer to read it first? [install.sh](install.sh) is 10 lines.
</details>

<details>
<summary><b>npm</b></summary>

```sh
npm i -g @dexterhere/tusk && tusk setup
```
`tusk setup` is a short guided check: it tells you what is missing, links the commands, and offers to open the dashboard. (Do not use `npx`: its cache is temporary, and Tusk refuses to link into it.)
</details>

<details>
<summary><b>git</b></summary>

```sh
git clone https://github.com/dexterhere/tusk.git ~/.local/share/tusk && ~/.local/share/tusk/bin/tusk setup
```
</details>

**Needs:** Linux · `python3` 3.10+ · `git` · `bwrap` (bubblewrap) · one logged-in agent CLI (`claude`, `codex` or `opencode`) · [Claude Code](https://claude.com/claude-code) for the slash commands. Not sure? Run `tusk doctor`: it lists what is missing and the exact fix.

## Your first run

```text
cd your-project        # the app you want to test
claude                 # open Claude Code here
/uat-full              # Tusk asks 4-5 questions, then does the rest
```

1. **Where to run:** this computer or an ssh host.
2. **Your team:** one orchestrator and at least one worker, the same model or different ones. Each model is pinged before use.
3. **How to start your app:** command, health URL, **fake** test accounts only.
4. **Watch:** open `http://127.0.0.1:7777`. It shows who tests what, findings, the fix cycle and the audit log.
5. **Decide:** you get a report and a list of problems. Say which fixes may start: some, all or none.

## What do you want to do?

| I want to... | Do this |
|---|---|
| Run everything and just answer questions | `/uat-full` |
| Go step by step | `/uat-init` → `/uat-plan` → `/uat-execute` → `/uat-feedback` → `/uat-fix` |
| See where I am | `/uat` or the dashboard's **Journey** page |
| Change the agents or models | `/uat-full reconfigure`, or the dashboard's **Team** page then `/uat-full apply` |
| Test on another machine | choose "ssh host" in `/uat-init` ([how](#where-it-runs)) |
| Add an agent that is not claude/codex/opencode | [Any agent](#any-agent-not-only-three) |
| Check my machine | `tusk doctor` |
| Update or remove Tusk | `tusk update` · `tusk uninstall` |
| Report a security problem | [SECURITY.md](SECURITY.md) |
| Contribute | [CONTRIBUTING.md](CONTRIBUTING.md) |

<details>
<summary><b>All <code>tusk</code> commands</b></summary>

| Command | Does |
|---|---|
| `tusk setup` | Guided: check the machine, link commands, offer the dashboard |
| `tusk doctor` | List requirements; exit 1 if a required one is missing |
| `tusk install` / `uninstall` | Link / unlink the `/uat*` commands (only links that point at this checkout) |
| `tusk dashboard [--root D] [--port N]` | Start the live dashboard for a project |
| `tusk token` | Print the dashboard's edit token (never paste it into chat) |
| `tusk update` | `git pull --ff-only`, then re-link |
</details>

**Safe by default:** tester commands run in a bubblewrap sandbox on disposable copies of your app; secrets never go to agents; the dashboard listens on 127.0.0.1 only; every phase, verdict and fix is in a tamper-evident audit log; nothing is fixed, pushed or deployed without your yes.

---

## How it works

1. **Set up** (`/uat-init`): you choose where to run and which agents; Tusk builds a scrubbed copy of your app and one isolated *lane* per parallel tester.
2. **Plan** (`/uat-plan`): business rules are extracted from your docs and code, with sources, and become test assignments you approve.
3. **Test** (`/uat-execute`): blind tester agents (cheap models) try to break the app, one lane each.
4. **Prove** (`/uat-feedback`): a script replays every claim; only reproducible problems are merged, ranked and put in the report and fix queue. A verifier agent judges only the disputed ones.
5. **Fix** (`/uat-fix`), only for issues you approve in chat: a fixer works on a branch and proves the fix by replay.
6. **Re-test**: the next run marks each fix *verified* or *regressed*, using the knowledge graph to check what else the change could break.

The tool is a testing environment that builds a knowledge graph of your code, its bugs and its tests. It is not a CI replacement and it does not edit your source during testing.

## Where it runs

`/uat-init` asks first:

| Choice | What happens |
|---|---|
| **This computer** | App copies, tools, tester CLIs and replay run locally. |
| **Remote host over SSH** (VPS, lab box, spare laptop) | The workspace, app copies, tools, tester CLIs and replay run on the host. Orchestrator, dashboard and reports stay here; allowlisted results are mirrored back. Needs only `ssh`, `rsync`, `python3`, `bwrap` on the host, key auth, and each agent CLI logged in there. |

Swarm size is bounded by the machine that runs the tests (`scripts/hostinfo.py`), not by the one you type on.

## Commands

| Command | Does |
|---|---|
| `/uat-full` | The orchestrator runs the whole cycle for you. First use asks how to shape the team (one orchestrator, at least one worker; same LLM or different). It still stops before any fix and waits for your yes. `reconfigure` / `apply` change the team |
| `/uat` | Status, starts the dashboard, routes to the next stage |
| `/uat-init` | Choose where to run, scan the repo, verify model names, build the scrubbed environment and lanes |
| `/uat-plan` | Extract business rules (the oracle) with sources, make charters, user approves |
| `/uat-execute` | Waves of testers, one sandboxed lane each; validate, replay, dedupe after every wave |
| `/uat-feedback` | Verifier judges only disputed clusters; report, JUnit, pinned regression repros, fix queue |
| `/uat-fix` | Fix agents work only on items the user approved; proven by replay |
| `/uat-status` | Ledger, resume, `down`, `clean` |

`scripts/swarm.py` is the deterministic wave runner (used for calibration and unattended runs). `scripts/dashboard.py` serves the live page.

## Live dashboard

`python3 scripts/dashboard.py --root .` then open the URL it prints. The page is read-only with one exception: the **Team** page can save a *proposal* for a new agent team. A proposal changes nothing by itself; the orchestrator shows it to you and applies it only after you agree in chat (`/uat-full apply`). Nothing about fixes, approvals or commands can be changed from the page. It has a collapsible sidebar, light/dark theme and Figtree, and pages for Journey, Overview, Agents (cards, map, assignments), Tests, Findings, Fix cycle, Audit log, Report, Knowledge graph and Team. Every finding shows which agent and model found it. The page updates live (server-sent events). The Journey page and the work board show where you are in the cycle and who owns what.



<details>
<summary><b>Knowledge base and the fix cycle</b></summary>


`.uat/kb/` (set `kb.dir` to move it inside the project, e.g. to commit it) holds `graph.json` (a few KB) and `DIGEST.md` (60 lines, for an agent to read first). Nodes: issues, files, rules, areas, pinned repro tests, fixes. Links: issue in file, issue violates rule, test guards issue, fix resolves issue, fix touches file, and `suspected_cause` when a new issue shows up in code an earlier fix touched.

```
find (execute) -> report + kb ingest -> you approve in chat -> kb impact (what else lives in this code)
  -> kb handoff (fix brief incl. Do-not-break list) -> fix + prove + related repros -> kb fixed
  -> next /uat-execute: kb retest --run -> verified | regressed (back in the queue)
```

Issue status: open, approved, fixing, fixed (proven, waiting), verified (passed again in a later run), regressed, wontfix, not_seen. In ssh mode the graph lives on the orchestrating computer and is never overwritten by the host mirror; testers on the host only see the behaviour-only re-verify list in their brief. Testers stay blind to code: `kb.py link` (done in `/uat-feedback`) and `kb.py fixed` add the code locations.
<details>
<summary><b>How trust works</b></summary>


- **Lanes.** One tester = one lane: its own copy of the workspace, its own port, its own app process. Testers never share state, so a failure is theirs to explain.
- **Sandbox.** Agent CLIs, replayed repro commands and the app itself run under bubblewrap: read-only system allowlist (no `/run`, no docker socket), empty `$HOME`
  plus a throwaway copy of only the credential files that CLI needs, lane workspace read-only, one writable agent dir. Missing bubblewrap = refuse to run
  (`sandbox.require`), never silently weaker. **Network is open** (the tester must reach the target and its model API); a hostile tester could therefore send its
  throwaway credential copy out. Use scoped or short-lived tokens for tester CLIs.
- **Tamper check** after every agent: edits to the system under test are reverted and that agent's findings quarantined. Same for every family.
- **Trusted snapshots.** Before replay, each finding and its declared repro files are copied out of the agent-writable directory. Replay, dedupe, report and the fix
  queue use the copy and its digest. Verdicts, logs and quarantine live outside the agent dir.
- **Replay** runs the repro in a fresh scratch dir, in a clean lane: exit 1 = bug present, 0 = correct, anything else = the repro is broken (`error`). Replay proves
  repeatability, not truth, so a repro that never touches the target is sent to the verifier. Concurrency findings get 12 stress runs; a race seen by one agent that
  replay cannot reproduce is kept as `watch`, not dropped. Flaky results always go to the verifier.
- **Dedupe** never merges findings whose rule ids differ; weak title matches need 0.8 overlap, same area and category. Each cluster records why it merged.
  Agent identity comes from the directory and the orchestrator's label, not from the finding.
- **Fix queue.** Every accepted result is `proposed`. Approval is bound to the finding's content digest; if it changes, the item is `proposed` again.
- **SSH host** is trusted to execute, not to decide: only agent findings, events, replays and the ledger are mirrored back (allowlist, no symlinks, normalised
  permissions); clusters, verdicts, plan, fix queue and report are never overwritten from the host.
</details>
</details>

## Layout

`skills/` commands · `agents/` personas · `resources/` schema, tool catalog, briefs · `scripts/` stdlib Python · `web/` dashboard page, logo and vendored assets · `docs/` install guide and journey · `tests/` unit tests and a demo app with 5 seeded bugs · `calibration/` recorded model runs.

Config: `uat.defaults.json`, overridden by `<repo>/.uat/uat.json`. Test: `UAT_STATE_DIR=$(mktemp -d) python3 -m unittest discover -s tests`. Install: [docs/INSTALL.md](docs/INSTALL.md).

<details>
<summary><b>Audit log integrity</b></summary>


`.uat/audit.jsonl` is hash-chained and its latest head is also kept in `~/.local/state/uat/` (override with `UAT_STATE_DIR`). `python3 scripts/audit.py verify` checks both. A deleted, truncated or rewritten log fails verification. If you wipe `.uat` on purpose, run `python3 scripts/audit.py reset` once. This is not a MAC: the same OS user can alter both copies. Events recorded on an SSH host (`events.host.jsonl`) are not part of the chain.
</details>

## Any agent, not only three

Testers, verifiers and fixers run through `scripts/harness.py`. `claude`, `codex` and `opencode` are verified. `gemini`, `qwen`, `aider`, `goose`, `amp`, `cursor`, `copilot` and `crush` are presets from each tool's headless flags and are **not verified**: log in, then `python3 scripts/run_agent.py --ping --family <name> --model <m>` must answer OK. Any other agent: add a `harness` entry in `.uat/uat.json` or a small wrapper script. The orchestrator can be any agent that can run the stage scripts; the `/uat*` skills are the Claude Code packaging.

## Journey, agents and assets

The full cycle, what is recorded at each step, and how to plug in any agent harness: `docs/JOURNEY.md`. Agent avatars are DiceBear "Gaze" (CC0) and icons are Iconoir (MIT), both vendored in `web/vendor/` (see the README there): the dashboard makes no requests to the internet.

<details>
<summary><b>Team and the one dashboard write</b></summary>


`.uat/team.json` holds one orchestrator and one or more workers (roles `tester`, `verifier`, `fixer`; one worker may hold all three; any worker may use the same program and model as the orchestrator). `scripts/team.py` validates and applies it.

The dashboard's only POST is `/api/team/propose`. Guards, all enforced server-side (see `tests/test_team.py`): loopback-only, `Host` check, exact `Origin`, `Content-Type: application/json` plus a custom header (no cross-site form can send it), a per-process random edit token compared in constant time (kept in an owner-only file, read with `python3 scripts/team.py token`; never shown in the page or chat), 8 KB body cap, 10 proposals a minute, strict schema, program names from the harness registry only, **models only from the configured list** (nothing free-typed reaches a command line), no command templates, paths or secrets. It writes `.uat/team.pending.json` and one audit record; it cannot change `team.json`, `uat.json` or run anything. Applying (ping every model, write config, audit) is done by the orchestrator in chat. Residual risk: anyone who can read your owner-only state files or run as you can already do more than this.
</details>

## Project

[Install guide](docs/INSTALL.md) · [Journey](docs/JOURNEY.md) · [MIT License](LICENSE) · [Contributing](CONTRIBUTING.md) · [Security policy](SECURITY.md) · [Code of conduct](CODE_OF_CONDUCT.md)
