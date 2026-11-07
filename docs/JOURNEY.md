# The UAT journey, end to end

One loop, eight steps. The dashboard's **Journey** page shows where you are, who is doing what, and the next command. Everything below is recorded: assignments and decisions in the hash-chained audit log, findings with the agent and the model that actually answered, issue history in the knowledge graph.

| # | Step | What happens | Who | You do | Recorded in |
|---|---|---|---|---|---|
| 1 | **Set up** `/tusk-init` | Pick where tests run (this computer, or a host over ssh), which agent harnesses and models to use, sandbox. Each model is pinged first. | you + orchestrator | answer 3-4 questions | `.tusk/tusk.json`, `ledger.jsonl` |
| 2 | **Plan** `/tusk-plan` | Rules are pulled from your docs and code; each becomes a test assignment (charter) with a lens. | orchestrator | approve the plan | `plan/plan.json` |
| 3 | **Test** `/tusk-execute` | First, earlier fixes are re-tested (`kb.py retest --run`). Then each tester gets its own copy of the app (lane) and a brief; the assignment is logged. | tester agents | watch, or leave | `assign` events, `agents/<id>/`, `ledger.jsonl` |
| 4 | **Judge** `/tusk-feedback` | Every finding is re-run several times in a clean lane. A script decides. Disputed ones go to a fresh verifier. | scripts, verifiers | nothing | `replays/`, `clusters.json`, `verdicts.json` (by whom, which model) |
| 5 | **Report** | Fix-ready report, pinned repro per problem, knowledge graph updated, code locations linked. | orchestrator | read it | `report/`, `kb/graph.json` |
| 6 | **You decide** | The orchestrator lists what could be fixed with its risk. You say yes or no in chat. The dashboard cannot approve. | you | approve | `fixqueue.json`, audit log |
| 7 | **Fix** `/tusk-fix` | For each approved issue the graph says what else lives in the same code. A fix brief with a "do not break" list goes to a fix agent in its own git worktree. Proof = its repro and every related repro pass. | fix agents, a different agent reviews | approve the plan | `assign` events, `kb fixed`, git branch |
| 8 | **Re-test** (next `/tusk-execute`) | The pinned repros of every fix run again. Pass: `verified`. Fail: `regressed`, back to step 6. A new issue in code a fix touched is linked as a suspected cause. | scripts | nothing | `kb/graph.json` history |

Issue status: open, approved, fixing, fixed (proven, waiting for the next run), verified, regressed, wontfix, not_seen.

## What you can see live

The page is pushed new state the moment a file under `.tusk/` changes (server-sent events; it falls back to polling every 2 s). A short message appears for each start, finish, assignment, verdict, fix and re-test. The **Work board** shows planned, in progress and done work with the owner (agent, model, lane). **Agents > Map** draws who was given what, who found which issue and who judged it. **Knowledge** shows code, rules, issues, tests and fixes; click a file or issue for its complete track-down, filter by tag (`area:`, `rule:`, `model:`, `agent:`, `status:`, `dir:`, `ext:`).

## Any agent, not only three

Testers, verifiers and fix agents are run through `scripts/harness.py`. `claude`, `codex` and `opencode` are verified on this machine. `gemini`, `qwen`, `aider`, `goose`, `amp`, `cursor`, `copilot` and `crush` are presets taken from each tool's headless flags and are **not verified**: log in, then `python3 scripts/run_agent.py --ping --family <name> --model <m>` must answer OK before use. Any other agent: add a `harness` entry in `.tusk/tusk.json` (command template, `arg`/`stdin`/`file` prompt, files to copy into its throwaway HOME) or point `cmd` at a small wrapper script that reads the prompt file and writes `./findings/F-*.json`. The orchestrator can be any agent that can run the stage commands in the table (they are plain scripts); the `/tusk*` slash skills are the Claude Code packaging of them.

## One command: /tusk-full

`/tusk-full` runs steps 1-4 and 6-7 for you and stops at step 5 (your decision on fixes). On first use it asks how to shape the team: one orchestrator and at least one worker, the same LLM for everything or different ones. Change it later with `/tusk-full reconfigure`, or propose a change on the dashboard's **Team** page and confirm it in chat with `/tusk-full apply`. See the README for what the dashboard may and may not write.
