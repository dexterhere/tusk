"""Shared helpers for uat-swarm scripts. Stdlib only."""
import hashlib
import json
import os
import re
import shutil
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
    if isinstance(obj, list) and len(obj) > schema.get("maxItems", 10**9):
        errs.append(f"{path}: more than {schema['maxItems']} items")
    if isinstance(obj, list) and "items" in schema:
        for i, v in enumerate(obj):
            errs += validate(v, schema["items"], f"{path}[{i}]")
    return errs


def finding_schema():
    return jload(HOME / "resources" / "finding-schema.json", {})


def finding_files(r):
    """Finding files at .uat/agents/<id>/findings/F-*.json. Symlinked dirs/files and anything resolving outside the agent dir are ignored
    (the agent controls its own dir, so it could point them anywhere)."""
    out = []
    for p in sorted((Path(r) / ".uat" / "agents").glob("*/findings/F-*.json")):
        agent_dir = p.parent.parent
        if p.parent.is_symlink() or p.is_symlink() or not p.resolve().is_relative_to(agent_dir.resolve()):
            continue
        out.append(p)
    return out


def replay_path(r, finding_file):
    """Replay verdicts live OUTSIDE the agent-writable dir, so a tester can never forge or overwrite them."""
    f = Path(finding_file)
    return udir(r) / "replays" / f.parent.parent.name / (f.stem + ".replay.json")


# ---------------------------------------------------------------- reading files an agent can write
MAX_BYTES = 1_000_000
CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def clean(text, n=300):
    """Printable, single-line, bounded: for anything an agent wrote that we display."""
    return CTRL.sub("", str(text)).replace("\n", " ").replace("\r", " ")[:n]


def safe_read(path, limit=MAX_BYTES, tail=False):
    """Read a regular, non-symlink file with a size cap. Returns '' otherwise. Never raises."""
    try:
        p = Path(path)
        if p.is_symlink() or not p.is_file():
            return ""
        with open(p, "rb") as f:
            if tail:
                f.seek(max(0, p.stat().st_size - limit))
            return f.read(limit).decode("utf-8", "replace")
    except OSError:
        return ""


def safe_json(path, default=None):
    try:
        return json.loads(safe_read(path))
    except ValueError:
        return default


def meta_path(r, agent):
    """Agent label (family/model/lens/charter). Written by the orchestrator OUTSIDE the agent-writable dir."""
    return udir(r) / "meta" / f"{agent}.json"


def agent_meta(r, agent):
    m = jload(meta_path(r, agent), {})
    return {k: clean(m.get(k, ""), 60) for k in ("id", "family", "model", "lane", "charter", "area", "lens", "role")}


# ---------------------------------------------------------------- events (feed the live dashboard)
def emit(r, actor, type_, **data):
    """Append one event. One O_APPEND write per line. Host-side scripts write events.host.jsonl (UAT_EVENTS_FILE) so the SSH mirror
    never clobbers the orchestrator's own events.jsonl."""
    line = json.dumps({"ts": round(time.time(), 2), "actor": actor, "type": type_, **data}, separators=(",", ":")) + "\n"
    fd = os.open(udir(r) / os.environ.get("UAT_EVENTS_FILE", "events.jsonl"), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        os.write(fd, line.encode())
    finally:
        os.close(fd)


AGENT_EVENTS = {"step", "check"}


def read_events(r, since=0):
    """Orchestrator/host events plus each agent's own events.jsonl. Agent files are untrusted: only step/check records, actor forced to the
    dir name, timestamps clamped to now, every field a bounded string, size-capped, never raises."""
    now = time.time()
    out = []
    sources = [(p, None) for p in sorted(udir(r).glob("events*.jsonl"))] + [(p, p.parent.name) for p in sorted((udir(r) / "agents").glob("*/events.jsonl"))]
    for path, actor in sources:
        for ln in safe_read(path, tail=True).splitlines()[-2000:]:
            try:
                e = json.loads(ln)
            except ValueError:
                continue
            if not isinstance(e, dict) or not isinstance(e.get("ts"), (int, float)) or isinstance(e.get("ts"), bool):
                continue
            if actor:
                if e.get("type") not in AGENT_EVENTS:
                    continue
                e = {"ts": min(float(e["ts"]), now), "actor": actor, "type": e["type"], **{k: clean(e.get(k, ""), 200) for k in ("text", "rule", "result", "note")}}
            elif not isinstance(e.get("actor"), str) or not isinstance(e.get("type"), str):
                continue
            if e["ts"] > since:
                out.append(e)
    return sorted(out, key=lambda e: e["ts"])


# ---------------------------------------------------------------- trusted snapshots of findings
def sha(data):
    return hashlib.sha256(data if isinstance(data, bytes) else data.encode()).hexdigest()


def trusted_dir(r, finding_file):
    f = Path(finding_file)
    return udir(r) / "trusted" / f.parent.parent.name / f.stem


def snapshot_finding(r, f):
    """Copy a finding and its declared repro files out of the agent-writable dir, once, before anything runs.
    Everything downstream (replay, dedupe, report, fix queue) uses this copy and its digest, never the live agent dir.
    Returns (finding_dict, digest) or (None, '') when unusable."""
    f = Path(f)
    raw = safe_read(f)
    try:
        obj = json.loads(raw)
    except ValueError:
        return None, ""
    t = trusted_dir(r, f)
    shutil.rmtree(t, ignore_errors=True)
    (t / "files").mkdir(parents=True)
    h = hashlib.sha256(raw.encode())
    agent_dir = f.parent.parent.resolve()
    for rel in (obj.get("repro", {}).get("files") or [])[:20]:
        src, dst = agent_dir / rel, t / "files" / rel
        if ".." in Path(rel).parts or Path(rel).is_absolute() or not src.is_file() or src.is_symlink() or not src.resolve().is_relative_to(agent_dir):
            continue
        if src.stat().st_size > 5_000_000:
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst, follow_symlinks=False)
        h.update(rel.encode() + dst.read_bytes())
    digest = h.hexdigest()
    (t / "finding.json").write_text(raw)
    (t / "digest").write_text(digest)
    return obj, digest


def trusted(r, f):
    """(finding, digest) from the snapshot taken before replay, or (None, '') if there is none."""
    t = trusted_dir(r, f)
    return jload(t / "finding.json"), (t / "digest").read_text() if (t / "digest").exists() else ""


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
