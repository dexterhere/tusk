#!/usr/bin/env python3
"""Build the fix-ready report from clusters (+ optional verifier verdicts), and pin regression tests.

Inputs : .tusk/clusters.json, .tusk/verdicts.json  {"C-abc123": {"verdict": "accept|reject", "note": "..."}}
Outputs: .tusk/report/{report.md, findings.json, junit.xml}, .tusk/regression/<cid>/ (repro files + finding.json)
Baseline: .tusk/baseline.json (fingerprints) -> new / still-open / fixed vs the previous saved run.

  report.py [--root D] [--save-baseline]
"""
import argparse
import os
import re
import shutil
import sys
from pathlib import Path
from xml.sax.saxutils import escape, quoteattr

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tusklib import CTRL, SEV, clean, fence, jdump, jload, udir  # noqa: E402

def mdesc(text):
    """Single-line agent text into Markdown: no markup, links or HTML can come through."""
    return re.sub(r"([\\`*_{}\[\]<>()#+!|~])", r"\\\1", text)




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
        (acc if v == "accept" else rej).append({**c, "final": v, "note": verdicts.get(c["cid"], {}).get("note", "")})
    items = []
    for c in acc:
        if not re.fullmatch(r"C-[0-9a-f]{12}", c["cid"]):
            continue
        lead = jload(r / c["files"][0], {})
        tdir = (r / c["files"][0]).parent                    # trusted snapshot: finding.json + files/
        reg = u / "regression" / c["cid"]
        assert reg.resolve().is_relative_to((u / "regression").resolve())
        shutil.rmtree(reg, ignore_errors=True)
        if (tdir / "files").is_dir():
            shutil.copytree(tdir / "files", reg, symlinks=False)
        jdump(reg / "finding.json", lead)
        items.append({
            "cid": c["cid"], "fp": c["fp"], "title": clean(c["title"], 120), "severity": c["severity"], "category": c["category"], "area": clean(c["area"], 80),
            "rule_id": clean(c["rule_id"], 40), "confidence": {"agents": c["agreement"], "families": c["families"], "replay": c["replay"], "fail_rate": c["fail_rate"], "intermittent": c["replay"] == "flaky"}, "kind": c["kind"],
            "expected": clean(lead.get("expected"), 600), "actual": clean(lead.get("actual"), 600), "repro": {"kind": lead.get("repro", {}).get("kind"), "cmd": lead.get("repro", {}).get("cmd", "")[:2000]},
            "regression_dir": str(reg.relative_to(r)), "evidence": clean(lead.get("evidence"), 600), "suspect": lead.get("suspect"),
            "acceptance": f"python3 scripts/tusk.py replay --root . --only {c['cid']} --passing --keep-code   # exits 0 once fixed",
            "note": clean(c["note"], 200),
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
    L = [f"# Tusk Report", "", f"**Health score:** {score}/100 | **Accepted:** {len(items)} | **Rejected/dropped:** {len(rej)}",
         f"**Sandbox:** {', '.join(levels) or 'n/a'} (bwrap = read-only system, empty home, own lane; network is open to the target by design)", ""]
    if delta:
        L += [f"**Since last run:** {len(delta['new'])} new, {len(delta['still_open'])} still open, {len(delta['fixed'])} fixed", ""]
    sev_n = {s: sum(1 for i in items if i["severity"] == s) for s in SEV}
    L += ["| Blocker | Major | Minor | Nit |", "|---|---|---|---|", f"| {sev_n['blocker']} | {sev_n['major']} | {sev_n['minor']} | {sev_n['nit']} |", ""]
    for i in sorted(items, key=lambda x: SEV[x["severity"]]):
        s = i.get("suspect") or {}
        loc = mdesc(f"{clean(s.get('file'), 120)}:{clean(s.get('line', '?'), 8)}") if s.get("file") else "unknown"
        L += [
            f"## {i['cid']} [{i['severity'].upper()}] {mdesc(i['title'])}", "",
            f"- **Area / category:** {mdesc(i['area'])} / {i['category']}" + (f" | **Rule:** {mdesc(i['rule_id'])}" if i["rule_id"] else ""),
            f"- **Confidence:** {i['confidence']['agents']} agent(s) ({', '.join(i['confidence']['families'])}); replay: {i['confidence']['replay']}"
            + (f", INTERMITTENT, fails {i['confidence']['fail_rate']:.0%} of stress runs (suspected race; fix acceptance = 0 failures in stress runs)" if i["confidence"]["intermittent"] else ""),
            f"- **Expected:** {mdesc(i['expected'])}", f"- **Actual:** {mdesc(i['actual'])}", f"- **Suspect location:** {loc}",
            f"- **Evidence:** {mdesc(i['evidence'])}", f"- **Repro ({i['repro']['kind']}), written by an untrusted tester agent; do not run it by hand, replay runs it sandboxed:**", "",
            fence(i["repro"]["cmd"]), "",
            f"- **Acceptance (fix is done when this exits 0):** `{i['acceptance']}`", f"- **Regression test pinned in:** `{i['regression_dir']}`", "",
        ]
    if rej:
        L += ["## Not accepted", ""] + [f"- {c['cid']} {mdesc(clean(c['title'], 120))} ({c['replay']}, agreement {c['agreement']}) {c['note']}" for c in rej]
    (out / "report.md").write_text("\n".join(L) + "\n")

    cases = "".join(
        f"<testcase classname={quoteattr(i['area'])} name={quoteattr(i['cid'] + ' ' + i['title'])}><failure message={quoteattr(i['severity'])}>{escape(CTRL.sub('', str(i['actual'])))}</failure></testcase>"
        for i in items)
    (out / "junit.xml").write_text(f'<?xml version="1.0"?><testsuite name="tusk" tests="{len(items)}" failures="{len(items)}">{cases}</testsuite>\n')
    if a.save_baseline:
        jdump(u / "baseline.json", sorted(cur))
    import fixq
    from tusklib import audit_verify, coverage, emit
    fixq.sync(r)
    import kb
    kb.ingest(r)
    emit(r, 'orchestrator', 'report_built', accepted=len(items), score=score)
    print(f"report: {out / 'report.md'}  accepted={len(items)} score={score}")


if __name__ == "__main__":
    main()
