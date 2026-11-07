#!/usr/bin/env python3
"""Build the fix-ready report from clusters (+ optional verifier verdicts), and pin regression tests.

Inputs : .uat/clusters.json, .uat/verdicts.json  {"C-abc123": {"verdict": "accept|reject", "note": "..."}}
Outputs: .uat/report/{report.md, findings.json, junit.xml}, .uat/regression/<cid>/ (repro files + finding.json)
Baseline: .uat/baseline.json (fingerprints) -> new / still-open / fixed vs the previous saved run.

  report.py [--root D] [--save-baseline]
"""
import argparse
import os
import shutil
import sys
from pathlib import Path
from xml.sax.saxutils import escape, quoteattr

sys.path.insert(0, str(Path(__file__).resolve().parent))
from uatlib import SEV, jdump, jload, udir  # noqa: E402

PENALTY = {"blocker": 25, "major": 10, "minor": 3, "nit": 1}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=os.getcwd())
    ap.add_argument("--save-baseline", action="store_true")
    a = ap.parse_args()
    r = Path(a.root).resolve()
    u = udir(r)
    clusters = jload(u / "clusters.json", [])
    verdicts = jload(u / "verdicts.json", {})
    acc, rej = [], []
    for c in clusters:
        v = verdicts.get(c["cid"], {}).get("verdict") or c["verdict"]
        (acc if v in ("accept", "intermittent") else rej).append({**c, "final": v, "note": verdicts.get(c["cid"], {}).get("note", "")})
    items = []
    for c in acc:
        lead = jload(r / c["files"][0], {})
        agent_dir = (r / c["files"][0]).parent.parent
        reg = u / "regression" / c["cid"]
        reg.mkdir(parents=True, exist_ok=True)
        for rel in lead.get("repro", {}).get("files", []):
            src, dst = (agent_dir / rel), (reg / rel)
            inside = lambda p, base: p.resolve().is_relative_to(base.resolve())  # noqa: E731
            if src.is_file() and not src.is_symlink() and inside(src, agent_dir) and inside(dst, reg):
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst, follow_symlinks=False)
        jdump(reg / "finding.json", lead)
        items.append({
            "cid": c["cid"], "fp": c["fp"], "title": c["title"], "severity": c["severity"], "category": c["category"], "area": c["area"],
            "rule_id": c["rule_id"], "confidence": {"agents": c["agreement"], "families": c["families"], "replay": c["replay"], "fail_rate": c["fail_rate"], "intermittent": c["final"] == "intermittent"}, "kind": c["kind"],
            "expected": lead.get("expected"), "actual": lead.get("actual"), "repro": lead.get("repro"), "repro_dir": str(agent_dir.relative_to(r)),
            "regression_dir": str(reg.relative_to(r)), "evidence": lead.get("evidence"), "suspect": lead.get("suspect"),
            "acceptance": f"cd {agent_dir.relative_to(r)} && {lead.get('repro', {}).get('cmd')}   # must exit 0 after the fix",
            "note": c["note"],
        })
    base = jload(u / "baseline.json", None)
    cur = {i["fp"] for i in items}
    delta = None if base is None else {"new": sorted(cur - set(base)), "still_open": sorted(cur & set(base)), "fixed": sorted(set(base) - cur)}
    score = max(0, 100 - sum(PENALTY[i["severity"]] for i in items))
    out = u / "report"
    out.mkdir(exist_ok=True)
    jdump(out / "findings.json", {"health_score": score, "baseline_delta": delta, "findings": items})

    led = [__import__("json").loads(x) for x in (u / "ledger.jsonl").read_text().splitlines()] if (u / "ledger.jsonl").exists() else []
    levels = sorted({x.get("sandbox", "?") for x in led if not x.get("ping")})
    L = [f"# UAT Report", "", f"**Health score:** {score}/100 | **Accepted:** {len(items)} | **Rejected/dropped:** {len(rej)}",
         f"**Sandbox:** {', '.join(levels) or 'n/a'} (bwrap = read-only system, empty home, own lane; network is open to the target by design)", ""]
    if delta:
        L += [f"**Since last run:** {len(delta['new'])} new, {len(delta['still_open'])} still open, {len(delta['fixed'])} fixed", ""]
    sev_n = {s: sum(1 for i in items if i["severity"] == s) for s in SEV}
    L += ["| Blocker | Major | Minor | Nit |", "|---|---|---|---|", f"| {sev_n['blocker']} | {sev_n['major']} | {sev_n['minor']} | {sev_n['nit']} |", ""]
    for i in sorted(items, key=lambda x: SEV[x["severity"]]):
        s = i.get("suspect") or {}
        loc = f"`{s.get('file')}:{s.get('line', '?')}`" if s.get("file") else "unknown"
        L += [
            f"## {i['cid']} [{i['severity'].upper()}] {i['title']}", "",
            f"- **Area / category:** {i['area']} / {i['category']}" + (f" | **Rule:** {i['rule_id']}" if i["rule_id"] else ""),
            f"- **Confidence:** {i['confidence']['agents']} agent(s) ({', '.join(i['confidence']['families'])}); replay: {i['confidence']['replay']}"
            + (f", INTERMITTENT, fails {i['confidence']['fail_rate']:.0%} of stress runs (suspected race; fix acceptance = 0 failures in stress runs)" if i["confidence"]["intermittent"] else ""),
            f"- **Expected:** {i['expected']}", f"- **Actual:** {i['actual']}", f"- **Suspect location:** {loc}",
            f"- **Evidence:** {i['evidence']}", f"- **Repro ({i['repro']['kind']}):** `{i['repro']['cmd']}`",
            f"- **Acceptance (fix is done when this exits 0):** `{i['acceptance']}`", f"- **Regression test pinned in:** `{i['regression_dir']}`", "",
        ]
    if rej:
        L += ["## Not accepted", ""] + [f"- {c['cid']} {c['title']} ({c['replay']}, agreement {c['agreement']}) {c['note']}" for c in rej]
    (out / "report.md").write_text("\n".join(L) + "\n")

    cases = "".join(
        f"<testcase classname={quoteattr(i['area'])} name={quoteattr(i['cid'] + ' ' + i['title'])}><failure message={quoteattr(i['severity'])}>{escape(str(i['actual']))}</failure></testcase>"
        for i in items)
    (out / "junit.xml").write_text(f'<?xml version="1.0"?><testsuite name="uat" tests="{len(items)}" failures="{len(items)}">{cases}</testsuite>\n')
    if a.save_baseline:
        jdump(u / "baseline.json", sorted(cur))
    import fixq
    fixq.sync(r)
    print(f"report: {out / 'report.md'}  accepted={len(items)} score={score}")


if __name__ == "__main__":
    main()
