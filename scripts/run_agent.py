#!/usr/bin/env python3
"""Run one tester agent headlessly (codex / opencode / claude fallback), with model fallback,
timeout, secret-stripped env, tamper check on the UAT workspace, and a ledger line.

  run_agent.py --id A4 --family codex --model gpt-6-luna,gpt-5.6-luna --brief .uat/agents/A4/brief.md
  run_agent.py --ping --family opencode --model opencode/glm-5.3-flash-free
Claude testers normally run through the Agent tool (model: haiku); family=claude here is a headless fallback.
"""
import argparse
import json
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from uatlib import config, udir  # noqa: E402

DROP = re.compile(r"SECRET|PASSWORD|TOKEN|AWS_|PRIVATE", re.I)


def clean_env(cfg, root):
    env = {k: v for k, v in os.environ.items() if not DROP.search(k)}
    env["UAT_BASE_URL"] = cfg["env"]["base_url"]
    env["UAT_WS"] = str(udir(root) / "workspace")
    tools = ":".join(str(udir(root) / "tools" / s) for s in ("venv/bin", "node_modules/.bin", "bin"))
    env["PATH"] = tools + ":" + env.get("PATH", "")
    return env


def build(family, model, agent_dir, prompt):
    if family == "codex":
        return ["codex", "exec", "-m", model, "-s", "workspace-write", "-C", str(agent_dir), "--skip-git-repo-check", "--ephemeral", "--json", "-o", str(agent_dir / "last.txt"), prompt]
    if family == "opencode":
        return ["opencode", "run", "-m", model, "--format", "json", "--auto", prompt]
    if family == "claude":
        return ["claude", "-p", prompt, "--model", model, "--permission-mode", "acceptEdits", "--allowedTools", "Bash Read Write Edit Glob Grep"]
    raise SystemExit(f"unknown family {family}")


def ws_status(root, cfg):
    ws = udir(root) / "workspace"
    if not (ws / ".git").exists():
        return ""
    spec = [f":(exclude){p}" for p in cfg["env"]["state_paths"]]
    p = subprocess.run(["git", "-C", str(ws), "status", "--porcelain", "--", ".", *spec], capture_output=True, text=True)
    return p.stdout


def run(cmd, cwd, env, timeout, log):
    with open(log, "wb") as lf:
        p = subprocess.Popen(cmd, cwd=cwd, env=env, stdout=lf, stderr=subprocess.STDOUT, start_new_session=True)
        try:
            return p.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(p.pid, signal.SIGKILL)
            return 124


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--id", default="ping")
    ap.add_argument("--family", required=True)
    ap.add_argument("--model", required=True, help="comma list = fallback order")
    ap.add_argument("--brief")
    ap.add_argument("--root", default=os.getcwd())
    ap.add_argument("--timeout", type=int)
    ap.add_argument("--ping", action="store_true")
    ap.add_argument("--cmd", help="test hook: run this shell-free command line instead of the CLI")
    a = ap.parse_args()
    root = Path(a.root).resolve()
    cfg = config(root)
    d = udir(root) / "agents" / a.id
    (d / "findings").mkdir(parents=True, exist_ok=True)
    timeout = a.timeout or (90 if a.ping else cfg["stop"]["agent_timeout_s"])
    prompt = "Reply with exactly: OK" if a.ping else f"Read {a.brief} and follow it exactly. Write findings only under ./findings/."
    before = ws_status(root, cfg)
    env = clean_env(cfg, root)
    rc, used, t0 = 1, None, time.time()
    for model in a.model.split(","):
        cmd = a.cmd.split() if a.cmd else build(a.family, model, d, prompt)
        rc = run(cmd, d, env, timeout, d / "agent.log")
        used = model
        out = (d / "agent.log").read_text(errors="ignore")
        if rc == 0 and (not a.ping or "OK" in out):
            break
    secs = round(time.time() - t0, 1)
    after = ws_status(root, cfg)
    tamper = after != before
    if tamper:
        ws = udir(root) / "workspace"
        subprocess.run(["git", "-C", str(ws), "checkout", "--", "."], capture_output=True)
        subprocess.run(["git", "-C", str(ws), "clean", "-fdq"], capture_output=True)
    n = len(list((d / "findings").glob("F-*.json")))
    row = {"ts": int(time.time()), "id": a.id, "family": a.family, "model": used, "rc": rc, "secs": secs, "log_bytes": (d / "agent.log").stat().st_size, "findings": n, "tamper": tamper, "ping": a.ping}
    with open(udir(root) / "ledger.jsonl", "a") as f:
        f.write(json.dumps(row) + "\n")
    print(json.dumps(row))
    sys.exit(0 if rc == 0 and not tamper else 2 if tamper else 1)


if __name__ == "__main__":
    main()
