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
    if "UAT_EVENTS_FILE" not in os.environ and type_ not in AUDIT_SKIP:      # host-side events (ssh) are not part of the local chain
        audit(r, actor, type_, **data)


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


# ---------------------------------------------------------------- model health (quota / rate limits)
QUOTA_RE = re.compile(r"free-?limit|FreeUsageLimit|usage limit|rate.?limit|quota (exceeded|exhausted)|\b429\b|insufficient_quota|credit balance", re.I)


def exhausted_models(r):
    """{model: until_ts} for models whose provider said 'out of quota' and are still within their retry window."""
    now = time.time()
    h = jload(udir(r) / "model_health.json", {})
    return {m: v for m, v in h.items() if isinstance(v, (int, float)) and v > now}


def mark_exhausted(r, model, retry_after_s):
    h = jload(udir(r) / "model_health.json", {})
    h[model] = round(time.time() + min(max(retry_after_s, 600), 86400))
    jdump(udir(r) / "model_health.json", h)


# ---------------------------------------------------------------- audit log (append-only, hash-chained)
AUDIT_SKIP = {"agent_status", "step", "check"}       # heartbeats and probes are live-feed noise, not audit facts


def audit(r, actor, type_, **data):
    """Append one record to .uat/audit.jsonl. Each record carries the hash of the previous one, so editing or deleting any past line is
    detectable (audit_verify). Locked, because lanes, replay and the dashboard write concurrently."""
    import fcntl
    path = udir(r) / "audit.jsonl"
    with open(path, "a+") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        f.seek(0)
        last = None
        for ln in f:
            last = ln
        a = _anchor(r)
        recs = audit_read(r) if last else []
        adopted = None
        if a.exists():                                    # also when the log is empty or deleted: anchor says records existed
            if not _anchor_ok(a, recs):                   # never advance the anchor over a log that no longer matches it
                a.with_suffix(".tamper").write_text(f"seen {time.time():.0f}\n")
        elif last:
            adopted = len(recs)                           # legacy log with no anchor: adopt it, visibly, so it is not silent
        prev = json.loads(last)["hash"] if last else "0" * 64
        seq = (json.loads(last)["seq"] + 1) if last else 1
        for t_, d_ in (([("audit_anchor_adopted", {"records": adopted})] if adopted else []) + [(type_, data)]):
            rec = {"seq": seq, "ts": round(time.time(), 2), "actor": str(actor)[:60] if t_ == type_ else "uatlib", "type": str(t_)[:40], "data": d_, "prev": prev}
            rec["hash"] = sha(prev + json.dumps({k: rec[k] for k in ("seq", "ts", "actor", "type", "data")}, sort_keys=True, separators=(",", ":")))
            f.write(json.dumps(rec, separators=(",", ":")) + "\n")
            prev, seq = rec["hash"], seq + 1
        f.flush()
        if not a.with_suffix(".tamper").exists():
            try:
                tmp = a.with_suffix(".tmp")
                tmp.write_text(f"{seq - 1} {prev}\n")
                os.replace(tmp, a)
            except OSError:
                pass


def _anchor(r):
    """Latest (seq, hash) kept OUTSIDE the project's .uat dir. An unkeyed chain cannot stop someone who can write the file from truncating
    the tail or rewriting every line; the verifier compares against this copy, so that needs write access to a second location too.
    Not a MAC: the same OS user can still change both. Detects accident and in-project tampering, not a hostile account owner."""
    d = Path(os.environ.get("UAT_STATE_DIR") or Path.home() / ".local" / "state" / "uat")
    d.mkdir(parents=True, exist_ok=True)
    return d / (sha(str(Path(r).resolve()))[:16] + ".head")


def _anchor_ok(a, recs):
    try:
        seq, head = a.read_text().split()
        return 0 < int(seq) <= len(recs) and recs[int(seq) - 1].get("hash") == head
    except (OSError, ValueError):
        return False


def audit_read(r, limit=None):
    out = []
    for ln in safe_read(udir(r) / "audit.jsonl", limit=20_000_000).splitlines():
        try:
            out.append(json.loads(ln))
        except ValueError:
            out.append({"seq": -1, "broken": True})
    return out[-limit:] if limit else out


def audit_verify(r):
    """(ok, count, head_hash, first_bad_seq)."""
    prev, n = "0" * 64, 0
    for rec in audit_read(r):
        n += 1
        want = sha(prev + json.dumps({k: rec.get(k) for k in ("seq", "ts", "actor", "type", "data")}, sort_keys=True, separators=(",", ":")))
        if rec.get("prev") != prev or rec.get("hash") != want or rec.get("seq") != n:
            return False, n, prev, n
        prev = rec["hash"]
    anchor = _anchor(r)
    if anchor.with_suffix(".tamper").exists() or (n and not anchor.exists()) or (anchor.exists() and not _anchor_ok(anchor, audit_read(r))):
        return False, n, prev, 0 if not anchor.exists() else n   # bad_seq 0 = no anchor (deleted, or log never written by this version)
    return True, n, prev, None


# ---------------------------------------------------------------- what was tested
def coverage(r):
    """One row per business rule: which charters cover it, who checked it, and the outcome.
    violated = an accepted finding cites it | passed = checked and held | failed-unconfirmed = an agent said fail but nothing replayed | untested."""
    plan = jload(udir(r) / "plan" / "plan.json", {})
    checks = {}
    for e in read_events(r):
        if e["type"] == "check" and e.get("rule"):
            checks.setdefault(e["rule"], {})[e["actor"]] = "fail" if e.get("result") == "fail" else "pass"
    clusters = jload(udir(r) / "clusters.json", [])
    out = []
    for rule in plan.get("rules", []):
        rid = rule.get("id")
        hit = [c for c in clusters if c.get("rule_id") == rid and c.get("verdict") in ("accept", "needs_verifier", "watch")]
        res = checks.get(rid, {})
        if any(c["verdict"] == "accept" for c in hit):
            st = "violated"
        elif "fail" in res.values() or hit:
            st = "failed-unconfirmed"
        elif res:
            st = "passed"
        else:
            st = "untested"
        out.append({"id": clean(rid, 40), "area": clean(rule.get("area"), 60), "statement": clean(rule.get("statement"), 300), "source": clean(rule.get("source"), 160),
                    "confidence": clean(rule.get("confidence"), 12), "status": st, "agents": {clean(a, 40): v for a, v in res.items()},
                    "charters": [c["id"] for c in plan.get("charters", []) if rid in c.get("rule_ids", [])], "clusters": [c["cid"] for c in hit]})
    return out
