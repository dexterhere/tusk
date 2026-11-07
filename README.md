<p align="center"><img src="web/logo.svg" alt="UAT Swarm logo: the orchestrator elephant" width="96" height="96"></p>

# UAT Swarm

**Hand your app to a herd of cheap, blind AI testers. Get back only the bugs a script could reproduce.**

An orchestrator (the elephant, which remembers everything) sends many low-cost tester agents into isolated copies of your app. A deterministic script, not a model, replays every claim and decides what is real. Confirmed bugs go into a fix queue and a knowledge graph. **Nothing is fixed, pushed or deployed until you say yes in chat.**

## Use it in 5 steps

**You need:** Linux, `python3`, `git`, `bwrap` (bubblewrap), and at least one agent CLI that is logged in (`claude`, `codex` or `opencode`; others in "Any agent" below). Claude Code runs the slash commands.

1. **Install the skills once:** `for s in skills/*; do ln -sfn "$PWD/$s" ~/.claude/skills/$(basename $s); done`
2. **Open your project** in Claude Code and run `/uat-full`.
3. **Answer the setup questions.** Where to run (this computer or an ssh host), and the team: one orchestrator plus at least one worker, the same model or different ones. Models are pinged before they are trusted.
4. **Watch it live** at `http://127.0.0.1:7777` (the dashboard starts for you). It shows who is testing what, findings, the fix cycle, the audit log and the knowledge graph.
5. **Decide.** When testing ends it shows the report and asks which fixes may start. Approve some, all or none. Approved fixes are written on a branch and proven by replay; the next run re-tests them and marks each *verified* or *regressed*.

Prefer control over automation? Run the stages yourself: `/uat-init`, `/uat-plan`, `/uat-execute`, `/uat-feedback`, `/uat-fix`; `/uat` shows where you are.

**Safe by default:** tester commands run in a bubblewrap sandbox, only in disposable copies of your app; secrets are never sent to agents; the dashboard listens on 127.0.0.1 only; every phase, verdict and fix is in a tamper-evident audit log. Details under "How trust works".

---

A swarm of cheap, blind tester agents (Claude Haiku, Codex, OpenCode free models) tries to break a system. A script, not a model, decides what is real. Every
confirmed result lands in a fix queue; fixes start only after a human approves them.

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

`python3 scripts/dashboard.py --root .` then open the URL it prints. The page is read-only with one exception: the **Team** page can save a *proposal* for a new agent team. A proposal changes nothing by itself; the orchestrator shows it to you and applies it only after you agree in chat (`/uat-full apply`). Nothing about fixes, approvals or commands can be changed from the page. It has a collapsible sidebar, light/dark theme and Figtree, and pages for Overview, Agents, Tests, Findings, Fix cycle, Audit log, Report and the Knowledge graph.

## Knowledge base and the fix cycle

`.uat/kb/` (set `kb.dir` to move it inside the project, e.g. to commit it) holds `graph.json` (a few KB) and `DIGEST.md` (60 lines, for an agent to read first). Nodes: issues, files, rules, areas, pinned repro tests, fixes. Links: issue in file, issue violates rule, test guards issue, fix resolves issue, fix touches file, and `suspected_cause` when a new issue shows up in code an earlier fix touched.

```
find (execute) -> report + kb ingest -> you approve in chat -> kb impact (what else lives in this code)
  -> kb handoff (fix brief incl. Do-not-break list) -> fix + prove + related repros -> kb fixed
  -> next /uat-execute: kb retest --run -> verified | regressed (back in the queue)
```

Issue status: open, approved, fixing, fixed (proven, waiting), verified (passed again in a later run), regressed, wontfix, not_seen. In ssh mode the graph lives on the orchestrating computer and is never overwritten by the host mirror; testers on the host only see the behaviour-only re-verify list in their brief. Testers stay blind to code: `kb.py link` (done in `/uat-feedback`) and `kb.py fixed` add the code locations.
Four tabs: **Live** (each agent as a pet with a label popup, who orchestrates, what each is doing now, rules under test), **Graph** (orchestrator, agents, charters,
rules, findings sized by severity), **Findings** (severity bars, new clusters per round, cluster table), **Fixes** (queue with Approve / Reject).

## How trust works

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

## Layout

`skills/` commands · `agents/` personas · `resources/` schema, tool catalog, briefs · `scripts/` stdlib Python · `web/` dashboard page · `tests/` unit tests and a demo app with 5 seeded bugs · `calibration/` recorded model runs.

Config: `uat.defaults.json`, overridden by `<repo>/.uat/uat.json`. Test: `python3 -m unittest discover -s tests -v`.
Install: `for s in skills/*; do ln -sfn "$PWD/$s" ~/.claude/skills/$(basename $s); done`

## Audit log integrity

`.uat/audit.jsonl` is hash-chained and its latest head is also kept in `~/.local/state/uat/` (override with `UAT_STATE_DIR`). `python3 scripts/audit.py verify` checks both. A deleted, truncated or rewritten log fails verification. If you wipe `.uat` on purpose, run `python3 scripts/audit.py reset` once. This is not a MAC: the same OS user can alter both copies. Events recorded on an SSH host (`events.host.jsonl`) are not part of the chain.

## Journey, agents and assets

The full cycle, what is recorded at each step, and how to plug in any agent harness: `docs/JOURNEY.md`. Agent avatars are DiceBear "Gaze" (CC0) and icons are Iconoir (MIT), both vendored in `web/vendor/` (see the README there): the dashboard makes no requests to the internet.

## Team and the one dashboard write

`.uat/team.json` holds one orchestrator and one or more workers (roles `tester`, `verifier`, `fixer`; one worker may hold all three; any worker may use the same program and model as the orchestrator). `scripts/team.py` validates and applies it.

The dashboard's only POST is `/api/team/propose`. Guards, all enforced server-side (see `tests/test_team.py`): loopback-only, `Host` check, exact `Origin`, `Content-Type: application/json` plus a custom header (no cross-site form can send it), a per-process random edit token compared in constant time (kept in an owner-only file, read with `python3 scripts/team.py token`; never shown in the page or chat), 8 KB body cap, 10 proposals a minute, strict schema, program names from the harness registry only, **models only from the configured list** (nothing free-typed reaches a command line), no command templates, paths or secrets. It writes `.uat/team.pending.json` and one audit record; it cannot change `team.json`, `uat.json` or run anything. Applying (ping every model, write config, audit) is done by the orchestrator in chat. Residual risk: anyone who can read your owner-only state files or run as you can already do more than this.

## Project

[MIT License](LICENSE) · [Contributing](CONTRIBUTING.md) · [Security policy](SECURITY.md) · [Code of conduct](CODE_OF_CONDUCT.md)
