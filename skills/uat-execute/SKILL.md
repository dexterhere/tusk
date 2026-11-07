---
name: uat-execute
description: Stage 3 of UAT Swarm. Runs the approved charters with a cheap-model tester swarm (Claude Haiku, Codex, OpenCode free models) in parallel, each tester in its own sandboxed lane, with schema validation and stress replay after every wave. Use after /uat-plan is approved.
disable-model-invocation: true
---

# /uat-execute: run the swarm in waves

`S=${UAT_HOME:-$HOME/.tusk}`. Requires phase `planned`. Caveman chat. You are the orchestrator; testers are blind and cheap, so rigor comes from narrow charters, schema, isolation and replay, not model strength. Call host-side scripts as `python3 $S/scripts/uat.py <script> ... --root .` (works for local and ssh targets).

## 0. Preconditions
- `uat.py uat_env lanes status` healthy, else `lanes up`. Dashboard running (`/uat dashboard`), tell the user the link.
- Read `.uat/uat.json` (`swarm`, `models`, `stop`) and `.uat/plan/plan.json`. Pending charters by priority.
- Phase `executing`; keep `round`, `spent_agents`, `started` in `state.json`. Emit progress for the dashboard: `python3 $S/scripts/uat_event.py emit orchestrator phase text="wave 1: 3 agents" --root .`

## 0b. Re-test earlier fixes first (the loop closes here)
If `.uat/kb/graph.json` lists issues in status `fixed` or `verified` (`python3 $S/scripts/kb.py retest`), replay their pinned repros against the current code before any new wave: `python3 $S/scripts/kb.py retest --run --root .`. Passed -> `verified`. Failed -> `regressed`, back in the fix queue as `proposed`. Tell the user which. `make_brief.py` also adds a "previously fixed, re-verify these first" list (behaviour only, no code locations) to briefs for the same area, so testers check fixed behaviour again with fresh eyes.

## 1. Assign agents and lanes
Agent ids `A1..An`. Agent `i`: family = `families[i % 3]` (only working ones), next pending charter whose lens fits, rotating lenses so each family gets each lens over time. Models come from `models.<family>.cheap` (list = fallback order). Never exceed `swarm.max_agents`.
**One agent = one lane.** A wave has at most `swarm.max_parallel` agents; agent k of the wave gets lane `L<k>`. Lanes are reset to baseline (code and data) by `run_agent.py` itself before each agent, so no tester ever sees another's state.
Brief: `python3 $S/scripts/make_brief.py --agent A4 --charter CH-07 --lane L2 --family codex --model gpt-5.6-luna --root .` (also writes the agent label the dashboard shows).

## 2. Dispatch a wave
Every family, including Claude Haiku, goes through the same runner, so confinement, timeout, tamper check, ledger and live events are identical (do NOT use the Agent tool for testers):
```
python3 $S/scripts/uat.py run_agent --id A4 --lane L2 --family codex --model "gpt-5.6-luna" --brief .uat/agents/A4/brief.md --root .
```
Run the wave's commands as parallel background Bash calls; wait for the completion notifications, do not poll or sleep. Exit code 2 = the agent edited the system under test: it was reverted and its findings quarantined, note it. Honour `stop.agent_timeout_s` and `stop.budget_wall_s`. Wave 0 may be the tool lane `CH-T1` (`agents/tool-runner.md`) in its own lane.

## 3. After every wave: judge by script, not by model
```
python3 $S/scripts/validate_findings.py --root .
python3 $S/scripts/uat.py replay --root .      # parallel across lanes; stress runs for concurrency findings; sandboxed
python3 $S/scripts/dedupe.py --root .
```
New clusters = fingerprints not seen before with verdict `accept`, `needs_verifier`, or `watch`. Append to `.uat/rounds.jsonl` one JSON line: `{"round":n,"agents":k,"findings":f,"new_clusters":c,"wall_s":s}`.

## 4. Adapt
- **Yield-driven expansion**: accepted areas get follow-up charters (neighbouring rules, another lens, a different family than the first finder).
- **Stop** when: `stop.saturation_rounds` waves in a row add no new cluster; all non-deferred charters done; `max_rounds`; wall budget; `max_agents` spent.
- **Model health**: a model failing twice (rc != 0 or zero valid findings) is rotated out for the run; say so. A family with no working model is dropped.
- A concurrency finding that one agent saw but replay could not reproduce is **kept** (`watch`), never dropped; a flaky one goes to the verifier.
- Do not analyse findings yourself here; the report stage does that.

## 5. Finish
Phase `executed`. Print: rounds, agents per family/model, wall time, clusters by verdict, dropped models, tamper events, sandbox level. Next: `/uat-feedback`.
