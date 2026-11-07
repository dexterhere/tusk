"""Filesystem/process confinement for anything an agent wrote or an agent CLI runs.

Mode "auto" uses bubblewrap when present. With `sandbox.require` (default) and no bubblewrap the run is refused:
fail closed, never silently weaker. What bubblewrap gives: read-only system, an empty $HOME with only whitelisted
tool dirs, a writable agent dir, a read-only lane workspace (plus declared writable paths), separate PID/IPC/UTS
namespaces, no other agent's directory. What it does NOT give: network confinement when the target is an HTTP service
(the tester must reach it), so sandbox.network.* defaults to "host". Use "none" for CLI targets. The level is
recorded in the report so nobody mistakes it for stronger isolation.
"""
import os
import re
import shutil
from pathlib import Path

ENV_KEEP = ("PATH", "LANG", "LC_ALL", "TERM", "TZ")
ENV_DENY = re.compile(r"SECRET|PASSWORD|PASSWD|TOKEN|CREDENTIAL|API_?KEY|AWS_|PRIVATE|SSH_|GPG|^GH_|^GITHUB_", re.I)
HOME_RO = (".local/share/mise", ".config/mise", ".local/bin", ".cargo", ".rustup", ".local/share/uv", ".nvm")


def level(cfg):
    """'bwrap' | 'none'. Raises when confinement is required but unavailable."""
    mode = cfg["sandbox"]["mode"]
    have = bool(shutil.which("bwrap"))
    if mode == "none" or (mode == "auto" and not have):
        if cfg["sandbox"]["require"]:
            raise SystemExit("sandbox: bubblewrap not available and sandbox.require=true. Install bubblewrap or set sandbox.require=false (weaker, recorded in report).")
        return "none"
    if not have:
        raise SystemExit("sandbox.mode=bwrap but bwrap not found")
    return "bwrap"


def clean_env(cfg, extra=None, path_prepend=()):
    """Allowlist, not denylist: only these variables reach agent-written code, plus the UAT_* ones passed in."""
    env = {k: os.environ[k] for k in ENV_KEEP if k in os.environ}
    env["PATH"] = ":".join([*map(str, path_prepend), env.get("PATH", "/usr/bin:/bin")])
    env.update(extra or {})
    return {k: v for k, v in env.items() if k.startswith("UAT_") or k in ENV_KEEP or not ENV_DENY.search(k)}


def wrap(cfg, argv, *, cwd, ws=None, tools=None, toolkit=None, family=None, net="host", writable=()):
    """Return argv prefixed with the sandbox. cwd = the agent dir (the only writable place besides `writable`)."""
    if level(cfg) == "none":
        return list(argv)
    home = Path.home()
    b = ["bwrap", "--unshare-pid", "--unshare-ipc", "--unshare-uts", "--die-with-parent", "--new-session",
         "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc", "--tmpfs", "/tmp", "--tmpfs", str(home)]
    if net == "none":
        b.append("--unshare-net")
    ro = [home / p for p in HOME_RO] + [Path(p).expanduser() for p in cfg["sandbox"]["extra_ro"]]
    ro += [Path(x) for x in (ws, tools, toolkit) if x]
    rw = [Path(cwd)] + [Path(w) for w in writable]
    rw += [home / p for p in cfg["sandbox"]["agent_home"].get(family or "", [])]
    for p in ro:
        if p.exists():
            b += ["--ro-bind", str(p), str(p)]
    for p in rw:
        if p.exists():
            b += ["--bind", str(p), str(p)]
    return b + ["--chdir", str(cwd), "--setenv", "HOME", str(home), *argv]
