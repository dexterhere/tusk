#!/usr/bin/env python3
"""UAT environment: prod-guard, scrubbed golden workspace, and isolated LANES.

A lane = its own copy of the golden workspace + its own port (+ its own app process). One tester runs in one lane, so parallel
testers never share state, and a tamper check (git status of the lane) is attributable to exactly one agent.

  uat_env.py guard                       # refuse prod-looking hosts or live keys
  uat_env.py workspace [--finish]        # copy repo -> .uat/workspace (secrets excluded), run setup_cmd, git baseline
  uat_env.py lanes up|down|status        # all lanes (count = swarm.max_parallel)
  uat_env.py lane reset L1 [--state-only]# back to baseline (code+state, or state only), restart the app
  uat_env.py lane tamper L1              # report and revert edits to the lane's code
  uat_env.py lane sync L1 SRC_DIR        # put a fixed source tree into a lane (fix proof), restart
Config: .uat/uat.json "env" block (see uat.defaults.json). start_cmd may use {port} {lane} (also $UAT_PORT $UAT_LANE).
"""
import argparse
import fnmatch
import os
import re
import shutil
import signal
import concurrent.futures as cf
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from uatlib import config, emit, lane_names, lane_port, lane_url, udir  # noqa: E402

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
    """The golden workspace: scrubbed copy, never executed, source of every lane reset."""
    return udir(r) / "workspace"


class Lane:
    def __init__(self, r, cfg, name):
        self.r, self.cfg, self.name = r, cfg, name
        self.dir = udir(r) / "lanes" / name
        self.ws = self.dir / "ws"
        self.port = lane_port(cfg, name)
        self.base_url = lane_url(cfg, name)
        self.health_url = lane_url(cfg, name, "health_url")
        self.pidfile = self.dir / "app.pid"

    def fmt(self, text):
        return text.replace("{port}", str(self.port)).replace("{lane}", self.name).replace("{name}", self.name)

    def env(self):
        keep = {k: os.environ[k] for k in ("PATH", "HOME", "LANG", "TERM") if k in os.environ}
        tools = ":".join(str(udir(self.r) / "tools" / s) for s in ("venv/bin", "node_modules/.bin", "bin"))
        keep["PATH"] = tools + ":" + keep.get("PATH", "")
        keep.update({k: self.fmt(str(v)) for k, v in self.cfg["env"]["vars"].items()})
        keep.update({"UAT_ENV": "1", "UAT_LANE": self.name, "UAT_PORT": str(self.port), "UAT_BASE_URL": self.base_url, "UAT_WS": str(self.ws)})
        return keep

    def pid(self):
        try:
            pid = int(self.pidfile.read_text())
            os.kill(pid, 0)
            return pid
        except (OSError, ValueError):
            return 0

    def tracked(self):
        """Paths allowed to change: declared state and build dirs. Everything else is the system under test."""
        e = self.cfg["env"]
        return [*e["state_paths"], *e["writable_paths"]]

    def up(self):
        e = self.cfg["env"]
        if not e["start_cmd"] or self.pid():
            return
        self.dir.mkdir(parents=True, exist_ok=True)
        # start_cmd is the user's own command from .uat/uat.json, approved in /uat-init; shell=True is deliberate.
        with open(self.dir / "app.log", "ab") as lf:
            p = subprocess.Popen(self.fmt(e["start_cmd"]), shell=True, cwd=self.ws, env=self.env(), stdout=lf, stderr=subprocess.STDOUT, start_new_session=True)
        self.pidfile.write_text(str(p.pid))
        end = time.time() + e["health_timeout_s"]
        while time.time() < end:
            if healthy(self.health_url):
                return
            if p.poll() is not None:
                break
            time.sleep(0.4)
        self.down()
        sys.exit(f"lane {self.name}: app not healthy at {self.health_url}; see {self.dir / 'app.log'}")

    def down(self):
        pid = self.pid()
        if pid:
            for sig in (signal.SIGTERM, signal.SIGKILL):
                try:
                    os.killpg(pid, sig)
                except OSError:
                    break
                time.sleep(0.8)
                if not self.pid():
                    break
        self.pidfile.unlink(missing_ok=True)
        if self.cfg["env"]["stop_cmd"]:
            sh(self.fmt(self.cfg["env"]["stop_cmd"]), self.ws if self.ws.exists() else ".", self.env(), check=False)

    def reset(self, state_only=False):
        e = self.cfg["env"]
        self.down()
        self.ws.mkdir(parents=True, exist_ok=True)
        gold = f"{ws(self.r)}/"
        if state_only:
            for rel in e["state_paths"]:
                subprocess.run(["rsync", "-a", "--delete", "--mkpath", f"{ws(self.r)}/{rel}".rstrip("/") + ("/" if (ws(self.r) / rel).is_dir() else ""), f"{self.ws}/{rel}"], check=False)
        else:
            subprocess.run(["rsync", "-a", "--delete", gold, f"{self.ws}/"], check=True)
            if e["lane_setup_cmd"]:
                sh(self.fmt(e["lane_setup_cmd"]), self.ws, self.env())
        if e["restore_cmd"]:
            sh(self.fmt(e["restore_cmd"]), self.ws, self.env())
        self.up()
        emit(self.r, "orchestrator", "lane_reset", lane=self.name, state_only=state_only)

    def tamper(self):
        """Edits to the system under test. Returns the porcelain lines and reverts them."""
        if not (self.ws / ".git").exists():
            return []
        spec = [f":(exclude){p}" for p in self.tracked()]
        out = subprocess.run(["git", "-C", str(self.ws), "status", "--porcelain", "--", ".", *spec], capture_output=True, text=True).stdout.splitlines()
        if out:
            subprocess.run(["git", "-C", str(self.ws), "checkout", "--", "."], capture_output=True)
            subprocess.run(["git", "-C", str(self.ws), "clean", "-fdq", "--", ".", *spec], capture_output=True)
        return out

    def sync(self, src):
        """Fix proof: a fixed source tree over the lane (state paths kept), then restart."""
        self.down()
        ex = [f"--exclude={x}" for x in (".git", ".uat", "node_modules", "target", ".venv", *self.tracked())]
        subprocess.run(["rsync", "-a", "--delete", *ex, f"{src.rstrip('/')}/", f"{self.ws}/"], check=True)
        self.up()


def healthy(url):
    try:
        urllib.request.urlopen(url, timeout=3)
        return True
    except urllib.error.HTTPError as e:
        return e.code < 500
    except (urllib.error.URLError, OSError):
        return False


def sh(cmd, cwd, env, check=True):
    # cmd comes from the project's own uat.json (user-approved in /uat-init), not from agents.
    p = subprocess.run(cmd, shell=True, cwd=cwd, env=env)
    if check and p.returncode:
        sys.exit(f"command failed ({p.returncode}): {cmd}")
    return p.returncode


def golden_env(r, cfg):
    keep = {k: os.environ[k] for k in ("PATH", "HOME", "LANG", "TERM") if k in os.environ}
    keep.update(cfg["env"]["vars"])
    keep.update({"UAT_ENV": "1", "UAT_WS": str(ws(r))})
    return keep


def stage(r, cfg):
    w = ws(r)
    if w.exists():
        shutil.rmtree(w)
    shutil.copytree(r, w, ignore=ignore_fn, symlinks=True)


def finish(r, cfg):
    w = ws(r)
    if cfg["env"]["setup_cmd"]:
        sh(cfg["env"]["setup_cmd"], w, golden_env(r, cfg))
    g = ["git", "-C", str(w)]
    subprocess.run(g + ["init", "-q"], check=True)
    subprocess.run(g + ["add", "-A"], check=True)
    subprocess.run(g + ["-c", "user.email=uat@local", "-c", "user.name=uat", "commit", "-qm", "uat baseline", "--allow-empty"], check=True)
    print(f"workspace ready: {w}")


def lanes_up(r, cfg):
    names = lane_names(cfg)
    port_free = "{port}" in cfg["env"]["start_cmd"] or "UAT_PORT" in cfg["env"]["start_cmd"]
    if len(names) > 1 and cfg["env"]["start_cmd"] and not port_free:
        sys.exit("start_cmd has no {port} / $UAT_PORT, so lanes would collide on one port. Make the app take its port from it, or set swarm.max_parallel=1.")
    with cf.ThreadPoolExecutor(len(names)) as ex:
        list(ex.map(lambda n: Lane(r, cfg, n).reset(), names))
    for n in names:
        print(f"{n} up {Lane(r, cfg, n).base_url}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["guard", "workspace", "lanes", "lane"])
    ap.add_argument("args", nargs="*")
    ap.add_argument("--root", default=os.getcwd())
    ap.add_argument("--finish", action="store_true")
    ap.add_argument("--state-only", action="store_true")
    a = ap.parse_args()
    r = Path(a.root).resolve()
    cfg = config(r)
    if a.cmd == "guard":
        hits = guard(r, cfg)
        print("\n".join(hits) or "guard: clean")
        sys.exit(1 if hits else 0)
    if a.cmd == "workspace":
        if not a.finish:
            stage(r, cfg)
        finish(r, cfg)
    elif a.cmd == "lanes":
        sub = a.args[0] if a.args else "status"
        if sub == "up":
            lanes_up(r, cfg)
        elif sub == "down":
            [Lane(r, cfg, n).down() for n in lane_names(cfg)]
        else:
            bad = 0
            for n in lane_names(cfg):
                ln = Lane(r, cfg, n)
                ok = (not cfg["env"]["start_cmd"]) or (ln.pid() and healthy(ln.health_url))
                bad += not ok
                print(f"{n} pid={ln.pid() or '-'} healthy={bool(ok)} {ln.base_url}")
            sys.exit(1 if bad else 0)
    else:
        sub, name = a.args[0], a.args[1]
        ln = Lane(r, cfg, name)
        if sub == "reset":
            ln.reset(a.state_only)
        elif sub == "tamper":
            t = ln.tamper()
            print("\n".join(t) or "clean")
            sys.exit(2 if t else 0)
        elif sub == "sync":
            ln.sync(a.args[2])


if __name__ == "__main__":
    main()
