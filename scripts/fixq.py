#!/usr/bin/env python3
"""Fix queue: every failed or non-optimal result waits here until a human approves it. Nothing is fixed unasked.

States: proposed -> approved -> fixing -> proven | failed | needs_human ;  proposed -> rejected
Items come from .uat/clusters.json (verdict accept, kind defect or suboptimal). Unconfirmed `watch` items are listed
as info only. The dashboard and /uat-fix both read and write this one file.

  fixq.py sync | list | approve ID.. | approve --all | approve --severity blocker,major | reject ID.. | set ID STATE [--note T] [--branch B]
"""
import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from uatlib import SEV, config, effective_verdict, emit, jdump, jload, udir  # noqa: E402

STATES = ("proposed", "approved", "fixing", "proven", "failed", "needs_human", "rejected", "stale")
FIXABLE = ("accept",)


def path(r):
    return udir(r) / "fixqueue.json"


def load(r):
    return jload(path(r), [])


def sync(r):
    """Add new clusters as `proposed`; keep the state of known ones; mark ones that no longer reproduce as stale."""
    cfg = config(r)
    have = {i["cid"]: i for i in load(r)}
    for c in jload(udir(r) / "clusters.json", []):
        v = effective_verdict(r, c)
        if v not in FIXABLE:
            continue
        if c["kind"] == "suboptimal" and not cfg["fix"]["suboptimal_queue"]:
            continue
        it = have.setdefault(c["cid"], {"cid": c["cid"], "state": "proposed", "history": [], "digest": c["digest"]})
        if it["digest"] != c["digest"] and it["state"] in ("approved", "fixing", "proven", "stale", "rejected"):
            # the finding changed after it was judged: an earlier approval or rejection does not carry over
            it["history"].append({"from": it["state"], "to": "proposed", "by": "orchestrator", "note": "content changed, needs a fresh decision"})
            it["state"] = "proposed"
        it["digest"] = c["digest"]
        it.update({k: c[k] for k in ("title", "severity", "category", "area", "kind", "fp", "agreement", "fail_rate")} | {"verdict": v, "rule_id": c["rule_id"]})
    seen = {c["cid"] for c in jload(udir(r) / "clusters.json", [])}
    for it in have.values():
        if it["cid"] not in seen and it["state"] in ("proposed", "approved"):
            it["history"].append({"from": it["state"], "to": "stale", "by": "orchestrator", "note": "cluster no longer present"})
            it["state"] = "stale"
    items = sorted(have.values(), key=lambda i: (SEV[i["severity"]], i["cid"]))
    jdump(path(r), items)
    return items


def set_state(r, cid, state, note="", branch="", by="orchestrator"):
    assert state in STATES, state
    items = load(r)
    it = next((i for i in items if i["cid"] == cid), None)
    if not it:
        raise KeyError(cid)
    it["history"].append({"from": it["state"], "to": state, "by": by, "note": note})
    it["state"] = state
    if branch:
        it["branch"] = branch
    jdump(path(r), items)
    emit(r, by, "fix_state", cid=cid, state=state, note=note)
    try:
        import kb
        kb.ingest(r, new_run=False)             # keep the knowledge graph's issue status in step with the queue
    except Exception:
        pass
    return it


def approve(r, ids=None, severities=None, by="user"):
    items = load(r)
    chosen = [i["cid"] for i in items if i["state"] == "proposed" and (ids is None and severities is None or i["cid"] in (ids or []) or i["severity"] in (severities or []))]
    for cid in chosen:
        set_state(r, cid, "approved", by=by)
    return chosen


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["sync", "list", "approve", "reject", "set"])
    ap.add_argument("ids", nargs="*")
    ap.add_argument("--root", default=os.getcwd())
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--severity")
    ap.add_argument("--note", default="")
    ap.add_argument("--branch", default="")
    a = ap.parse_args()
    r = Path(a.root).resolve()
    if a.cmd == "sync":
        items = sync(r)
    elif a.cmd == "approve":
        if not (a.ids or a.all or a.severity):
            sys.exit("name ids, or --all, or --severity")
        print("approved:", approve(r, a.ids or None, a.severity.split(",") if a.severity else None))
        items = load(r)
    elif a.cmd == "reject":
        [set_state(r, c, "rejected", a.note, by="user") for c in a.ids]
        items = load(r)
    elif a.cmd == "set":
        set_state(r, a.ids[0], a.ids[1], a.note, a.branch)
        items = load(r)
    else:
        items = load(r)
    for i in items:
        print(f"{i['cid']} {i['state']:11} {i['severity']:7} {i['kind']:10} {i['area']}: {i['title']}")


if __name__ == "__main__":
    main()
