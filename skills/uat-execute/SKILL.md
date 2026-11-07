---
name: uat-execute
description: Stage 3 of UAT Swarm. Runs the approved charters with a cheap-model tester swarm (Claude Haiku, Codex luna, OpenCode free models) in adaptive waves, with schema validation and replay after each wave. Use after /uat-plan is approved.
disable-model-invocation: true
---

# /uat-execute: run the swarm in waves

`S=${UAT_HOME:-$HOME/Work/uat-swarm}`. Requires phase `planned`. Caveman chat. You are the Elephant; testers are blind and cheap, so rigor comes from narrow charters, schema, and replay, not model strength.

## 0. Preconditions
- `uat_env.py status --root .` healthy, else `up`. `restore baseline` before the first wave.
- Read `.uat/uat.json` (`swarm`, `models`, `stop`) and `.uat/plan/plan.json`. Pending charters sorted by priority.
- Mark `state.json` phase `executing`; keep `round`, `spent_agents`, `started` updated after every wave (resumable via `/uat-status`).

## 1. Assign agents
Agent ids `A1..An`. Agent `i` (0-based): family = `families[i % 3]`; take the next pending charter whose lens fits, rotating lenses so every family gets every lens over time. Model per family comes from `models.<family>.cheap` (a list = fallback order). Never exceed `swarm.max_agents` total.

For each: `python3 $S/scripts/make_brief.py --agent A4 --charter CH-07 --family codex --model gpt-6-luna --root .`

## 2. Wave 0: tool lane
Spawn the `uat-tool-runner` (Agent tool, model `haiku`, background) with brief for `CH-T1` (persona `$S/agents/tool-runner.md`). It runs the dynamic free tools and writes findings with `family: tool`.

## 3. Dispatch a wave (at most `swarm.max_parallel` agents at once)
- **claude** testers: Agent tool, `subagent_type: general-purpose`, `model: haiku`, `run_in_background: true`. Prompt: `Read <abs path of brief.md> and follow it exactly. Write findings only under <agent dir>/findings/.`
- **codex** testers: Bash background: `python3 $S/scripts/run_agent.py --id A4 --family codex --model "gpt-6-luna,gpt-5.6-luna,gpt-reserve" --brief .uat/agents/A4/brief.md --root .` (sandboxed `workspace-write` in the agent dir).
- **opencode** testers: same script with `--family opencode --model "<free model list, comma separated>"`. The script falls back through the list on failure.
Before the wave: `restore baseline` (single shared env; testers prefix their data with their agent id). Wait for completion notifications; do not poll or sleep. A runner exit code 2 means workspace tamper (auto-reverted): discard that agent's findings and note it.
Respect `stop.agent_timeout_s` and the wall budget `stop.budget_wall_s`.

## 4. After every wave: judge by script, not by model
```
python3 $S/scripts/validate_findings.py --root .
python3 $S/scripts/replay.py --root .          # 3x per finding, shared env restored between runs
python3 $S/scripts/dedupe.py --root .
```
Compute new clusters (fingerprints not seen before) with verdict `accept` or `needs_verifier`. Append a line to `.uat/rounds.jsonl`: round, agents run, findings, new clusters, wall seconds.

## 5. Adapt
- **Yield-driven expansion**: for areas whose clusters are accepted, add follow-up charters (neighbouring rules, other lens) before untouched low-priority areas. Hot areas get extra testers from a different family than the first finder.
- **Stop** when any holds: `stop.saturation_rounds` consecutive waves add no new cluster; all non-deferred charters done; `max_rounds` reached; wall budget used; `max_agents` spent. Unused agents are simply not run.
- **Model health**: a model that fails twice (rc != 0 or zero valid findings) is rotated out for the run; say so. A family with no working model is dropped.
- Do not spend your own tokens analysing findings here; the report stage does that.

## 6. Finish
Set phase `executed`. Print: rounds, agents run per family/model, wall time, clusters by verdict, any dropped models or tamper events. Next step: `/uat-feedback`.
