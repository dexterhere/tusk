# Install and set up Tusk

Tusk runs from a plain checkout. There is nothing to compile and no Python package to install.

## 1. Requirements

| Need | Why | Check |
|---|---|---|
| Linux | The sandbox uses bubblewrap | `uname -s` |
| `python3` (standard library only) | All scripts and the dashboard. Developed and tested on 3.14; older 3.x is untested | `python3 --version` |
| `git` | Baselines, tamper detection, fix branches | `git --version` |
| `bwrap` (bubblewrap) | Sandbox for every command an agent writes | `bwrap --version` |
| One agent CLI, logged in | The testers. `claude`, `codex` or `opencode` are verified; see the README for others | `claude --version` |
| [Claude Code](https://claude.com/claude-code) | Runs the `/tusk-*` slash commands (the scripts also run without it) | `claude --version` |
| Remote host (optional) | `ssh`, `rsync`, `python3`, `bwrap` on it, key auth only | `ssh -o BatchMode=yes <host> true` |

Install bubblewrap: `sudo pacman -S bubblewrap` (Arch/Omarchy) or `sudo apt install bubblewrap` (Debian/Ubuntu). Without it Tusk refuses to run agent commands unless you explicitly turn the sandbox off.

Disk and memory: about 10 GB free and 4 GB RAM on the machine that runs the tests (checked for you by `python3 scripts/hostinfo.py`).

## 2. Install (pick one)

```sh
curl -fsSL https://raw.githubusercontent.com/dexterhere/tusk/main/install.sh | sh     # clones to ~/.local/share/tusk
git clone https://github.com/dexterhere/tusk.git ~/.local/share/tusk && ~/.local/share/tusk/bin/tusk setup
```

`tusk doctor` checks the requirements above and prints the exact fix for anything missing.

## 3. What install does

`tusk install` creates symlinks only: `~/.tusk` pointing at the checkout, `~/.local/bin/tusk` for the command, and `~/.claude/skills/tusk*` pointing at its `skills/`. It never overwrites a real file or folder (it says SKIPPED) and `tusk uninstall` removes only links that point at this checkout. Restart Claude Code and type `/tusk` to confirm. To use another location, set `TUSK_HOME=/path` (skills default to `~/.tusk`).

## 4. Check it works (no agent, no network)

```sh
TUSK_STATE_DIR=$(mktemp -d) python3 -m unittest discover -s tests    # about 2 minutes, all green
python3 scripts/harness.py list                                      # which agent programs are installed
```

## 5. Run it on your project

1. `cd` into the project you want to test and start Claude Code there.
2. Run **`/tusk-full`**.
3. Answer the questions:
   - **Where to run:** this computer, or an ssh host.
   - **Team:** one orchestrator and at least one worker. They can be the same model or different ones. Each model is pinged before use.
   - **Scope, size, tools, budget, how to start your app** (command, health URL, test accounts). Use fake accounts and fake data only.
4. Open the dashboard it prints (default `http://127.0.0.1:7777`).
5. When testing ends, read the report and say which fixes may start. Nothing is fixed before you say yes.

Everything Tusk creates lives in `<your project>/.tusk/` and is git-ignored. Your source is never edited by the testers.

Prefer step by step: `/tusk-init`, `/tusk-plan`, `/tusk-execute`, `/tusk-feedback`, `/tusk-fix`, with `/tusk` showing where you are.

## 6. Dashboard and team changes

- Start or restart: `tusk dashboard` (add `--port N`). It listens on 127.0.0.1 only.
- Change the team: on the **Team** page choose from the listed programs and models and save a *proposal*. You need the edit token: `tusk token`. Then tell the orchestrator `/tusk-full apply`. The page cannot change anything by itself.
- The tab icon, theme and Figtree font are served locally; the page makes no outside requests.

## 7. Settings you may want

Put overrides in `<project>/.tusk/tusk.json` (they merge over `tusk.defaults.json`):

| Setting | Meaning |
|---|---|
| `swarm.max_agents`, `swarm.max_parallel` | Total testers, and how many run at once (one isolated lane each) |
| `models.<program>.cheap / mid` | Models offered for testers and for verifiers/fixers |
| `env.*` | How to start your app, ports, state files to restore between runs |
| `stop.*` | Wall-time budget, rounds, per-agent timeout |
| `harness.<name>` | Add any other agent CLI (see `scripts/harness.py`) |
| `target.mode` | `local` or `ssh` |

## 8. Troubleshooting

| Symptom | Fix |
|---|---|
| "bwrap" missing or sandbox cannot start | Install bubblewrap; on hardened kernels allow unprivileged user namespaces |
| An agent shows "not logged in" | Log in to that CLI on the machine that runs it (on the ssh host too) |
| A model "did not answer the test question" | Pick another model; presets other than claude, codex and opencode are unverified |
| "out of quota" | Wait for the reset or add another model to the team |
| Audit log "cannot be proven complete" | Normal after wiping `.tusk/` on purpose: `python3 scripts/audit.py reset` |
| Slash commands missing | Run `tusk install`, then restart Claude Code |
| `tusk: command not found` | `tusk install` links it into `~/.local/bin`; add that folder to your `PATH` |
| Tab icon looks old | Hard-refresh (Ctrl+Shift+R) after restarting the dashboard |

## 9. Upgrading from the old `uat` names

Earlier builds called everything `uat`. After `tusk update` (or a fresh install) rename by hand what lives outside the checkout:

```sh
mv <project>/.uat <project>/.tusk                      # per project; and in .gitignore / .git/info/exclude
mv <project>/.tusk/uat.json <project>/.tusk/tusk.json  # if you had overrides
mkdir -p ~/.local/state/tusk && cp -n ~/.local/state/uat/* ~/.local/state/tusk/   # audit anchors
```

`tusk install` removes the old `uat*` command links itself. Environment variables `UAT_*` are now `TUSK_*`.

## 10. Uninstall

Run `tusk uninstall`, delete the checkout, and delete `.tusk/` inside each project. Audit anchors live in `~/.local/state/tusk/`.
