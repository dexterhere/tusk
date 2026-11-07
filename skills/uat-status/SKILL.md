---
name: uat-status
description: UAT Swarm housekeeping. Shows phase, env health, agents, rounds, token/time ledger and disk use; resumes an interrupted run; tears the UAT env down. Use any time after /uat-init, or with "down" to clean up.
disable-model-invocation: true
---

# /uat-status [down | resume | clean]

`S=${UAT_HOME:-$HOME/Work/uat}`. Caveman chat.

## Default: show status
1. `cat .uat/state.json`; `python3 $S/scripts/uat.py uat_env lanes status --root .`
2. Ledger: `jq -s 'group_by(.family+" "+.model)|map({who:.[0].family+" "+.[0].model,runs:length,secs:(map(.secs)|add),findings:(map(.findings)|add),fails:(map(select(.rc!=0))|length),tamper:(map(select(.tamper))|length)})' .uat/ledger.jsonl`
3. Rounds: `.uat/rounds.jsonl` last 5 lines. Clusters: counts by verdict from `.uat/clusters.json`.
4. Disk: `du -sh .uat .uat/tools .uat/agents`; also `docker system df` if docker tools were used.
Print a short table and the recommended next command for the current phase (init -> plan -> execute -> feedback -> fix).

## resume
Read phase. If `executing`: report which charters have an `agents/<id>/findings` dir and which are missing, re-run `uat_env.py status`/`up`, then continue `/uat-execute` from the first charter without findings. Do not redo finished agents.

## down
Stop all lanes (`uat.py uat_env lanes down --root .`) and the dashboard (`pkill -f scripts/dashboard.py`) and any tool containers started for UAT (`docker ps` filtered by the images in the catalog; ask before stopping anything you did not start). Keep reports and regression repros.

## clean
Ask first (AskUserQuestion). On yes: `down`, then delete `.uat/workspace`, `.uat/lanes`, `.uat/logs`, `.uat/fix`. Keep `.uat/report`, `.uat/regression`, `.uat/fixqueue.json`, `.uat/uat.json`, `.uat/baseline.json`. Remove `.uat/tools` only if the user asks (reinstall costs time and disk).
