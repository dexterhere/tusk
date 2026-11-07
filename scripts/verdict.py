#!/usr/bin/env python3
"""Record a verifier verdict for a disputed cluster (and audit it). The only supported way to write .tusk/verdicts.json.

  verdict.py CID accept|reject "one sentence why" [--by verifier-1 --family claude --model sonnet] [--root D]
"""
import argparse
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tusklib import clean, emit, jdump, jload, udir  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cid")
    ap.add_argument("verdict", choices=["accept", "reject"])
    ap.add_argument("note", nargs="?", default="")
    ap.add_argument("--by", default="verifier")
    ap.add_argument("--family", default="", help="agent family of the verifier (claude, codex, ...)")
    ap.add_argument("--model", default="", help="model id of the verifier")
    ap.add_argument("--digest", required=True, help="the cluster's evidence digest as shown to the verifier (clusters.json 'digest')")
    ap.add_argument("--root", default=os.getcwd())
    a = ap.parse_args()
    r = Path(a.root).resolve()
    if not re.fullmatch(r"C-[0-9a-f]{12}", a.cid) or a.cid not in {c["cid"] for c in jload(udir(r) / "clusters.json", [])}:
        sys.exit(f"unknown cluster {a.cid}")
    v = jload(udir(r) / "verdicts.json", {})
    cl = next(c for c in jload(udir(r) / "clusters.json", []) if c["cid"] == a.cid)
    if not cl.get("digest") or a.digest != cl["digest"]:
        sys.exit(f"{a.cid}: the evidence changed since you reviewed it (digest {a.digest[:16]!r} is not the current one). Review it again and use the new digest.")
    v[a.cid] = {"digest": a.digest, "verdict": a.verdict, "note": clean(a.note, 300), "by": clean(a.by, 40), "family": clean(a.family, 30), "model": clean(a.model, 60), "ts": round(__import__("time").time())}
    jdump(udir(r) / "verdicts.json", v)
    emit(r, clean(a.by, 40), "verdict", cid=a.cid, verdict=a.verdict, note=clean(a.note, 300), family=clean(a.family, 30), model=clean(a.model, 60))
    print(f"{a.cid}: {a.verdict}")


if __name__ == "__main__":
    main()
