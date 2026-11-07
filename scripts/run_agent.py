#!/usr/bin/env python3
"""Run one agent headlessly (any harness in scripts/harness.py: claude, codex, opencode, presets, or your own) inside its own lane and sandbox.

Every family goes through this one path, so confinement, timeout, tamper check, ledger and live events are identical for all:
  1. reset the agent's lane (own workspace copy + own app port)
  2. run the CLI sandboxed (writable: its agent dir only), model fallback list, heartbeat events for the dashboard
  3. tamper check on the lane; edits are reverted and the agent's findings are quarantined

  run_agent.py --id A4 --lane L2 --family codex --model gpt-5.6-luna --brief .tusk/agents/A4/brief.md
  run_agent.py --ping --family opencode --model opencode/glm-5.3-flash-free
Exit: 0 ok, 1 agent failed, 2 tamper, 4 every listed model is out of quota (recorded in .tusk/model_health.json; do not count it as a clean run).
"""
import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import harness  # noqa: E402
import sandbox  # noqa: E402
from tusk_env import Lane  # noqa: E402
from tusklib import HOME, QUOTA_RE, config, emit, finding_files, mark_exhausted, udir  # noqa: E402


def last_line(log):
    try:
        lines = [x for x in Path(log).read_text(errors="ignore").splitlines() if x.strip()]
        return lines[-1][:160] if lines else ""
    except OSError:
        return ""


def run(cmd, cwd, env, timeout, log, on_beat, stdin_text=None):
    """Run cmd, kill the whole process group on timeout, call on_beat() every 5 s."""
    with open(log, "wb") as lf:
        p = subprocess.Popen(cmd, cwd=cwd, env=env, stdin=subprocess.PIPE if stdin_text is not None else subprocess.DEVNULL, stdout=lf, stderr=subprocess.STDOUT, start_new_session=True,
                             preexec_fn=sandbox.limits)
        if stdin_text is not None:
            try:
                p.stdin.write(stdin_text.encode())
                p.stdin.close()
            except OSError:
                pass
        end, beat = time.time() + timeout, 0
        while p.poll() is None:
            if time.time() > end:
                os.killpg(p.pid, signal.SIGKILL)
                return 124
            if time.time() - beat > 5:
                beat = time.time()
                on_beat()
            time.sleep(0.5)
        return p.returncode


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--id", default="ping")
    ap.add_argument("--family", required=True)
    ap.add_argument("--model", required=True, help="comma list = fallback order")
    ap.add_argument("--lane", default="L1")
    ap.add_argument("--brief")
    ap.add_argument("--root", default=os.getcwd())
    ap.add_argument("--timeout", type=int)
    ap.add_argument("--ping", action="store_true")
    ap.add_argument("--cmd", help="test hook: run this command line instead of the CLI")
    a = ap.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", a.id):
        sys.exit(f"invalid agent id {a.id!r}")
    root = Path(a.root).resolve()
    cfg = config(root)
    if not a.cmd:
        harness.spec(cfg, a.family)                   # unknown harness: stop here with the list of known ones
    d = udir(root) / "agents" / a.id
    if not a.ping:
        (d / "findings").mkdir(parents=True, exist_ok=True)
    else:
        d.mkdir(parents=True, exist_ok=True)
    logdir = udir(root) / "logs"
    logdir.mkdir(exist_ok=True)
    log = logdir / f"{a.id}.log"                       # outside the agent-writable dir: the agent cannot swap it for a symlink
    timeout = a.timeout or (90 if a.ping else cfg["stop"]["agent_timeout_s"])
    prompt = "Reply with exactly: OK" if a.ping else f"Read {d / 'brief.md'} and follow it exactly. Write findings only under ./findings/."
    lane = Lane(root, cfg, a.lane)
    lvl = "none" if a.ping else sandbox.level(cfg)
    if not a.ping:
        lane.reset()
        emit(root, a.id, "agent_start", family=a.family, model=a.model.split(",")[0], lane=a.lane, brief=str(d / "brief.md"))
    env = os.environ.copy() if a.ping else sandbox.clean_env(
        cfg, {"TUSK_BASE_URL": lane.base_url, "TUSK_WS": str(lane.ws), "TUSK_LANE": a.lane, "TUSK_AGENT": a.id, "TUSK_ROOT": str(root), "TUSK_HOME": str(HOME), "HOME": str(Path.home())},
        [udir(root) / "tools" / s for s in ("venv/bin", "node_modules/.bin", "bin")])

    t0 = time.time()
    state = {"model": a.model.split(",")[0]}

    def beat():
        n = len(list((d / "findings").glob("F-*.json")))
        emit(root, a.id, "agent_status", now=last_line(log), findings=n, secs=round(time.time() - t0))

    rc, used, dead = 1, None, []
    tmp_home = None if a.ping else sandbox.agent_home(cfg, a.family, harness.home_files(cfg, a.family))
    for model in a.model.split(","):
        state["model"] = used = model
        inner, stdin_text = (a.cmd.split(), None) if a.cmd else harness.command(cfg, a.family, model, d, prompt, lvl == "bwrap")
        wr = [lane.ws / p for p in lane.tracked()]
        cmd = inner if a.ping else sandbox.wrap(cfg, inner, cwd=d, ws=lane.ws, tools=udir(root) / "tools", toolkit=HOME, home=tmp_home,
                                                net=cfg["sandbox"]["network"]["agent"], writable=wr)
        rc = run(cmd, d, env, timeout, log, beat, stdin_text)
        out = log.read_text(errors="ignore")
        if a.ping and rc == 0 and "OK" not in out:
            rc = 3                                     # process ran but did not answer: not a working model
        if rc == 0:
            break
        if harness.quota_re(cfg, a.family, QUOTA_RE).search(out[-4000:]):               # provider says out of quota: a dead model, not a clean result
            m = re.search(r'"retryAfterMs":\s*(\d+)', out)
            mark_exhausted(root, model, int(m.group(1)) / 1000 if m else 3600)
            dead.append(model)
            emit(root, a.id, "model_exhausted", model=model)
    secs = round(time.time() - t0, 1)
    shutil.rmtree(tmp_home, ignore_errors=True) if tmp_home else None   # credentials copy never outlives the run
    tamper = [] if a.ping else lane.tamper()
    if (d / "findings").is_symlink():                  # agent swapped its findings dir for a link: treat as tampering
        tamper.append("findings dir is a symlink")
    if tamper:
        q = udir(root) / "quarantine" / a.id
        q.mkdir(parents=True, exist_ok=True)
        for f in finding_files(root):
            if f.parent.parent.name == a.id:
                shutil.move(str(f), str(q / f.name))
    n = len([f for f in finding_files(root) if f.parent.parent.name == a.id])
    row = {"ts": int(time.time()), "id": a.id, "family": a.family, "model": used, "rc": rc, "secs": secs, "findings": n, "tamper": bool(tamper),
           "ping": a.ping, "lane": a.lane, "sandbox": lvl, "exhausted": dead}
    with open(udir(root) / "ledger.jsonl", "a") as f:
        f.write(json.dumps(row) + "\n")
    if not a.ping:
        emit(root, a.id, "agent_done", rc=rc, findings=n, secs=secs, tamper=tamper[:5], model=used,
             note="all models out of quota" if dead and len(dead) == len(a.model.split(",")) else "")
    print(json.dumps(row))
    sys.exit(2 if tamper else 0 if rc == 0 else 4 if len(dead) == len(a.model.split(",")) else 1)


if __name__ == "__main__":
    main()
