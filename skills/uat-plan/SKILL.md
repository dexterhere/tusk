---
name: uat-plan
description: Stage 2 of UAT Swarm. Builds a knowledge graph, extracts the system's business rules with sources (the test oracle), runs free static/coverage pre-scans for leads, and produces an approved test plan of charters. Use after /uat-init.
disable-model-invocation: true
---

# /uat-plan: business logic -> rules ledger -> charters

`S=${UAT_HOME:-$HOME/Work/uat}`. Requires `.uat/state.json` phase `initialized`. Read-only on source. Caveman chat; files normal prose.
The hard part of testing is knowing what "correct" means. Every charter must point at rules with sources, or testers will file false bugs.

## 1. Orient
1. Build the graph on the repo root with the `graphify` skill (`/graphify .`) unless `graphify-out/` is fresh. Use it for areas, entry points and call paths.
2. Use `jg "<question>" .` for file paths and business-flow lookups (checkout, billing, permissions...). Never feed it secrets.
3. Read `.uat/detect.json` areas. Pick the areas in scope (from init answers).

## 2. Pre-scans: free tools produce leads, not findings
Run only enabled and installed tools, scoped to `.uat/workspace`:
- Static: semgrep, gitleaks, trivy (docker). Save raw output to `.uat/plan/leads/<tool>.json`.
- Coverage (if a test suite exists): coverage.py / c8 / cargo-llvm-cov. Untested modules become charter priority boosts.
Summarise the top leads per area (max 5 each). Static leads are hypotheses; testers must prove them at runtime.

## 3. Extract business rules (planners)
For each in-scope area spawn one `uat-planner` subagent (Agent tool, model `haiku`; use `sonnet` for areas touching money, auth or permissions). Prompt: the area's paths from `jg`/graphify, the persona file `$S/agents/planner.md`, output path `.uat/plan/rules-<area>.json`. Run up to `max_parallel` at once.
Merge into `.uat/plan/rules.json`. Then:
- **Low-confidence or conflicting rules go to the user**, in AskUserQuestion batches of up to 4 ("Is it correct that X? Source: ..."). Record answers; confirmed rules become `high`, rejected ones are dropped.
- Cap: highest-impact 25 rules per area.

## 4. Write the plan
Create charters in `.uat/plan/plan.json`:
```json
{"rules":[...merged rules...],
 "charters":[{"id":"CH-01","area":"checkout","lens":"journey","rule_ids":["R-..."],"entry":"/cart","tools":["playwright","hurl"],"leads":["semgrep: sql string at db.py:88"],"priority":1,"notes":""}]}
```
Guidelines:
- One charter = one area x one lens, with 3-8 rules. Lenses: journey, breaker, resilience.
- Lens tools: journey -> playwright, hurl. breaker -> schemathesis (if openapi), hypothesis/fast-check, zap baseline. resilience -> toxiproxy, wiremock/mockoon, k6, libfaketime, faker. Add axe/pa11y/lighthouse to journey charters of UI areas.
- Priority: money/auth/data-integrity first, then high-churn or untested code, then the rest.
- Plus one `tool` lane charter `CH-T1` covering the dynamic tools (schemathesis, ZAP, axe, pa11y, lighthouse, k6) via the `uat-tool-runner` persona.
- Never more charters than `swarm.max_agents`; if more exist, mark the extras `deferred`.

Write `.uat/plan/plan.md`: scope, rules table (id, statement, source, confidence), charters with assignments (agent id -> family -> model), tool list, expected cost (agent count x cheap model), stop rules. Plain English, no hype.

## 5. Approve
Show the user the plan path and a 10-line summary. AskUserQuestion (one question): approve and run / edit (they tell you what) / cancel. Only on approval set `.uat/state.json` phase `planned`. Next step: `/uat-execute`.
