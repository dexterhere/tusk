#!/usr/bin/env python3
"""Replay every finding's repro N times. The script, not a model, is the judge.
repro.cmd asserts CORRECT behaviour: non-zero exit = bug present.

  status: confirmed (all runs fail) | flaky (some) | not_reproducible (none) | error (timeout/crash)
Writes <finding>.replay.json next to each finding.

  replay.py [--root D] [--runs N] [--only CLUSTER_ID] [--passing]   # --passing: expect all pass (fix check)
"""
import argparse
import os
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from uatlib import config, finding_files, jdump, jload, udir  # noqa: E402


def replay_one(f, cfg, root, runs, restore):
    obj = jload(f)
    agent_dir = f.parent.parent
    # agent-written commands: never hand them the user's secrets
    env = {k: v for k, v in os.environ.items() if not re.search(r"SECRET|PASSWORD|TOKEN|AWS_|PRIVATE", k, re.I)}
    env.update({"UAT_BASE_URL": cfg["env"]["base_url"], "UAT_WS": str(udir(root) / "workspace"), "UAT_AGENT_DIR": str(agent_dir)})
    env["PATH"] = ":".join(str(udir(root) / "tools" / s) for s in ("venv/bin", "node_modules/.bin", "bin")) + ":" + env["PATH"]
    results = []
    for _ in range(runs):
        if restore:
            subprocess.run([sys.executable, str(Path(__file__).parent / "uat_env.py"), "restore", "baseline", "--root", str(root)], capture_output=True)
        t0 = time.time()
        try:
            # repro.cmd is a shell command line written by the tester agent; it runs inside the sandboxed UAT env only.
            p = subprocess.run(obj["repro"]["cmd"], shell=True, cwd=agent_dir, env=env, capture_output=True, text=True, timeout=cfg["replay"]["timeout_s"])
            results.append({"rc": p.returncode, "secs": round(time.time() - t0, 1), "tail": (p.stdout + p.stderr)[-400:]})
        except subprocess.TimeoutExpired:
            results.append({"rc": None, "secs": cfg["replay"]["timeout_s"], "tail": "timeout"})
    fails = sum(1 for x in results if x["rc"] not in (0, None))
    errors = sum(1 for x in results if x["rc"] is None or x["rc"] in (126, 127))
    if errors == len(results):
        status = "error"
    elif fails == len(results):
        status = "confirmed"
    elif fails == 0:
        status = "not_reproducible"
    else:
        status = "flaky"
    jdump(f.with_suffix(".replay.json"), {"status": status, "runs": results})
    return status


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=os.getcwd())
    ap.add_argument("--runs", type=int)
    ap.add_argument("--only", help="cluster id from .uat/clusters.json")
    ap.add_argument("--passing", action="store_true", help="fix check: pass if every run exits 0")
    a = ap.parse_args()
    r = Path(a.root).resolve()
    cfg = config(r)
    runs = a.runs or cfg["replay"]["runs"]
    files = finding_files(r)
    if a.only:
        ids = next((c["members"] for c in jload(udir(r) / "clusters.json", []) if c["cid"] == a.only), [])
        files = [f for f in files if jload(f, {}).get("id") in ids]
    counts = {}
    for f in files:
        s = replay_one(f, cfg, r, runs, cfg["replay"]["restore_between"])
        counts[s] = counts.get(s, 0) + 1
        print(f"{f.parent.parent.name}/{f.stem}: {s}")
    print(counts)
    if a.passing:
        sys.exit(0 if set(counts) <= {"not_reproducible"} else 1)


if __name__ == "__main__":
    main()
