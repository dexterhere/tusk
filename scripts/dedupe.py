#!/usr/bin/env python3
"""Cluster findings by root cause (script, no LLM) and decide what needs a verifier.

Two findings with DIFFERENT rule_ids are never merged (unless their repro command is identical): distinct rules are distinct bugs
even when the wording or the code location is close. Otherwise, strongest first:
  strong  same rule_id + area | same repro command | same suspect.file within 25 lines and same category
  weak    title token overlap >= 0.8, same area and category; never chains clusters that already carry different rules
Each cluster records `merge_reasons` so a wrong merge is auditable.

Verdicts: accept (replay confirmed) | intermittent (flaky concurrency/nondeterministic: kept, reported with its fail rate)
| watch (single-agent concurrency report that did not replay: kept as unconfirmed, never silently dropped)
| needs_verifier | drop (single agent, not reproducible, not concurrency; tamper) | unreplayed.

  dedupe.py [--root D]   -> .uat/clusters.json
"""
import argparse
import hashlib
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from uatlib import SEV, config, emit, finding_files, jdump, jload, udir  # noqa: E402

STOP = {"the", "a", "an", "is", "are", "of", "to", "in", "on", "for", "and", "not", "with", "when", "no", "returns", "does"}
WEAK = 0.8


def toks(s):
    return {w for w in re.findall(r"[a-z0-9]+", s.lower()) if w not in STOP}


def norm_cmd(c):
    return re.sub(r"\s+", " ", c.strip())


def link(a, b):
    """'strong' | 'weak' | '' for one pair, or 'blocked' when their rule_ids differ."""
    ra, rb = a.get("rule_id"), b.get("rule_id")
    if norm_cmd(a["repro"]["cmd"]) == norm_cmd(b["repro"]["cmd"]):
        return "strong:same repro"
    if ra and rb and ra != rb:
        return "blocked"
    if ra and ra == rb and a["area"] == b["area"]:
        return "strong:same rule"
    sa, sb = a.get("suspect", {}), b.get("suspect", {})
    if sa.get("file") and sa["file"] == sb.get("file") and abs(sa.get("line", 0) - sb.get("line", 0)) <= 25 and a["category"] == b["category"]:
        return "strong:same code location"
    ta, tb = toks(a["title"]), toks(b["title"])
    if a["area"] == b["area"] and a["category"] == b["category"] and ta and tb and len(ta & tb) / len(ta | tb) >= WEAK:
        return "weak:similar title"
    return ""


def best_status(sts):
    if sts == {"tamper"}:
        return "tamper"
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
            obj["_rate"] = rp.get("fail_rate", 0)
            obj["_file"] = str(f.relative_to(r))
            items.append(obj)
    parent = list(range(len(items)))
    reasons = {i: set() for i in range(len(items))}

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def rules(root_i):
        return {items[k]["rule_id"] for k in range(len(items)) if find(k) == root_i and items[k].get("rule_id")}

    pairs = [(i, j, link(items[i], items[j])) for i in range(len(items)) for j in range(i + 1, len(items))]
    for kind in ("strong", "weak"):             # all strong merges first, so weak ones see the final rule sets
        for i, j, l in pairs:
            if l.startswith(kind) and find(i) != find(j) and len(rules(find(i)) | rules(find(j))) <= 1:
                reasons[find(j)] |= reasons[find(i)] | {l.split(":")[1]}
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
        race = any(x["category"] == "concurrency" or x.get("nondeterministic") for x in g)
        if status == "confirmed":
            verdict = "accept"
        elif status == "flaky" and race:
            verdict = "intermittent"
        elif status in ("flaky", "error") or (status == "not_reproducible" and len(agents) >= 2):
            verdict = "needs_verifier"
        elif status == "not_reproducible" and race:
            verdict = "watch"
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
            "merge_reasons": sorted(reasons[find(items.index(lead))]), "fail_rate": max(x["_rate"] for x in g), "kind": lead.get("kind", "defect"),
        })
    out.sort(key=lambda c: (SEV[c["severity"]], -c["agreement"]))
    jdump(udir(r) / "clusters.json", out)
    tally = {}
    for c in out:
        tally[c["verdict"]] = tally.get(c["verdict"], 0) + 1
    emit(r, "orchestrator", "clusters", total=len(out), tally=tally)
    print(f"clusters={len(out)} {tally}")


if __name__ == "__main__":
    main()
