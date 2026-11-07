#!/usr/bin/env python3
"""UAT environment: prod-guard, scrubbed workspace copy, up/down/status, snapshot/restore/sync.

  uat_env.py guard     [--root D]   # refuse prod-looking hosts or live keys
  uat_env.py workspace [--root D]   # copy repo -> .uat/workspace (secrets excluded), run setup_cmd, git baseline
  uat_env.py up|down|status|snapshot NAME|restore NAME|sync SRC_DIR
Config: .uat/uat.json "env" block (see uat.defaults.json).
"""
import argparse
import fnmatch
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from uatlib import config, udir  # noqa: E402

URL_RE = re.compile(r"\b(postgres(?:ql)?|mysql|mariadb|mongodb(?:\+srv)?|redis|rediss|amqps?|mssql)://(?:[^\s'\"<>/@]*@)?([A-Za-z0-9_.${}-]+)")
KEY_RE = re.compile(r"(sk_live_[A-Za-z0-9]{10,}|AKIA[0-9A-Z]{16}|xox[bp]-[A-Za-z0-9-]{10,}|ghp_[A-Za-z0-9]{30,}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----)")
COPY_IGNORE = {".git", ".uat", "node_modules", "target", ".venv", "venv", "__pycache__", "graphify-out", ".dart_tool", ".next"}
SECRET_FILES = (".env", ".env.*", "*.pem", "*.key", "id_rsa*", "*.p12", "*.keystore", "credentials*.json", "service-account*.json")
KEEP_FILES = (".env.example", ".env.sample", ".env.template")


def guard(r, cfg):
    safe = tuple(cfg["guard"]["safe_hosts"])
    hits = []
    for dp, dns, fns in os.walk(r):
        dns[:] = [d for d in dns if d not in COPY_IGNORE]
        if len(Path(dp).relative_to(r).parts) > 3:
            dns[:] = []
        for fn in fns:
            if not any(fnmatch.fnmatch(fn, g.split("/")[-1]) for g in cfg["guard"]["scan_globs"]):
                continue
            p = Path(dp) / fn
            try:
                if p.stat().st_size > 200_000:
                    continue
                text = p.read_text(errors="ignore")
            except OSError:
                continue
            for m in URL_RE.finditer(text):
                host = m.group(2).lower()
                if not (host in safe or host.endswith((".local", ".test", ".internal", ".localhost")) or host.startswith("${")):
                    hits.append(f"{p.relative_to(r)}: remote datastore host '{host}'")
            for m in KEY_RE.finditer(text):
                hits.append(f"{p.relative_to(r)}: live-looking credential ({m.group(1)[:8]}...)")
    return hits


def ignore_fn(_dir, names):
    out = set()
    for n in names:
        if n in COPY_IGNORE or (any(fnmatch.fnmatch(n, g) for g in SECRET_FILES) and n not in KEEP_FILES):
            out.add(n)
    return out


def ws(r):
    return udir(r) / "workspace"


def minenv(cfg):
    keep = {k: os.environ[k] for k in ("PATH", "HOME", "LANG", "TERM") if k in os.environ}
    tools_path = ":".join(str(Path(cfg.get("_root", ".")) / ".uat/tools" / s) for s in ("venv/bin", "node_modules/.bin", "bin"))
    keep["PATH"] = tools_path + ":" + keep.get("PATH", "")
    keep.update(cfg["env"]["vars"])
    keep["UAT_ENV"] = "1"
    keep["UAT_BASE_URL"] = cfg["env"]["base_url"]
    keep["UAT_WS"] = str(ws(cfg["_root"]))
    return keep


def sh(cmd, cwd, env, check=True):
    # cmd comes from the project's own uat.json (user-approved in /uat-init), not from agents.
    p = subprocess.run(cmd, shell=True, cwd=cwd, env=env)
    if check and p.returncode:
        sys.exit(f"command failed ({p.returncode}): {cmd}")
    return p.returncode


def cmd_workspace(r, cfg):
    w = ws(r)
    if w.exists():
        shutil.rmtree(w)
    shutil.copytree(r, w, ignore=ignore_fn, symlinks=True)
    if cfg["env"]["setup_cmd"]:
        sh(cfg["env"]["setup_cmd"], w, minenv(cfg))
    g = ["git", "-C", str(w)]
    subprocess.run(g + ["init", "-q"], check=True)
    subprocess.run(g + ["add", "-A"], check=True)
    subprocess.run(g + ["-c", "user.email=uat@local", "-c", "user.name=uat", "commit", "-qm", "uat baseline", "--allow-empty"], check=True)
    print(f"workspace ready: {w}")


def pidfile(r):
    return udir(r) / "app.pid"


def alive(r):
    try:
        pid = int(pidfile(r).read_text())
        os.kill(pid, 0)
        return pid
    except (OSError, ValueError):
        return 0


def healthy(url):
    try:
        urllib.request.urlopen(url, timeout=3)
        return True
    except urllib.error.HTTPError as e:
        return e.code < 500
    except (urllib.error.URLError, OSError):
        return False


def cmd_up(r, cfg):
    if alive(r):
        print("already up")
        return
    if not cfg["env"]["start_cmd"]:
        sys.exit("env.start_cmd not set in .uat/uat.json")
    logs = udir(r) / "logs"
    logs.mkdir(exist_ok=True)
    # shell=True is deliberate: start_cmd is the user's own command line from .uat/uat.json, approved in /uat-init.
    with open(logs / "app.log", "ab") as lf:
        p = subprocess.Popen(cfg["env"]["start_cmd"], shell=True, cwd=ws(r), env=minenv(cfg), stdout=lf, stderr=subprocess.STDOUT, start_new_session=True)
    pidfile(r).write_text(str(p.pid))
    end = time.time() + cfg["env"]["health_timeout_s"]
    while time.time() < end:
        if healthy(cfg["env"]["health_url"]):
            print(f"up pid={p.pid} {cfg['env']['base_url']}")
            return
        if p.poll() is not None:
            break
        time.sleep(0.5)
    cmd_down(r, cfg)
    sys.exit(f"app not healthy at {cfg['env']['health_url']}; see {logs / 'app.log'}")


def cmd_down(r, cfg):
    pid = alive(r)
    if pid:
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(pid, sig)
            except OSError:
                break
            time.sleep(1)
            if not alive(r):
                break
    pidfile(r).unlink(missing_ok=True)
    if cfg["env"]["stop_cmd"]:
        sh(cfg["env"]["stop_cmd"], ws(r), minenv(cfg), check=False)
    print("down")


def snap_dir(r, name):
    return udir(r) / "snapshots" / name


def cmd_snapshot(r, cfg, name):
    if cfg["env"]["snapshot_cmd"]:
        return sh(cfg["env"]["snapshot_cmd"].replace("{name}", name), ws(r), minenv(cfg))
    d = snap_dir(r, name)
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True)
    for rel in cfg["env"]["state_paths"]:
        src = ws(r) / rel
        if src.is_dir():
            shutil.copytree(src, d / rel)
        elif src.exists():
            (d / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, d / rel)
    print(f"snapshot {name}")


def cmd_restore(r, cfg, name):
    if cfg["env"]["restore_cmd"]:
        return sh(cfg["env"]["restore_cmd"].replace("{name}", name), ws(r), minenv(cfg))
    d = snap_dir(r, name)
    if not d.exists():
        sys.exit(f"no snapshot {name}")
    was_up = alive(r)
    if was_up:
        cmd_down(r, cfg)
    for rel in cfg["env"]["state_paths"]:
        dst = ws(r) / rel
        if dst.is_dir():
            shutil.rmtree(dst)
        elif dst.exists():
            dst.unlink()
        if (d / rel).is_dir():
            shutil.copytree(d / rel, dst)
        elif (d / rel).exists():
            shutil.copy2(d / rel, dst)
    if was_up:
        cmd_up(r, cfg)
    print(f"restored {name}")


def cmd_sync(r, cfg, src):
    """Copy a fixed source tree (e.g. a fix worktree) over the workspace, then restart."""
    was_up = alive(r)
    if was_up:
        cmd_down(r, cfg)
    ex = [f"--exclude={x}" for x in (".git", ".uat", *cfg["env"]["state_paths"])]
    subprocess.run(["rsync", "-a", "--delete", *ex, "--exclude=node_modules", "--exclude=target", "--exclude=.venv", f"{src.rstrip('/')}/", f"{ws(r)}/"], check=True)
    if was_up:
        cmd_up(r, cfg)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["guard", "workspace", "up", "down", "status", "snapshot", "restore", "sync"])
    ap.add_argument("arg", nargs="?")
    ap.add_argument("--root", default=os.getcwd())
    a = ap.parse_args()
    r = Path(a.root).resolve()
    cfg = config(r)
    cfg["_root"] = str(r)
    if a.cmd == "guard":
        hits = guard(r, cfg)
        print("\n".join(hits) or "guard: clean")
        sys.exit(1 if hits else 0)
    elif a.cmd == "workspace":
        cmd_workspace(r, cfg)
    elif a.cmd == "up":
        cmd_up(r, cfg)
    elif a.cmd == "down":
        cmd_down(r, cfg)
    elif a.cmd == "status":
        pid = alive(r)
        h = healthy(cfg["env"]["health_url"]) if pid else False
        print(f"pid={pid or '-'} healthy={h} workspace={ws(r).exists()}")
        sys.exit(0 if pid and h else 1)
    elif a.cmd in ("snapshot", "restore", "sync"):
        if not a.arg:
            sys.exit("name/dir required")
        {"snapshot": cmd_snapshot, "restore": cmd_restore, "sync": cmd_sync}[a.cmd](r, cfg, a.arg)


if __name__ == "__main__":
    main()
