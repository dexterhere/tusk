# Calibration: seeded-bug demo app

Run 2026-11-07 on this machine (local target, bubblewrap sandbox, 3 lanes, 2 waves, `scripts/swarm.py`).
App: `tests/demo_app` (5 seeded bugs, 1 decoy rule that is not a bug). One charter, all 6 rules, black-box + read-only source.

| family | model | recall | precision | wall |
|---|---|---|---|---|
| claude | haiku | 5/5 | 5/5 | 92 s |
| opencode | opencode/deepseek-v4.1-flash-free | 5/5 | 5/5 | 260 s |
| opencode | opencode/glm-5.3-flash-free | 5/5 | 6/6 | 360 s |
| codex | gpt-6-luna | 2/5 | 2/2 | 223 s |

Pipeline: 18 findings, all replay-confirmed, merged into exactly 5 clusters (no missed duplicates, no over-merge), decoy not reported.
Takeaway: with a narrow charter the cheap models reach full recall on a small app; `gpt-6-luna` under-explores, so give it more than one pass
or a second lens before trusting a clean result from it. Single run, one app: treat as a smoke calibration, not a benchmark.

## Model names verified by ping (answered OK)

claude: haiku, sonnet. codex: gpt-6-luna, gpt-5.6-luna, gpt-6.1-terra, gpt-reserve (the CLI does not echo which model served it, so
"answered" is the only thing verified). opencode: opencode/deepseek-v4.1-flash-free, opencode/glm-5.3-flash-free (4 s),
opencode/mimo-v2.6-flash-free (48 s, slow), opencode/nemotron-3.5-lightning-free, openai/gpt-5.6-luna.
