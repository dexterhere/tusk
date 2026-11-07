# Security policy

## Reporting a vulnerability

Do not open a public issue. Use the repository's **private vulnerability reporting** (Security tab, "Report a vulnerability"). Include the version or commit, what you did, what you expected and what happened. Never include real secrets or customer data.

You can expect an acknowledgement within 7 days and a fix or a written assessment within 30 days. Credit is given unless you prefer otherwise.

## What is in scope

- Escaping the tester sandbox, or a tester agent changing files outside its own agent directory.
- The dashboard accepting a request it should refuse (wrong Host or Origin, missing token), executing anything, or writing anything except a team proposal.
- Prompt injection that makes the orchestrator act on agent or dashboard text (approve or start a fix, change the team, push, send secrets).
- Tampering with the audit log, fix queue or knowledge graph without detection, within the limits below.
- Secrets reaching agents, logs, the report or git.

## Known limits (not vulnerabilities)

- The audit log is a hash chain with an off-project anchor, not a signature. Someone who runs as your OS user can rewrite both.
- Sandbox network access is open by design (testers must reach the app under test).
- Agent harnesses other than `claude`, `codex` and `opencode` are unverified presets.
- The dashboard is for 127.0.0.1 only. Exposing it on a network is unsupported.

## Design rules we hold to

Loopback only with Host and Origin checks; one write path (team proposals) behind a per-process token; agent-written text is cleaned on the server and escaped in the page; nothing is fixed, pushed, merged or deployed without the user's yes in chat; no telemetry.
