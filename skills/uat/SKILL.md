---
name: uat
description: UAT Swarm entry point. Shows where a project stands, starts the live dashboard on localhost, and routes to the right stage (init, plan, execute, feedback, fix, status). Use when the user says /uat, "run UAT", or asks what to do next in a UAT run.
disable-model-invocation: true
---

# /uat [dashboard | next | <stage>]

`S=${UAT_HOME:-$HOME/Work/uat}`. Caveman chat. This is the front door; the work lives in the stage skills.

1. Read `.uat/state.json` (missing = never initialised) and print a 3-line status: phase, target (this computer or ssh host), counts from `.uat/clusters.json` and `.uat/fixqueue.json`.
2. **Dashboard** (always offer, start on `dashboard` or after init): `python3 $S/scripts/dashboard.py --root . &` (background), then tell the user `http://127.0.0.1:7777`. It is read-only: agents with label popups, who orchestrates, which tests ran, findings, the fix cycle (found, approved, fixing, fixed, verified, regressed), the audit log, the report and the knowledge graph. It has no buttons that change anything; approvals happen in chat. Re-use a running one (`curl -s 127.0.0.1:7777/api/state`).
3. **Route** by phase: none -> `/uat-init`; initialized -> `/uat-plan`; planned -> `/uat-execute`; executed -> `/uat-feedback`; reported -> ask the user which fixes may start, then `/uat-fix`; fixed -> offer a second `/uat-execute` on touched areas.
Ask before starting a stage; never chain stages silently. Exception: `/uat-full` is the user's explicit request to chain them; it still stops before any fix.
If the user wants one command for everything, offer `/uat-full`. The dashboard's Team page can propose a team change; apply it with `/uat-full apply`.
