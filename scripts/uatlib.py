"""Shared helpers for uat-swarm scripts. Stdlib only."""
import json
import os
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
