#!/usr/bin/env python3
"""Render one tester brief from a charter in .tusk/plan/plan.json, for one lane. Paths in the brief are the ones the executing machine
(this one or the SSH host) sees. Also writes .tusk/meta/<agent>.json (outside the agent-writable dir), the label the dashboard shows.

  make_brief.py --agent A4 --charter CH-07 --lane L2 --family codex --model gpt-5.6-luna [--root D]   -> .tusk/agents/A4/brief.md
plan.json: {"rules":[{id,area,statement,source,confidence}], "charters":[{id,area,lens,rule_ids,entry,tools,leads,notes}]}
"""
import argparse
import os
import sys
from pathlib import Path
from string import Template

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tusklib import config, emit, jdump, jload, lane_url, layout, meta_path, udir  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--agent", required=True)
    ap.add_argument("--charter", required=True)
    ap.add_argument("--family", default="")
    ap.add_argument("--model", default="")
    ap.add_argument("--lane", default="L1")
    ap.add_argument("--root", default=os.getcwd())
    a = ap.parse_args()
    r = Path(a.root).resolve()
    cfg = config(r)
    plan = jload(udir(r) / "plan" / "plan.json", {})
    ch = next((c for c in plan.get("charters", []) if c["id"] == a.charter), None)
    if not ch:
        sys.exit(f"charter {a.charter} not in plan.json")
    rules = {x["id"]: x for x in plan.get("rules", [])}
    rule_txt = "\n".join(f"- {rid}: {rules[rid]['statement']} (source: {rules[rid]['source']})" for rid in ch.get("rule_ids", []) if rid in rules) or "- (none listed; derive expectations from the area's docs and code, say so in findings)"
    L = layout(cfg, r)
    home = Path(L["home"])
    persona = home / "agents" / f"tester-{ch['lens']}.md"
    tpl = Template((Path(__file__).resolve().parent.parent / "resources" / "tester-brief-template.md").read_text())
    d = udir(r) / "agents" / a.agent
    (d / "findings").mkdir(parents=True, exist_ok=True)
    jdump(meta_path(r, a.agent), {"id": a.agent, "family": a.family, "model": a.model, "lane": a.lane, "charter": ch["id"], "area": ch["area"], "lens": ch["lens"], "role": "tester"})
    rd = Path(L["udir"])
    text = tpl.safe_substitute(
        agent=a.agent, family=a.family, model=a.model, charter=ch["id"], area=ch["area"], lens=ch["lens"], persona=persona, core=home / "agents" / "tester-core.md",
        schema=home / "resources" / "finding-schema.json", base_url=lane_url(cfg, a.lane), workspace=rd / "lanes" / a.lane / "ws", agent_dir=rd / "agents" / a.agent,
        entry=ch.get("entry", "see plan"), rules=rule_txt, tools="\n".join(f"- {t}" for t in ch.get("tools", [])) or "- none specified",
        leads="\n".join(f"- {x}" for x in ch.get("leads", [])) or "- none", notes=ch.get("notes", ""), max_findings=cfg["stop"]["max_findings_per_agent"],
        catalog=home / "resources" / "tool-catalog.json", tools_path=rd / "tools", event=f"python3 {home}/scripts/tusk_event.py", kind=cfg["env"]["kind"])
    import kb
    rv = kb.reverify_lines(r, ch["area"])
    if rv:      # behaviour that was reported and fixed earlier: look at it first, no code locations given (testers stay blind)
        text += "\n\n## Previously reported and fixed: re-verify these first\nIf one is broken again, report it as a normal finding.\n" + "\n".join(rv) + "\n"
    (d / "brief.md").write_text(text)
    emit(r, "orchestrator", "assign", to=a.agent, kind="charter", ref=ch["id"], area=ch["area"], lens=ch["lens"], family=a.family, model=a.model.split(",")[0], lane=a.lane,
         note=f"test {len(ch.get('rule_ids', []))} rule(s) in {ch['area']} as a {ch['lens']}")
    print(d / "brief.md")


if __name__ == "__main__":
    main()
