#!/usr/bin/env python3
"""The agent team: ONE orchestrator and at least one worker. Workers may share a harness/LLM with the orchestrator or not.

  team.py show   [--root D]                     current team, any pending proposal, what can be chosen
  team.py set    --file team.json [--ping]      validate and apply a team (used by /uat-full after the user agreed in chat)
  team.py apply  [--ping]                       apply the proposal the dashboard saved (.uat/team.pending.json), after the user agreed in chat
  team.py discard                               drop the pending proposal
  team.py testers                               'family:model,...' for swarm.py --agents
  team.py token                                 print the dashboard's one-time edit token (never put it in chat)

Team file (.uat/team.json):
  {"orchestrator": {"name": "Elephant", "family": "claude", "model": "opus"},
   "workers": [{"family": "claude", "model": "haiku", "roles": ["tester"]},
               {"family": "codex",  "model": "gpt-6.1-terra", "roles": ["verifier", "fixer"]}]}

Safety: names and models must match a strict pattern and cannot start with '-' (they end up as command-line arguments); families must exist
in the harness registry; the dashboard may only propose models already listed in the config, and a proposal never takes effect by itself.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import harness  # noqa: E402
from uatlib import HOME, audit, config, jload, udir  # noqa: E402

ROLES = ("tester", "verifier", "fixer")
NAME = re.compile(r"[A-Za-z][A-Za-z0-9 _.-]{0,39}")
FAMILY = re.compile(r"[a-z][a-z0-9_-]{0,29}")
MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,79}")
MAX_WORKERS = 12


def known_models(cfg):
    """{family: sorted models listed in the config}. This is the allowlist the dashboard may choose from."""
    out = {}
    for f in harness.registry(cfg):
        m = (cfg.get("models") or {}).get(f) or {}
        out[f] = sorted({x for t in ("cheap", "mid") for x in (m.get(t) or []) if isinstance(x, str) and MODEL.fullmatch(x)})
    return out


def validate(team, cfg, strict_models=False):
    """-> clean team dict or raises ValueError with a sentence a person can read. Never trusts types or lengths."""
    if not isinstance(team, dict) or set(team) - {"orchestrator", "workers"}:
        raise ValueError("team must have only 'orchestrator' and 'workers'")
    fams, known = harness.registry(cfg), known_models(cfg)

    def one(x, what):
        if not isinstance(x, dict):
            raise ValueError(f"{what} must be an object")
        f, m = x.get("family"), x.get("model")
        if not isinstance(f, str) or not FAMILY.fullmatch(f) or f not in fams:
            raise ValueError(f"{what}: unknown harness {str(f)[:30]!r}. Known: {', '.join(sorted(fams))}")
        if not isinstance(m, str) or not MODEL.fullmatch(m):
            raise ValueError(f"{what}: model name is empty or has characters that are not allowed")
        if strict_models and m not in known[f]:
            raise ValueError(f"{what}: model {m!r} is not in the configured list for {f}. Add it with /uat-full reconfigure first.")
        return f, m

    o = team.get("orchestrator")
    of, om = one(o, "orchestrator")
    name = o.get("name", "Elephant")
    if not isinstance(name, str) or not NAME.fullmatch(name):
        raise ValueError("orchestrator name: 1-40 letters, digits, space, dot, dash or underscore, starting with a letter")
    ws = team.get("workers")
    if not isinstance(ws, list) or not 1 <= len(ws) <= MAX_WORKERS:
        raise ValueError(f"need between 1 and {MAX_WORKERS} workers")
    clean, seen = [], set()
    for i, w in enumerate(ws, 1):
        f, m = one(w, f"worker {i}")
        roles = w.get("roles")
        if not isinstance(roles, list) or not roles or len(set(roles)) != len(roles) or any(r not in ROLES for r in roles):
            raise ValueError(f"worker {i}: roles must be a non-empty list of {', '.join(ROLES)}")
        if (f, m) in seen:
            raise ValueError(f"worker {i}: {f}/{m} is listed twice; give it several roles instead")
        seen.add((f, m))
        clean.append({"family": f, "model": m, "roles": [r for r in ROLES if r in roles]})
    if not any("tester" in w["roles"] for w in clean):
        raise ValueError("at least one worker must be a tester")
    return {"orchestrator": {"name": name, "family": of, "model": om}, "workers": clean}


def path(r, pending=False):
    return udir(r) / ("team.pending.json" if pending else "team.json")


def current(r):
    return jload(path(r), None)


def testers(team):
    return ",".join(f"{w['family']}:{w['model']}" for w in team["workers"] if "tester" in w["roles"])


def ping(r, f, m):
    p = subprocess.run([sys.executable, str(HOME / "scripts" / "uat.py"), "run_agent", "--ping", "--family", f, "--model", m, "--root", str(r)], capture_output=True, text=True, timeout=200)
    return p.returncode == 0


def apply(r, team, actor, do_ping=False, strict=False):
    cfg = config(r)
    t = validate(team, cfg, strict_models=strict)
    bad = []
    if do_ping:
        for f, m in sorted({(w["family"], w["model"]) for w in t["workers"]}):
            if not ping(r, f, m):
                bad.append(f"{f}/{m}")
    if bad:
        raise ValueError("these workers did not answer the test question, so the team was not changed: " + ", ".join(bad))
    u = udir(r)
    mine = jload(u / "uat.json", {})
    models = mine.setdefault("models", {})
    for w in t["workers"]:                              # only ever add to the allowlist, never shrink it
        tier = models.setdefault(w["family"], {k: list((cfg.get("models") or {}).get(w["family"], {}).get(k, [])) for k in ("cheap", "mid")})
        lst = tier.setdefault("cheap" if "tester" in w["roles"] else "mid", [])
        if w["model"] not in lst:
            lst.append(w["model"])
    mine.setdefault("swarm", {})["families"] = sorted({w["family"] for w in t["workers"] if "tester" in w["roles"]})
    for p, data in ((u / "uat.json", mine), (path(r), t), (u / "orchestrator.json", {"name": t["orchestrator"]["name"], "model": t["orchestrator"]["model"], "family": t["orchestrator"]["family"]})):
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=1, sort_keys=True))
        os.replace(tmp, p)
    path(r, True).unlink(missing_ok=True)
    audit(r, actor, "team_applied", orchestrator=f"{t['orchestrator']['family']}/{t['orchestrator']['model']}", workers=[f"{w['family']}/{w['model']}:{'+'.join(w['roles'])}" for w in t["workers"]], pinged=do_ping)
    return t


def options(r):
    cfg = config(r)
    models = known_models(cfg)
    return [{"family": h["name"], "installed": h["installed"], "verified": h["verified"], "models": models.get(h["name"], [])} for h in harness.listing(cfg)]


def view(r):
    """Everything the dashboard's Team page shows. Read-only."""
    return {"team": current(r), "pending": jload(path(r, True), None), "options": options(r), "roles": list(ROLES), "max_workers": MAX_WORKERS}


def token_path(r):
    from uatlib import _anchor
    return _anchor(r).with_suffix(".dashtoken")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["show", "set", "apply", "discard", "testers", "token"])
    ap.add_argument("--root", default=os.getcwd())
    ap.add_argument("--file")
    ap.add_argument("--ping", action="store_true")
    a = ap.parse_args()
    r = Path(a.root).resolve()
    try:
        if a.cmd == "show":
            print(json.dumps(view(r), indent=1))
        elif a.cmd == "testers":
            t = current(r) or sys.exit("no team yet: run /uat-full")
            print(testers(t))
        elif a.cmd == "token":
            print(token_path(r).read_text().strip() if token_path(r).exists() else sys.exit("the dashboard is not running (start it with /uat dashboard)"))
        elif a.cmd == "discard":
            path(r, True).unlink(missing_ok=True)
            audit(r, "user", "team_proposal_discarded")
            print("discarded")
        else:
            src = Path(a.file) if a.cmd == "set" else path(r, True)
            if a.cmd == "set" and not a.file:
                sys.exit("--file is required")
            t = apply(r, json.loads(src.read_text()), "orchestrator", a.ping, strict=a.cmd == "apply")
            print(f"team applied: {len(t['workers'])} worker(s), testers {testers(t)}")
    except (ValueError, OSError, json.JSONDecodeError) as e:
        sys.exit(f"team not changed: {e}")


if __name__ == "__main__":
    main()
