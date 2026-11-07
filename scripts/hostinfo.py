#!/usr/bin/env python3
"""Capabilities of the machine that will run the UAT env (run it locally or over SSH). Prints JSON.
Feeds /tusk-init preflight and the recommended swarm size.   hostinfo.py"""
import json
import os
import platform
import shutil
import subprocess
import sys

CLIS = {"claude": "--version", "codex": "--version", "opencode": "--version", "jg": "--version", "docker": "--version", "git": "--version", "rsync": "--version", "bwrap": "--version"}


def ver(cmd, flag):
    try:
        p = subprocess.run([cmd, flag], capture_output=True, text=True, timeout=15)
        return (p.stdout or p.stderr).strip().splitlines()[0][:80]
    except (OSError, subprocess.TimeoutExpired, IndexError):
        return "?"


def mem_gb():
    try:
        for ln in open("/proc/meminfo"):
            if ln.startswith("MemAvailable"):
                return round(int(ln.split()[1]) / 1048576, 1)
    except OSError:
        pass
    return 0.0


def main():
    cpus = os.cpu_count() or 1
    free_gb = round(shutil.disk_usage(os.path.expanduser("~")).free / 2**30, 1)
    mem = mem_gb()
    info = {
        "os": platform.platform(), "python": sys.version.split()[0], "cpus": cpus, "mem_available_gb": mem, "disk_free_gb": free_gb,
        "tools": {c: ver(c, f) for c, f in CLIS.items() if shutil.which(c)},
        # one lane (app copy + one agent) is about 1.5 GB RAM and 2 CPUs of headroom
        "recommended_parallel": max(1, min(6, int(mem // 1.5), cpus // 2)),
    }
    json.dump(info, sys.stdout, indent=2)
    print()


if __name__ == "__main__":
    main()
