#!/usr/bin/env python3
"""Render one tester brief from a charter in .uat/plan/plan.json.

  make_brief.py --agent A4 --charter CH-07 [--root D]   -> .uat/agents/A4/brief.md (prints path)
plan.json: {"rules":[{id,area,statement,source,confidence}], "charters":[{id,area,lens,rule_ids,entry,tools,leads,notes}]}
"""
import argparse
import os
import sys
from pathlib import Path
from string import Template

sys.path.insert(0, str(Path(__file__).resolve().parent))
from uatlib import HOME, config, jload, udir  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--agent", required=True)
    ap.add_argument("--charter", required=True)
    ap.add_argument("--family", default="")
    ap.add_argument("--model", default="")
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
    persona = HOME / "agents" / f"tester-{ch['lens']}.md"
    tpl = Template((HOME / "resources" / "tester-brief-template.md").read_text())
    d = udir(r) / "agents" / a.agent
    (d / "findings").mkdir(parents=True, exist_ok=True)
    text = tpl.safe_substitute(
        agent=a.agent, family=a.family, model=a.model, charter=ch["id"], area=ch["area"], lens=ch["lens"], persona=persona, core=HOME / "agents" / "tester-core.md",
        schema=HOME / "resources" / "finding-schema.json", base_url=cfg["env"]["base_url"], workspace=udir(r) / "workspace", agent_dir=d,
        entry=ch.get("entry", "see plan"), rules=rule_txt, tools="\n".join(f"- {t}" for t in ch.get("tools", [])) or "- none specified",
        leads="\n".join(f"- {x}" for x in ch.get("leads", [])) or "- none", notes=ch.get("notes", ""), max_findings=cfg["stop"]["max_findings_per_agent"],
        catalog=HOME / "resources" / "tool-catalog.json", tools_path=udir(r) / "tools")
    (d / "brief.md").write_text(text)
    print(d / "brief.md")


if __name__ == "__main__":
    main()
