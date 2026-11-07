#!/usr/bin/env python3
"""Replay every finding's repro N times, in a lane, inside the same sandbox as the testers. The script, not a model, judges.

Trust model: a finding is first SNAPSHOT into .uat/trusted/ (finding JSON + its declared repro files + digest). Replay runs that copy in a
fresh scratch dir, so a repro cannot rewrite its own finding, files or verdict. Dedupe/report/fixq use the snapshot, never the live agent dir.

repro.cmd asserts CORRECT behaviour. Exit codes (replay.fail_codes, default [1]) mean "bug present"; 0 means correct; anything else
(2 syntax error, 126/127, 137 kill, 139 segfault, timeout) means the repro itself is broken => `error`, not a confirmed bug.
A repro that never touches the target (no $UAT_BASE_URL / $UAT_WS / lane URL in its command or files) is flagged `touches_target=false`:
it proves repeatability, not truth, so dedupe sends it to the verifier.

  status: confirmed | flaky (fail_rate recorded) | not_reproducible | error | tamper
Concurrency findings get `replay.stress_runs` runs. Findings replay in parallel, one lane each.

  replay.py [--root D] [--runs N] [--only CLUSTER_ID] [--passing] [--lane L1] [--keep-code]
  --passing    fix check: exit 0 only if >=1 finding ran and every one now exits 0 on every run (use --keep-code after `uat_env lane sync`)
"""
import argparse
import hashlib
import concurrent.futures as cf
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sandbox  # noqa: E402
from uat_env import Lane  # noqa: E402
from uatlib import HOME, trusted, clean, config, emit, finding_files, finding_schema, jdump, jload, lane_names, replay_path, snapshot_finding, udir, validate  # noqa: E402

TARGET_MARKERS = ("UAT_BASE_URL", "UAT_WS", "UAT_LANE", "127.0.0.1", "localhost")


def run_count(obj, cfg, runs):
    stress = obj.get("category") == "concurrency" or obj.get("nondeterministic")
    return min(max(runs, cfg["replay"]["stress_runs"]), 50) if stress else runs


def touches_target(obj, files_dir, cfg):
    text = obj["repro"]["cmd"] + "".join(p.read_text(errors="ignore") for p in Path(files_dir).rglob("*") if p.is_file() and p.stat().st_size < 200_000)
    return any(m in text for m in (*TARGET_MARKERS, *cfg["replay"]["target_markers"]))


def replay_one(f, cfg, root, lane, runs, keep_code, use_trusted=False):
    # fix proofs replay the snapshot taken before the first replay; the live agent dir may have been edited since
    obj, digest = trusted(root, f) if use_trusted else snapshot_finding(root, f)
    if obj is None or validate(obj, finding_schema()):
        return "error"
    t = udir(root) / "trusted" / f.parent.parent.name / f.stem / "files"       # execution uses ONLY this copy
    n = run_count(obj, cfg, runs)
    net = "none" if cfg["env"]["kind"] == "cli" else cfg["sandbox"]["network"]["replay"]
    fail_codes = set(cfg["replay"]["fail_codes"])
    results = []
    for i in range(n):
        lane.reset(state_only=keep_code)
        (udir(root) / "scratch").mkdir(exist_ok=True)
        scratch = Path(tempfile.mkdtemp(prefix=f"uat-{lane.name}-", dir=udir(root) / "scratch"))
        try:
            shutil.copytree(t, scratch, dirs_exist_ok=True)
            env = sandbox.clean_env(cfg, {"UAT_BASE_URL": lane.base_url, "UAT_WS": str(lane.ws), "UAT_AGENT_DIR": str(scratch), "UAT_LANE": lane.name, "HOME": str(Path.home())},
                                    [udir(root) / "tools" / s for s in ("venv/bin", "node_modules/.bin", "bin")])
            cmd = sandbox.wrap(cfg, ["bash", "-c", obj["repro"]["cmd"]], cwd=scratch, ws=lane.ws, tools=udir(root) / "tools", toolkit=HOME, net=net,
                               writable=[lane.ws / p for p in lane.tracked()])
            t0 = time.time()
            with tempfile.TemporaryFile() as out:
                try:
                    p = subprocess.run(cmd, cwd=scratch, env=env, stdout=out, stderr=subprocess.STDOUT, timeout=cfg["replay"]["timeout_s"],
                                       preexec_fn=sandbox.limits, start_new_session=True)
                    rc = p.returncode
                except subprocess.TimeoutExpired:
                    rc = None
                out.seek(0)
                head = out.read(200).decode("utf-8", "replace")
                if rc is not None and head.startswith("bwrap:"):        # the sandbox itself failed to start: that is not a bug in the target
                    rc = None
                out.seek(max(0, out.seek(0, 2) - 400))
                tail = clean(out.read().decode("utf-8", "replace"), 400)
            results.append({"rc": rc, "secs": round(time.time() - t0, 1), "tail": tail})
        finally:
            shutil.rmtree(scratch, ignore_errors=True)
    fails = sum(1 for x in results if x["rc"] in fail_codes)
    passes = sum(1 for x in results if x["rc"] == 0)
    errors = len(results) - fails - passes
    if lane.tamper():
        status = "tamper"
    elif errors == len(results):
        status = "error"
    elif fails == len(results):
        status = "confirmed"
    elif fails == 0 and errors == 0:
        status = "not_reproducible"
    elif fails == 0:
        status = "error"
    else:
        status = "flaky"
    rate = round(fails / len(results), 3)
    jdump(replay_path(root, f), {"status": status, "fail_rate": rate, "lane": lane.name, "digest": digest, "touches_target": touches_target(obj, t, cfg), "runs": results})
    emit(root, "orchestrator", "replay", finding=clean(obj.get("id", "?"), 60), agent=f.parent.parent.name, status=status, fail_rate=rate)
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
        cl = next((c for c in jload(udir(r) / "clusters.json", []) if c["cid"] == a.only), None)
        if cl is None:
            sys.exit(f"unknown cluster {a.only}")
        files = [f for f in files if (trusted(r, f)[0] or {}).get("id") in cl["members"] and f.parent.parent.name in cl["agents"]]
        if len(files) < len(cl["members"]):
            sys.exit(f"cluster {a.only}: {len(cl['members'])} member findings expected, {len(files)} found")
        if hashlib.sha256("".join(sorted(trusted(r, f)[1] for f in files)).encode()).hexdigest()[:16] != cl.get("digest"):
            sys.exit(f"cluster {a.only}: the trusted snapshots no longer match the cluster's evidence digest; re-run the replay and dedupe steps")
    if a.passing and not files:
        sys.exit("--passing with nothing to replay is not a proof")
    names = [a.lane] if a.lane else lane_names(cfg)
    pool = queue.Queue()
    for n in names:
        pool.put(Lane(r, cfg, n))

    def job(f):
        lane = pool.get()
        try:
            return f, replay_one(f, cfg, r, lane, runs, a.keep_code, bool(a.only or a.passing))
        finally:
            pool.put(lane)

    counts = {}
    with cf.ThreadPoolExecutor(cfg["replay"]["workers"] or len(names)) as ex:
        for f, s in ex.map(job, files):
            counts[s] = counts.get(s, 0) + 1
            print(f"{clean(f.parent.parent.name, 40)}/{clean(f.stem, 60)}: {s}")
    print(counts)
    if a.passing:
        sys.exit(0 if set(counts) == {"not_reproducible"} else 1)


if __name__ == "__main__":
    main()
