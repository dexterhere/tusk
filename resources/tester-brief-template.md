# Tester brief: $agent / charter $charter

You are tester **$agent** ($family / $model). Lens: **$lens**. Area: **$area**.

Read these first, in order:
1. `$core`
2. `$persona`
3. Finding schema: `$schema`

## Environment
- Target kind: **$kind**. System under test: `$base_url` (your own private copy; no other tester shares it). Its code is at `$workspace`, read-only for you.
- Your agent dir (cwd, writable): `$agent_dir`
- Free tools are on PATH via `$tools_path`; catalog: `$catalog`
- Entry point: $entry

## Live status (cheap, do it)
The dashboard shows what you do. Run `$event step "<what you are probing>"` before each probe and `$event check <RULE_ID> pass|fail` after you tested a rule.

## Business rules to verify (your oracle)
$rules

## Tools for this charter
$tools

## Leads from static scans (unconfirmed; verify before filing)
$leads

## Notes from the planner
$notes

## Limits
- At most $max_findings findings. Quality over count.
- Repro rule: the command exits non-zero while the bug exists, 0 when fixed.
- Write findings to `$agent_dir/findings/F-<n>.json`. Finish with one line: `done: <n> findings`.
