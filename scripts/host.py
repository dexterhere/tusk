"""Where the UAT environment runs: this machine, or any machine reachable over SSH (VPS, lab box, spare laptop).

The orchestrator and dashboard always stay local. In ssh mode the workspace, app lanes, tools, tester CLIs and replay run on the
host; `.uat/` is mirrored back after every call so reports and the dashboard read local files. Needs only ssh + rsync + python3.
Test hook: UAT_SSH_CMD replaces the ssh command (a shim that runs locally).
"""
import os
import shlex
import subprocess
from pathlib import Path

from uatlib import HOME, layout, udir

HEAVY = ("workspace/", "lanes/", "tools/", "snapshots/", "fix/", "logs/*.log")


def ssh_argv(cfg):
    s = cfg["target"]["ssh"]
    if os.environ.get("UAT_SSH_CMD"):
        return shlex.split(os.environ["UAT_SSH_CMD"])
    a = ["ssh", "-o", "BatchMode=yes", "-o", "ControlMaster=auto", "-o", "ControlPath=~/.ssh/uat-%C", "-o", "ControlPersist=300",
         "-o", "ServerAliveInterval=20", "-p", str(s["port"])]
    return a + (["-i", s["identity"]] if s["identity"] else []) + [s["host"]]


def _rpath(p):
    return p.replace("~/", "$HOME/", 1) if p.startswith("~/") else p


def rsh(cfg, script, timeout=None, check=False, text=True):
    """Run a shell snippet on the host (in the run directory)."""
    d = _rpath(layout(cfg, ".")["root"])
    cmd = ssh_argv(cfg) + [f'mkdir -p "{d}" && cd "{d}" && {script}']
    return subprocess.run(cmd, capture_output=True, text=text, timeout=timeout, check=check)


def _rsync(cfg, src, dst, flags):
    e = " ".join(shlex.quote(x) for x in ssh_argv(cfg)[:-1] if x) if os.environ.get("UAT_SSH_CMD") is None else os.environ["UAT_SSH_CMD"]
    return subprocess.run(["rsync", "-a", "-e", e, *flags, src, dst], capture_output=True, text=True)


def _dest(cfg, rel):
    h = cfg["target"]["ssh"]["host"]
    base = layout(cfg, ".")["root"]
    return f"{h}:{base}/{rel}"


def push(cfg, src, rel, flags=()):
    r = _rsync(cfg, str(src), _dest(cfg, rel), ["--mkpath", *flags])
    if r.returncode:
        raise SystemExit(f"rsync push failed: {r.stderr.strip()[-300:]}")


def sync_toolkit(cfg):
    push(cfg, f"{HOME}/", "toolkit/", ["--delete", "--exclude=.git", "--exclude=__pycache__", "--exclude=.uat", "--exclude=calibration/runs"])


def sync_up(cfg, r):
    """Config, plan and briefs go to the host."""
    u = udir(r)
    flags = ["-R", "--include=uat.json", "--include=plan/***", "--include=state.json", "--include=agents/", "--include=agents/*/", "--include=agents/*/brief.md", "--exclude=*"]
    _rsync(cfg, f"{u}/", _dest(cfg, ".uat/"), ["--mkpath", *flags[1:]])


def sync_down(cfg, r):
    """Everything the host produced, except heavy trees. Never deletes local files."""
    u = udir(r)
    ex = [f"--exclude={h}" for h in HEAVY] + ["--exclude=agents/*/brief.md", "--exclude=uat.json", "--exclude=state.json", "--exclude=plan/", "--exclude=report/", "--exclude=fixqueue.json"]
    # a compromised host must not plant symlinks/devices in the local mirror
    _rsync(cfg, _dest(cfg, ".uat/"), f"{u}/", ex + ["--no-links", "--no-devices", "--no-specials", "--max-size=20M"])


def python_cmd(cfg, script, args):
    """Remote command line for toolkit/scripts/<script>.py with --root rewritten to the host's run directory."""
    h = _rpath(layout(cfg, ".")["home"])
    root = _rpath(layout(cfg, ".")["root"])
    return f'python3 "{h}/scripts/{script}.py" --root "{root}" ' + " ".join(shlex.quote(a) for a in args)
