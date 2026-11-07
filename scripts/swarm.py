#!/usr/bin/env python3
"""Headless wave runner: the deterministic part of /uat-execute, for calibration, CI and unattended runs.

Assigns (family, model) x charter agents, runs them `max_parallel` at a time (one lane each, sandboxed), then validates, replays and
dedupes after every wave. No adaptive planning: the orchestrator skill adds that on top.

  swarm.py --agents codex:gpt-6-luna,claude:haiku,opencode:opencode/glm-5.3-flash-free [--charters CH-01,CH-02] [--root D]
Each agent gets the charters round-robin. Prints a one-line summary per agent and per wave.
"""
import argparse
import concurrent.futures as cf
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from uatlib import config, emit, exhausted_models, jload, udir  # noqa: E402

S = Path(__file__).resolve().parent


def py(script, *args, root):
    return subprocess.run([sys.executable, str(S / "uat.py"), script, *args, "--root", str(root)], capture_output=True, text=True)


def one(root, aid, lane, family, model, charter, cfg):
    mk = subprocess.run([sys.executable, str(S / "make_brief.py"), "--agent", aid, "--charter", charter, "--lane", lane, "--family", family, "--model", model, "--root", str(root)], capture_output=True, text=True)
    if mk.returncode:
        return aid, f"brief failed: {mk.stderr.strip()[-200:]}"
    p = py("run_agent", "--id", aid, "--lane", lane, "--family", family, "--model", model, "--brief", str(udir(root) / "agents" / aid / "brief.md"), root=root)
    return aid, (p.stdout.strip().splitlines() or [p.stderr.strip()[-200:]])[-1]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--agents", required=True, help="family:model[,family:model...]")
    ap.add_argument("--charters", help="default: every charter in plan.json")
    ap.add_argument("--root", default=os.getcwd())
    a = ap.parse_args()
    root = Path(a.root).resolve()
    cfg = config(root)
    plan = jload(udir(root) / "plan" / "plan.json", {})
    charters = a.charters.split(",") if a.charters else [c["id"] for c in plan["charters"]]
    specs = [x.split(":", 1) for x in a.agents.split(",")]
    dead = exhausted_models(root)
    for fam, m in [x for x in specs if x[1] in dead]:
        print(f"skip {fam}:{m}: out of quota until {time.strftime('%H:%M', time.localtime(dead[m]))}")
    specs = [x for x in specs if x[1] not in dead] or sys.exit("every requested model is out of quota")
    lanes = [f"L{i}" for i in range(1, cfg["swarm"]["max_parallel"] + 1)]
    jobs = [(f"A{i + 1}", specs[i % len(specs)][0], specs[i % len(specs)][1], charters[i % len(charters)]) for i in range(max(len(specs), len(charters)))]
    wave_n = len(lanes)
    t_all = time.time()
    for w in range(0, len(jobs), wave_n):
        wave, t0 = jobs[w:w + wave_n], time.time()
        emit(root, "orchestrator", "wave", n=w // wave_n + 1, agents=[j[0] for j in wave])
        with cf.ThreadPoolExecutor(len(wave)) as ex:
            for aid, line in ex.map(lambda ij: one(root, ij[1][0], lanes[ij[0]], ij[1][1], ij[1][2], ij[1][3], cfg), enumerate(wave)):
                print(f"{aid}: {line}", flush=True)
        for step in (("validate_findings",), ("replay",), ("dedupe",)):
            r = py(*step, root=root) if step[0] == "replay" else subprocess.run([sys.executable, str(S / f"{step[0]}.py"), "--root", str(root)], capture_output=True, text=True)
            print(f"  {step[0]}: {(r.stdout.strip().splitlines() or ['-'])[-1]}", flush=True)
        clusters = jload(udir(root) / "clusters.json", [])
        with open(udir(root) / "rounds.jsonl", "a") as f:
            import json
            f.write(json.dumps({"round": w // wave_n + 1, "agents": len(wave), "new_clusters": len(clusters), "wall_s": round(time.time() - t0)}) + "\n")
    print(f"done in {round(time.time() - t_all)}s")


if __name__ == "__main__":
    main()
