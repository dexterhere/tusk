#!/usr/bin/env python3
"""audit.py verify|reset [--root .]  verify: check the chain and its off-project anchor. reset: forget the anchor after you deliberately
deleted .tusk (a wiped log otherwise reads as tampering). Reset is a human decision; nothing in the swarm calls it."""
import argparse
import sys
from pathlib import Path

import tusklib

ap = argparse.ArgumentParser()
ap.add_argument("cmd", choices=["verify", "reset"])
ap.add_argument("--root", default=".")
a = ap.parse_args()
r = Path(a.root).resolve()
if a.cmd == "verify":
    ok, n, head, bad = tusklib.audit_verify(r)
    print(f"{'ok' if ok else 'FAILED'}: {n} records, head {head[:16]}" + ("" if ok else f", bad_seq {bad} (0 = no anchor)"))
    sys.exit(0 if ok else 1)
anc = tusklib._anchor(r)
for p in (anc, anc.with_suffix(".tamper"), anc.with_suffix(".tmp")):
    p.unlink(missing_ok=True)
print(f"anchor cleared: {anc}")
