---
name: uat
description: UAT Swarm entry point. Shows where a project stands, starts the live dashboard on localhost, and routes to the right stage (init, plan, execute, feedback, fix, status). Use when the user says /uat, "run UAT", or asks what to do next in a UAT run.
disable-model-invocation: true
---

# /uat [dashboard | next | <stage>]

`S=${UAT_HOME:-$HOME/Work/uat}`. Caveman chat. This is the front door; the work lives in the stage skills.

1. Read `.uat/state.json` (missing = never initialised) and print a 3-line status: phase, target (this computer or ssh host), counts from `.uat/clusters.json` and `.uat/fixqueue.json`.
2. **Dashboard** (always offer, start on `dashboard` or after init): `python3 $S/scripts/dashboard.py --root . &` (background), then tell the user `http://127.0.0.1:7777`. It shows each agent as a pet with a label popup, who orchestrates, what each is doing, a results graph, findings, and the fix queue where the user approves fixes. Re-use a running one (`curl -s 127.0.0.1:7777/api/state`).
3. **Route** by phase: none -> `/uat-init`; initialized -> `/uat-plan`; planned -> `/uat-execute`; executed -> `/uat-feedback`; reported -> review the fix queue in the dashboard, then `/uat-fix`; fixed -> offer a second `/uat-execute` on touched areas.
Ask before starting a stage; never chain stages silently.
