#!/usr/bin/env python3
"""Single entry point: run a toolkit script where the UAT env lives (local or SSH host).

  uat.py <script> [args...] [--root D]       e.g.  uat.py run_agent --id A1 --lane L1 ...   uat.py uat_env lanes up
Scripts in HOST_SIDE execute on the host and `.uat/` is mirrored back; the rest always run locally on the mirror.
"""
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import host  # noqa: E402
from uatlib import HOME, config, is_remote, udir  # noqa: E402

HOST_SIDE = {"run_agent", "replay", "uat_tools", "hostinfo", "uat_env"}
LOCAL_ENV_CMDS = {"guard"}          # scans the user's repo: always local


def split_root(argv):
    a = list(argv)
    if "--root" in a:
        i = a.index("--root")
        root = a[i + 1]
        del a[i:i + 2]
        return a, Path(root).resolve()
    return a, Path(os.getcwd()).resolve()


def local(script, args, r):
    return subprocess.call([sys.executable, str(HOME / "scripts" / f"{script}.py"), *args, "--root", str(r)] if script != "hostinfo" else [sys.executable, str(HOME / "scripts/hostinfo.py")])


def stage_workspace(r, cfg):
    """ssh mode: scrub-copy locally, push, then finish (setup_cmd + git baseline) on the host."""
    import uat_env
    uat_env.stage(r, cfg)
    host.push(cfg, str(udir(r) / "workspace") + "/", ".uat/workspace/", ["--delete"])


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    script, (args, r) = sys.argv[1], split_root(sys.argv[2:])
    cfg = config(r)
    remote_side = script in HOST_SIDE and not (script == "uat_env" and args[:1] and args[0] in LOCAL_ENV_CMDS)
    if not is_remote(cfg) or not remote_side:
        sys.exit(local(script, args, r))
    if not cfg["target"]["ssh"]["host"]:
        sys.exit("target.mode=ssh but target.ssh.host is empty")
    if script == "uat_env" and args[:1] == ["workspace"]:
        stage_workspace(r, cfg)
        args = ["workspace", "--finish"]
    host.sync_toolkit(cfg) if script != "hostinfo" and not (udir(r) / ".toolkit_synced").exists() else None
    (udir(r) / ".toolkit_synced").touch()
    host.sync_up(cfg, r)
    p = host.rsh(cfg, host.python_cmd(cfg, script, args) if script != "hostinfo" else f'python3 "{host.layout(cfg, ".")["home"]}/scripts/hostinfo.py"')
    sys.stdout.write(p.stdout)
    sys.stderr.write(p.stderr)
    host.sync_down(cfg, r)
    sys.exit(p.returncode)


if __name__ == "__main__":
    main()
