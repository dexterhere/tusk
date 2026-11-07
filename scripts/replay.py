#!/usr/bin/env python3
"""Replay every finding's repro N times, inside a lane and the same sandbox as the testers. The script, not a model, judges.
repro.cmd asserts CORRECT behaviour: non-zero exit = bug present.

  status: confirmed (all runs fail) | flaky (some fail; fail_rate recorded) | not_reproducible (none) | error (timeout/crash) | tamper
Concurrency-type findings get `replay.stress_runs` runs instead of `replay.runs`, so a real but rare race shows up as flaky, not as nothing.
Findings replay in parallel, one lane each. Writes .uat/replays/<agent>/<finding>.replay.json (outside the agent-writable dir, so testers cannot forge verdicts).

  replay.py [--root D] [--runs N] [--only CLUSTER_ID] [--passing] [--lane L1] [--keep-code]
  --passing    fix check: pass only if every finding now exits 0 on every run (use --keep-code after `uat_env lane sync`)
"""
import argparse
import concurrent.futures as cf
import os
import queue
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sandbox  # noqa: E402
from uat_env import Lane  # noqa: E402
from uatlib import HOME, config, emit, finding_files, jdump, jload, lane_names, replay_path, udir  # noqa: E402


def run_count(obj, cfg, runs):
    stress = obj.get("category") == "concurrency" or obj.get("nondeterministic")
    return max(runs, cfg["replay"]["stress_runs"]) if stress else runs


def replay_one(f, cfg, root, lane, runs, keep_code):
    obj = jload(f)
    agent_dir = f.parent.parent
    n = run_count(obj, cfg, runs)
    net = "none" if cfg["env"]["kind"] == "cli" else cfg["sandbox"]["network"]["replay"]
    env = sandbox.clean_env(cfg, {"UAT_BASE_URL": lane.base_url, "UAT_WS": str(lane.ws), "UAT_AGENT_DIR": str(agent_dir), "UAT_LANE": lane.name, "HOME": str(Path.home())},
                            [udir(root) / "tools" / s for s in ("venv/bin", "node_modules/.bin", "bin")])
    results = []
    for i in range(n):
        if i or not keep_code:
            lane.reset(state_only=bool(i or keep_code))
        cmd = sandbox.wrap(cfg, ["bash", "-c", obj["repro"]["cmd"]], cwd=agent_dir, ws=lane.ws, tools=udir(root) / "tools", toolkit=HOME, net=net,
                           writable=[lane.ws / p for p in lane.tracked()])
        t0 = time.time()
        try:
            p = subprocess.run(cmd, cwd=agent_dir, env=env, capture_output=True, text=True, timeout=cfg["replay"]["timeout_s"])
            results.append({"rc": p.returncode, "secs": round(time.time() - t0, 1), "tail": (p.stdout + p.stderr)[-400:]})
        except subprocess.TimeoutExpired:
            results.append({"rc": None, "secs": cfg["replay"]["timeout_s"], "tail": "timeout"})
    fails = sum(1 for x in results if x["rc"] not in (0, None))
    errors = sum(1 for x in results if x["rc"] is None or x["rc"] in (126, 127))
    if lane.tamper():
        status = "tamper"
    elif errors == len(results):
        status = "error"
    elif fails == len(results):
        status = "confirmed"
    elif fails == 0:
        status = "not_reproducible"
    else:
        status = "flaky"
    jdump(replay_path(root, f), {"status": status, "fail_rate": round(fails / len(results), 3), "lane": lane.name, "runs": results})
    emit(root, "orchestrator", "replay", finding=obj["id"], agent=obj["agent"], status=status, fail_rate=round(fails / len(results), 3))
    return status


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=os.getcwd())
    ap.add_argument("--runs", type=int)
    ap.add_argument("--only", help="cluster id from .uat/clusters.json")
    ap.add_argument("--passing", action="store_true")
    ap.add_argument("--lane", help="use this lane only (default: all lanes in parallel)")
    ap.add_argument("--keep-code", action="store_true", help="do not reset the lane's code first (fix proof)")
    a = ap.parse_args()
    r = Path(a.root).resolve()
    cfg = config(r)
    runs = a.runs or cfg["replay"]["runs"]
    files = finding_files(r)
    if a.only:
        ids = next((c["members"] for c in jload(udir(r) / "clusters.json", []) if c["cid"] == a.only), [])
        files = [f for f in files if jload(f, {}).get("id") in ids]
    names = [a.lane] if a.lane else lane_names(cfg)
    pool = queue.Queue()
    for n in names:
        pool.put(Lane(r, cfg, n))

    def job(f):
        lane = pool.get()
        try:
            return f, replay_one(f, cfg, r, lane, runs, a.keep_code)
        finally:
            pool.put(lane)

    counts = {}
    with cf.ThreadPoolExecutor(len(names) if not cfg["replay"]["workers"] else cfg["replay"]["workers"]) as ex:
        for f, s in ex.map(job, files):
            counts[s] = counts.get(s, 0) + 1
            print(f"{f.parent.parent.name}/{f.stem}: {s}")
    print(counts)
    if a.passing:
        sys.exit(0 if set(counts) <= {"not_reproducible"} else 1)


if __name__ == "__main__":
    main()
