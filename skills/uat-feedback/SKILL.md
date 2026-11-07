---
name: uat-feedback
description: Stage 4 of UAT Swarm. Collects all agent findings, replays and clusters them, has a fresh verifier judge only the disputed ones, then writes the fix-ready report (report.md, findings.json, junit.xml) and pins regression tests. Use after /uat-execute.
disable-model-invocation: true
---

# /uat-feedback: one precise report for fix agents

`S=${UAT_HOME:-$HOME/Work/uat}`. Caveman chat; the report is normal prose.

## 1. Make sure the data is current
```
python3 $S/scripts/validate_findings.py --root .
python3 $S/scripts/uat.py replay --root .  # deterministic; stress runs for races
python3 $S/scripts/dedupe.py --root .
```
Read `.uat/clusters.json`. Verdicts: `accept` (replay confirmed), `needs_verifier` (flaky, error, or 2+ agents saw it but replay fails), `watch` (single-agent concurrency report that did not replay: kept as unconfirmed, listed in the report, not queued for fix), `drop` (single agent, not reproducible, not a race; or tamper), `unreplayed` (rerun replay). Each cluster shows `merge_reasons`; check any cluster that merged on `similar title` alone.

## 2. Verify only the disputed clusters
For each `needs_verifier` cluster spawn one fresh `uat-verifier` (Agent tool, model `sonnet`; or Codex `gpt-6.1-terra` via `codex exec` if the user prefers a second family). Prompt: persona file `$S/agents/verifier.md`, the cluster id and its evidence `digest` (from `clusters.json`), paths of its finding and replay files, the rule text. Record the hand-off first: `uat_event.py emit orchestrator assign to=V<n> kind=verify ref=<cid> family=<f> model=<m> note="<why it is disputed>" --root .`. The verifier records its verdict with `verdict.py <cid> accept|reject "note" --by V<n> --family <f> --model <m> --digest <digest>` (not by editing files; it refuses if the evidence changed after the verifier started), so the dashboard shows who decided and with which model. Run up to `max_parallel` at once. If the app needs a restart or restore, do it between verifiers (shared env).
Never override a script `accept` with a model opinion. A model may only decide the disputed ones. A verifier may accept a flaky cluster as a real race (`note: flaky: <cause>`); the report then labels it INTERMITTENT with its measured fail rate.

## 3. Optional quality gate (free tools, slow)
If mutation tools are installed and the user wants it: run mutmut / stryker / cargo-mutants scoped to modules the accepted findings touch, to show whether the project's own tests would catch such bugs. Add a one-line result per module to the report notes. Skip by default.

## 4. Build the report
`python3 $S/scripts/report.py --root . --save-baseline`
Also runs `fixq.py sync` and `kb.py ingest` (merges the run into the knowledge graph `.uat/kb/`): every accepted result (defect or suboptimal) enters `.uat/fixqueue.json` as `proposed`. Produces `.uat/report/report.md` (human), `findings.json` (machine, one entry per cluster with expected/actual, repro, evidence, suspect location, confidence, acceptance command), `junit.xml` (CI), and pins each repro under `.uat/regression/<cid>/`. The baseline file lets the next run say new / still-open / fixed.

## 5. Add suspect locations the testers missed
For accepted clusters with no `suspect`, use `jg "where is <rule/behaviour> implemented" .` and the graphify graph to propose `file:line`. Write it into that cluster's `findings.json` entry (`suspect`). Mark it `"suspect_source":"elephant"` so fix agents know it is a lead. Max 10 clusters; stop at the first plausible location each. Then tell the graph: `python3 $S/scripts/kb.py link <cid> --files <path>...`. Testers are blind to code, so this link is what lets `kb.py impact` find other issues in the same code. Run `kb.py digest` and show the user its Open and Re-verify sections.

## 6. Present
Show the user: health score, counts by severity, top 5 findings (title, severity, confidence), rejected/dropped count, and the report path. The dashboard is read-only. Ask the user in chat which proposed fixes may start (list them with severity and `kb.py impact --cid <cid>` risk); on a yes run `fixq.py approve <cid>...` yourself and record who said so. Then `/uat-fix`. Set phase `reported`.
