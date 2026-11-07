#!/usr/bin/env python3
"""Install catalog tools into <root>/.tusk/tools (isolated; nothing global).

  tusk_tools.py list    [--root D]
  tusk_tools.py install NAME... [--root D] [--dry-run]
  tusk_tools.py env     [--root D]     # PATH prefix for agents / replay
Docker images pull lazily on first run unless installed here.
"""
import argparse
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tusklib import HOME, jload  # noqa: E402


def catalog():
    return {t["name"]: t for t in jload(HOME / "resources" / "tool-catalog.json", {"tools": []})["tools"]}


def plan(t, tools):
    m, spec = t["install"]["method"], t["install"]["spec"]
    if m == "pip":
        return [[sys.executable, "-m", "venv", str(tools / "venv")], [str(tools / "venv/bin/pip"), "install", "-q", *shlex.split(spec)]]
    if m == "npm":
        return [["npm", "install", "--silent", "--prefix", str(tools), *shlex.split(spec)]]
    if m == "docker":
        return [["docker", "pull", spec]]
    if m == "cargo":
        return [["cargo", "install", "--root", str(tools), spec]]
    return []  # builtin/system: manual


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["list", "install", "env"])
    ap.add_argument("names", nargs="*")
    ap.add_argument("--root", default=os.getcwd())
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    tools = Path(a.root).resolve() / ".tusk" / "tools"
    cat = catalog()
    if a.cmd == "list":
        for n, t in cat.items():
            print(f"{n:16} {t['kind']:10} {t['install']['method']:8} {t['license']}")
        return 0
    if a.cmd == "env":
        print(":".join(str(tools / s) for s in ("venv/bin", "node_modules/.bin", "bin")))
        return 0
    rc = 0
    for n in a.names:
        t = cat.get(n)
        if not t:
            print(f"unknown tool: {n}", file=sys.stderr)
            rc = 1
            continue
        steps = plan(t, tools)
        if not steps:
            print(f"{n}: manual -> {t['install']['spec']}")
            continue
        if steps[0][0] != sys.executable and not shutil.which(steps[0][0]):
            print(f"{n}: missing prerequisite {steps[0][0]}", file=sys.stderr)
            rc = 1
            continue
        for s in steps:
            print("+", " ".join(s))
            if a.dry_run:
                continue
            tools.mkdir(parents=True, exist_ok=True)
            if s[1:3] == ["-m", "venv"] and (tools / "venv").exists():
                continue
            if subprocess.run(s).returncode:
                rc = 1
                break
    return rc


if __name__ == "__main__":
    sys.exit(main())
