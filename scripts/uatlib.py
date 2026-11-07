"""Shared helpers for uat-swarm scripts. Stdlib only."""
import json
import os
import re
import time
from pathlib import Path

HOME = Path(os.environ.get("UAT_HOME") or Path(__file__).resolve().parent.parent)
SEV = {"blocker": 0, "major": 1, "minor": 2, "nit": 3}
TYPES = {"string": str, "integer": int, "boolean": bool, "object": dict, "array": list, "number": (int, float)}


def root(arg=None):
    return Path(arg or os.getcwd()).resolve()


def udir(r):
    d = Path(r) / ".uat"
    d.mkdir(exist_ok=True)
    return d


def jload(p, default=None):
    try:
        return json.loads(Path(p).read_text())
    except (OSError, ValueError):
        return default


def jdump(p, obj):
    Path(p).parent.mkdir(parents=True, exist_ok=True)
    Path(p).write_text(json.dumps(obj, indent=2) + "\n")


def deep_merge(a, b):
    out = dict(a)
    for k, v in b.items():
        out[k] = deep_merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def config(r):
    """Defaults overlaid with the project's .uat/uat.json."""
    return deep_merge(jload(HOME / "uat.defaults.json", {}), jload(Path(r) / ".uat" / "uat.json", {}))


def validate(obj, schema, path="$"):
    """Tiny JSON-schema subset: type, required, enum, minLength, maxLength, properties, items."""
    errs = []
    t = schema.get("type")
    if t:
        ok = isinstance(obj, TYPES[t]) and not (t in ("integer", "number") and isinstance(obj, bool))
        if not ok:
            return [f"{path}: expected {t}"]
    if "enum" in schema and obj not in schema["enum"]:
        errs.append(f"{path}: {obj!r} not in {schema['enum']}")
    if isinstance(obj, str):
        if len(obj) < schema.get("minLength", 0):
            errs.append(f"{path}: shorter than {schema['minLength']}")
        if len(obj) > schema.get("maxLength", 10**9):
            errs.append(f"{path}: longer than {schema['maxLength']}")
    if isinstance(obj, dict):
        for k in schema.get("required", []):
            if k not in obj:
                errs.append(f"{path}.{k}: required")
        for k, sub in schema.get("properties", {}).items():
            if k in obj:
                errs += validate(obj[k], sub, f"{path}.{k}")
    if isinstance(obj, list) and "items" in schema:
        for i, v in enumerate(obj):
            errs += validate(v, schema["items"], f"{path}[{i}]")
    return errs


def finding_schema():
    return jload(HOME / "resources" / "finding-schema.json", {})


def finding_files(r):
    """Valid-location finding files: .uat/agents/<id>/findings/F-*.json"""
    return sorted(p for p in (Path(r) / ".uat" / "agents").glob("*/findings/F-*.json") if not p.name.endswith(".replay.json"))


# ---------------------------------------------------------------- events (feed the live dashboard)
def emit(r, actor, type_, **data):
    """Append one event to .uat/events.jsonl. One O_APPEND write per line, so parallel writers do not interleave."""
    line = json.dumps({"ts": round(time.time(), 2), "actor": actor, "type": type_, **data}, separators=(",", ":")) + "\n"
    fd = os.open(udir(r) / "events.jsonl", os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        os.write(fd, line.encode())
    finally:
        os.close(fd)


def read_events(r, since=0):
    """Orchestrator events plus each agent's own events.jsonl (actor forced to the agent's dir name), sorted by time."""
    out = []
    sources = [(udir(r) / "events.jsonl", None)] + [(p, p.parent.name) for p in sorted((udir(r) / "agents").glob("*/events.jsonl"))]
    for path, actor in sources:
        try:
            lines = path.read_text(errors="ignore").splitlines()[-2000:]
        except OSError:
            continue
        for ln in lines:
            try:
                e = json.loads(ln)
            except ValueError:
                continue
            if actor:
                e["actor"] = actor
            if e.get("ts", 0) > since:
                out.append(e)
    return sorted(out, key=lambda e: e["ts"])


# ---------------------------------------------------------------- lanes and host layout
def lane_names(cfg):
    return [f"L{i}" for i in range(1, int(cfg["swarm"]["max_parallel"]) + 1)]


def lane_port(cfg, lane):
    return int(cfg["env"]["base_port"]) + int(lane[1:]) - 1


def lane_url(cfg, lane, key="base_url"):
    """base_url / health_url of one lane: same URL with the port swapped for the lane port."""
    return re.sub(r"(://[^/:]+):\d+", lambda m: f"{m.group(1)}:{lane_port(cfg, lane)}", cfg["env"][key], count=1)


def is_remote(cfg):
    return cfg["target"]["mode"] == "ssh"


def layout(cfg, r):
    """Paths as seen by the machine that executes the tests (this one, or the SSH host)."""
    if is_remote(cfg):
        base = cfg["target"]["ssh"]["dir"].rstrip("/")
        return {"home": base + "/toolkit", "root": base, "udir": base + "/.uat"}
    return {"home": str(HOME), "root": str(Path(r)), "udir": str(udir(r))}
