#!/usr/bin/env python3
"""Agent harness registry: how to run ANY coding-agent CLI headlessly as a tester, fixer or verifier.

A harness is a command template plus a few facts. Three ship verified on this machine (claude, codex, opencode). Others are presets that
follow each tool's documented non-interactive flags but are NOT verified here: run `run_agent.py --ping --family <name> --model <m>` first.
Add your own, or override a preset, in .tusk/tusk.json:

  "harness": {"mycli": {"cmd": ["mycli", "--model", "{model}", "--yolo", "-p", "{prompt}"],
                        "prompt": "arg",                       # arg (default) | stdin | file ({prompt_file})
                        "home_files": [".mycli/auth.json"],    # copied into the throwaway HOME (files only, never directories)
                        "quota_regex": "rate limit|quota"}}    # optional: marks a model dead instead of failing the run
  "models":  {"mycli": {"cheap": ["some-model"]}}

Placeholders: {model} {prompt} {prompt_file} {agent_dir}. `cmd_confined` replaces `cmd` when our own sandbox already confines the agent
(needed where the tool has a nested sandbox of its own). Anything the CLI cannot do (HTTP-only agents, IDE agents) is reachable with a
small wrapper script that reads {prompt_file} and writes ./findings/F-*.json; point `cmd` at the wrapper.

  harness.py list [--root D]     known harnesses, whether the program is installed, whether verified
"""
import argparse
import os
import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tusklib import config  # noqa: E402

BUILTIN = {
    "claude": {"cmd": ["claude", "-p", "{prompt}", "--model", "{model}", "--permission-mode", "acceptEdits", "--allowedTools", "Bash Read Write Edit Glob Grep"], "verified": True},
    "codex": {"cmd": ["codex", "exec", "-m", "{model}", "-s", "workspace-write", "-C", "{agent_dir}", "--skip-git-repo-check", "--ephemeral", "--json", "-o", "{agent_dir}/last.txt", "{prompt}"],
              "cmd_confined": ["codex", "exec", "-m", "{model}", "-s", "danger-full-access", "-C", "{agent_dir}", "--skip-git-repo-check", "--ephemeral", "--json", "-o", "{agent_dir}/last.txt", "{prompt}"], "verified": True},
    "opencode": {"cmd": ["opencode", "run", "-m", "{model}", "--format", "json", "--auto", "{prompt}"], "verified": True},
    # presets from each tool's documented headless mode; not run here
    "gemini": {"cmd": ["gemini", "-m", "{model}", "--yolo", "-p", "{prompt}"], "home_files": [".gemini/settings.json", ".gemini/oauth_creds.json"], "verified": False},
    "qwen": {"cmd": ["qwen", "-m", "{model}", "--yolo", "-p", "{prompt}"], "home_files": [".qwen/settings.json", ".qwen/oauth_creds.json"], "verified": False},
    "aider": {"cmd": ["aider", "--model", "{model}", "--yes-always", "--no-auto-commits", "--message", "{prompt}"], "verified": False},
    "goose": {"cmd": ["goose", "run", "--text", "{prompt}"], "home_files": [".config/goose/config.yaml"], "verified": False},
    "amp": {"cmd": ["amp", "--dangerously-allow-all", "-x", "{prompt}"], "verified": False},
    "cursor": {"cmd": ["cursor-agent", "-p", "{prompt}", "--model", "{model}", "--force"], "verified": False},
    "copilot": {"cmd": ["copilot", "-p", "{prompt}", "--allow-all-tools"], "verified": False},
    "crush": {"cmd": ["crush", "run", "{prompt}"], "verified": False},
}


def registry(cfg):
    reg = {k: dict(v) for k, v in BUILTIN.items()}
    for k, v in (cfg.get("harness") or {}).items():
        reg.setdefault(k, {"verified": False}).update(v)
    return reg


def spec(cfg, family):
    s = registry(cfg).get(family)
    if not s or not s.get("cmd"):
        raise SystemExit(f"unknown agent harness {family!r}. Known: {', '.join(sorted(registry(cfg)))}. Define it under \"harness\" in .tusk/tusk.json (see scripts/harness.py).")
    return s


def command(cfg, family, model, agent_dir, prompt, confined):
    """-> (argv, stdin_text or None)."""
    s = spec(cfg, family)
    tpl = s.get("cmd_confined") if confined and s.get("cmd_confined") else s["cmd"]
    mode = s.get("prompt", "arg")
    pf = Path(agent_dir) / "prompt.txt"
    if mode == "file":
        pf.write_text(prompt)
    sub = {"{model}": model, "{agent_dir}": str(agent_dir), "{prompt_file}": str(pf), "{prompt}": prompt if mode == "arg" else f"Read {pf} and follow it exactly." if mode == "file" else ""}
    argv = []
    for t in tpl:
        for k, v in sub.items():
            t = t.replace(k, v)
        if t != "":
            argv.append(t)
    return argv, (prompt if mode == "stdin" else None)


def home_files(cfg, family):
    s = registry(cfg).get(family, {})
    return s.get("home_files") if s.get("home_files") is not None else cfg["sandbox"]["agent_home"].get(family, [])


def quota_re(cfg, family, default):
    q = registry(cfg).get(family, {}).get("quota_regex")
    return re.compile(q, re.I) if q else default


def listing(cfg):
    out = []
    for k, v in sorted(registry(cfg).items()):
        b = (v.get("cmd") or [""])[0]
        out.append({"name": k, "program": b, "installed": bool(shutil.which(b)), "verified": bool(v.get("verified")), "prompt": v.get("prompt", "arg")})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["list"])
    ap.add_argument("--root", default=os.getcwd())
    a = ap.parse_args()
    for x in listing(config(Path(a.root).resolve())):
        print(f"{x['name']:10} {'installed' if x['installed'] else 'not installed':14} {'verified' if x['verified'] else 'preset (run --ping first)':26} prompt via {x['prompt']}  ({x['program']})")


if __name__ == "__main__":
    main()
