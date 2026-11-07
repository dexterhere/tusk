#!/usr/bin/env python3
"""Cluster findings by root cause (script, no LLM) and decide what needs a verifier.

Cluster keys, strongest first: same rule_id+area | same suspect.file within 25 lines | title token overlap >= 0.6 in same area.
Verifier needed only when replay is flaky/error, or >=2 agents saw it but replay can't reproduce.
Single-agent not_reproducible = dropped as false positive.

  dedupe.py [--root D]   -> .uat/clusters.json
"""
import argparse
import hashlib
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from uatlib import SEV, config, finding_files, jdump, jload, udir  # noqa: E402

STOP = {"the", "a", "an", "is", "are", "of", "to", "in", "on", "for", "and", "not", "with", "when", "no", "returns", "does"}


def toks(s):
    return {w for w in re.findall(r"[a-z0-9]+", s.lower()) if w not in STOP}


def same(a, b):
    if a["area"] != b["area"] and not (a.get("suspect", {}).get("file") and a["suspect"].get("file") == b.get("suspect", {}).get("file")):
        return False
    if a.get("rule_id") and a.get("rule_id") == b.get("rule_id") and a["area"] == b["area"]:
        return True
    sa, sb = a.get("suspect", {}), b.get("suspect", {})
    if sa.get("file") and sa.get("file") == sb.get("file") and abs(sa.get("line", 0) - sb.get("line", 0)) <= 25:
        return True
    ta, tb = toks(a["title"]), toks(b["title"])
    return bool(ta and tb) and len(ta & tb) / len(ta | tb) >= 0.6


def best_status(sts):
    for s in ("confirmed", "flaky", "error", "not_reproducible"):
        if s in sts:
            return s
    return "unreplayed"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=os.getcwd())
    r = Path(ap.parse_args().root).resolve()
    cfg = config(r)
    items = []
    for f in finding_files(r):
        obj = jload(f)
        rp = jload(f.with_suffix(".replay.json"), {})
        if obj:
            obj["_replay"] = rp.get("status", "unreplayed")
            obj["_file"] = str(f.relative_to(r))
            items.append(obj)
    parent = list(range(len(items)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            if same(items[i], items[j]):
                parent[find(i)] = find(j)
    groups = {}
    for i, it in enumerate(items):
        groups.setdefault(find(i), []).append(it)
    out = []
    for g in groups.values():
        g.sort(key=lambda x: SEV[x["severity"]])
        lead = g[0]
        agents = sorted({x["agent"] for x in g})
        fams = sorted({x["family"] for x in g})
        status = best_status({x["_replay"] for x in g})
        if status == "confirmed":
            verdict = "accept"
        elif status in ("flaky", "error") or (status == "not_reproducible" and len(agents) >= 2):
            verdict = "needs_verifier"
        elif status == "unreplayed":
            verdict = "unreplayed"
        else:
            verdict = "drop"
        key = lead.get("rule_id") or (lead.get("suspect", {}).get("file")) or " ".join(sorted(toks(lead["title"]))[:6])
        fp = hashlib.sha1(f"{lead['area']}|{key}".encode()).hexdigest()[:10]
        out.append({
            "cid": "C-" + fp[:6], "fp": fp, "title": lead["title"], "severity": lead["severity"], "category": lead["category"],
            "area": lead["area"], "rule_id": lead.get("rule_id", ""), "members": [x["id"] for x in g], "files": [x["_file"] for x in g],
            "agents": agents, "families": fams, "agreement": len(agents), "replay": status, "verdict": verdict,
            "lenses": sorted({x["lens"] for x in g}),
        })
    out.sort(key=lambda c: (SEV[c["severity"]], -c["agreement"]))
    jdump(udir(r) / "clusters.json", out)
    tally = {}
    for c in out:
        tally[c["verdict"]] = tally.get(c["verdict"], 0) + 1
    print(f"clusters={len(out)} {tally}")


if __name__ == "__main__":
    main()
