#!/usr/bin/env python3
"""Scan a repo: size, stack tags, areas, applicable tools, recommended swarm size.
Writes <root>/.tusk/detect.json and prints it. Stdlib only.

  tusk_detect.py [--root DIR]
"""
import argparse
import math
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tusklib import HOME, config, jdump, jload, udir  # noqa: E402

SKIP = {".git", ".tusk", "node_modules", "target", ".venv", "venv", "__pycache__", "dist", "build", ".next", ".gradle", "graphify-out", ".dart_tool", "Pods", ".idea", ".mypy_cache"}
SRC_EXT = {".py", ".js", ".jsx", ".ts", ".tsx", ".rs", ".go", ".java", ".kt", ".rb", ".php", ".dart", ".swift", ".c", ".cc", ".cpp", ".cs", ".vue", ".svelte", ".html", ".sql"}
MARKERS = {"package.json": "node", "pyproject.toml": "python", "requirements.txt": "python", "setup.py": "python", "Cargo.toml": "rust", "go.mod": "go", "pubspec.yaml": "flutter", "pom.xml": "java", "build.gradle": "java", "Gemfile": "ruby", "composer.json": "php", "Dockerfile": "docker", "docker-compose.yml": "docker", "docker-compose.yaml": "docker", "compose.yaml": "docker"}
UI_HINTS = ("react", "vue", "svelte", "next", "angular", "solid-js", "playwright", "vite")
API_HINTS = ("express", "fastify", "koa", "nestjs", "fastapi", "flask", "django", "axum", "actix", "rocket", "gin-gonic", "echo", "spring-boot", "rails", "laravel", "hono")
AWS_HINTS = ("boto3", "aws-sdk", "@aws-sdk", "aws-cdk")


def scan(root):
    files, size, tags, areas, hay = 0, 0, set(), {}, []
    for dp, dns, fns in os.walk(root):
        dns[:] = [d for d in dns if d not in SKIP]
        rel = Path(dp).relative_to(root)
        for fn in fns:
            p = Path(dp) / fn
            if fn in MARKERS:
                tags.add(MARKERS[fn])
                if fn in ("package.json", "pyproject.toml", "requirements.txt", "Cargo.toml", "go.mod", "pubspec.yaml", "Gemfile", "pom.xml"):
                    try:
                        hay.append(p.read_text(errors="ignore")[:60000].lower())
                    except OSError:
                        pass
            if fn.startswith(("openapi", "swagger")) and fn.endswith((".json", ".yaml", ".yml")):
                tags.update(["openapi", "api"])
            if fn.endswith((".graphql", ".gql")):
                tags.add("graphql")
            ext = p.suffix.lower()
            if ext in SRC_EXT:
                files += 1
                try:
                    size += p.stat().st_size
                except OSError:
                    pass
                if ext in (".html", ".vue", ".svelte", ".jsx", ".tsx"):
                    tags.add("web-ui")
                top = rel.parts[0] if rel.parts else "."
                if top in ("src", "apps", "packages", "services", "crates", "lib", "cmd") and len(rel.parts) > 1:
                    top = f"{top}/{rel.parts[1]}"
                areas[top] = areas.get(top, 0) + 1
    blob = "\n".join(hay)
    if any(h in blob for h in UI_HINTS):
        tags.add("web-ui")
    if any(h in blob for h in API_HINTS):
        tags.add("api")
    if any(h in blob for h in AWS_HINTS):
        tags.add("aws")
    if "psycopg" in blob or "postgres" in blob or "pg" in blob.split():
        tags.add("postgres")
    return files, size, tags, areas


def recommend(files, areas, cfg):
    sw = cfg["swarm"]
    by_size = 3 if files < 200 else 6 if files < 1000 else 9 if files < 5000 else 12
    n = max(by_size, min(len(areas), sw["max_agents"]))
    n = math.ceil(n / len(sw["families"])) * len(sw["families"])  # balance families
    n = max(sw["min_agents"], min(n, sw["max_agents"]))
    mem_gb = 4.0
    try:
        for line in open("/proc/meminfo"):
            if line.startswith("MemAvailable"):
                mem_gb = int(line.split()[1]) / 1048576
    except OSError:
        pass
    par = max(1, min(sw["max_parallel"], int(mem_gb // 2.5)))
    return n, par, round(mem_gb, 1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=os.getcwd())
    a = ap.parse_args()
    r = Path(a.root).resolve()
    cfg = config(r)
    files, size, tags, areas = scan(r)
    n, par, mem = recommend(files, areas, cfg)
    catalog = jload(HOME / "resources" / "tool-catalog.json", {"tools": []})["tools"]
    tools = []
    for t in catalog:
        if "*" in t["when"] or tags & set(t["when"]):
            tools.append({"name": t["name"], "kind": t["kind"], "method": t["install"]["method"], "optional": t.get("optional", False), "installed": installed(t, r)})
    out = {
        "root": str(r), "source_files": files, "source_mb": round(size / 1e6, 2), "tags": sorted(tags),
        "areas": dict(sorted(areas.items(), key=lambda kv: -kv[1])[:30]),
        "recommended_agents": n, "recommended_parallel": par, "mem_available_gb": mem,
        "disk_free_gb": round(shutil.disk_usage(r).free / 1e9, 1), "tools": tools,
    }
    jdump(udir(r) / "detect.json", out)
    print(__import__("json").dumps(out, indent=2))


def installed(t, r):
    m = t["install"]["method"]
    if m == "builtin":
        return bool(shutil.which(t["check"].split()[0]))
    tools = Path(r) / ".tusk" / "tools"
    first = t["check"].split()[0]
    if m in ("pip", "npm", "cargo"):
        return any((tools / sub / first).exists() for sub in ("venv/bin", "node_modules/.bin", "bin")) or bool(shutil.which(first))
    if m == "docker":
        return docker_has(t["install"]["spec"])
    return bool(shutil.which(first)) or ok(shlex.split(t["check"]))


def docker_has(image):
    if not shutil.which("docker"):
        return False
    return ok(["docker", "image", "inspect", image])


def ok(argv):
    """Run a catalog-defined check command (no shell); True on exit 0."""
    try:
        return subprocess.run(argv, capture_output=True, timeout=20).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


if __name__ == "__main__":
    main()
