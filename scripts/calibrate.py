#!/usr/bin/env python3
"""Score tester models against a seeded-bug app: recall + precision per family/model.
A cluster 'hits' a seeded bug when any member finding carries that bug's rule_id.

  calibrate.py --seeded tests/demo_app/seeded_bugs.json [--root D]
"""
import argparse
import os
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from uatlib import finding_files, jload, udir  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeded", required=True)
    ap.add_argument("--root", default=os.getcwd())
    a = ap.parse_args()
    r = Path(a.root).resolve()
    seeded = {b["rule_id"] for b in jload(a.seeded, {"bugs": []})["bugs"]}
    stats = defaultdict(lambda: {"found": set(), "total": 0, "true": 0})
    for f in finding_files(r):
        o = jload(f, {})
        rp = jload(f.with_suffix(".replay.json"), {}).get("status")
        s = stats[(o.get("family"), o.get("model"))]
        s["total"] += 1
        if o.get("rule_id") in seeded and rp == "confirmed":
            s["true"] += 1
            s["found"].add(o["rule_id"])
    print(f"{'family':9} {'model':38} recall precision  (seeded={len(seeded)})")
    for (fam, mod), s in sorted(stats.items(), key=lambda kv: str(kv[0])):
        print(f"{fam:9} {mod:38} {len(s['found'])}/{len(seeded)}   {s['true']}/{s['total']}")


if __name__ == "__main__":
    main()
