---
name: tusk-status
description: Tusk housekeeping. Shows phase, env health, agents, rounds, token/time ledger and disk use; resumes an interrupted run; tears the Tusk env down. Use any time after /tusk-init, or with "down" to clean up.
disable-model-invocation: true
---

# /tusk-status [down | resume | clean]

`S=${TUSK_HOME:-$HOME/.tusk}`. Caveman chat.

## Default: show status
1. `cat .tusk/state.json`; `python3 $S/scripts/tusk.py tusk_env lanes status --root .`
2. Ledger: `jq -s 'group_by(.family+" "+.model)|map({who:.[0].family+" "+.[0].model,runs:length,secs:(map(.secs)|add),findings:(map(.findings)|add),fails:(map(select(.rc!=0))|length),tamper:(map(select(.tamper))|length)})' .tusk/ledger.jsonl`
3. Rounds: `.tusk/rounds.jsonl` last 5 lines. Clusters: counts by verdict from `.tusk/clusters.json`.
4. Knowledge: `python3 $S/scripts/kb.py digest --root .` (issues by status, hot files, what to re-verify).
5. Disk: `du -sh .tusk .tusk/tools .tusk/agents`; also `docker system df` if docker tools were used.
Print a short table and the recommended next command for the current phase (init -> plan -> execute -> feedback -> fix).

## resume
Read phase. If `executing`: report which charters have an `agents/<id>/findings` dir and which are missing, re-run `tusk_env.py status`/`up`, then continue `/tusk-execute` from the first charter without findings. Do not redo finished agents.

## down
Stop all lanes (`tusk.py tusk_env lanes down --root .`) and the dashboard (`pkill -f "python3 .*scripts/[d]ashboard.py"`) and any tool containers started for Tusk (`docker ps` filtered by the images in the catalog; ask before stopping anything you did not start). Keep reports and regression repros.

## clean
Ask first (AskUserQuestion). On yes: `down`, then delete `.tusk/workspace`, `.tusk/lanes`, `.tusk/logs`, `.tusk/fix`. Keep `.tusk/kb` (the knowledge graph is the memory between runs), `.tusk/report`, `.tusk/regression`, `.tusk/fixqueue.json`, `.tusk/tusk.json`, `.tusk/baseline.json`. Remove `.tusk/tools` only if the user asks (reinstall costs time and disk).
