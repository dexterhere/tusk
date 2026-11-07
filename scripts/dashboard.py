#!/usr/bin/env python3
"""Local live dashboard: who is testing what, results, the fix queue. Stdlib only, binds to 127.0.0.1.

  dashboard.py [--root D] [--port 7777]      then open http://127.0.0.1:7777
Read side: polls files under .uat/ (in ssh mode a thread mirrors them from the host every 3 s).
Read-only: the dashboard has no write endpoints. Approvals happen in the chat (/uat-fix) or with fixq.py, never from this page.
"""
import argparse
import json
import os
import secrets
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fixq  # noqa: E402
import kb  # noqa: E402
import plain  # noqa: E402
import team  # noqa: E402
import host  # noqa: E402
import re  # noqa: E402
from uatlib import HOME, SEV, agent_identity, agent_meta, audit, audit_read, audit_verify, clean, config, coverage, effective_verdict, is_remote, jload, read_events, safe_read, udir  # noqa: E402

WEB = HOME / "web" / "index.html"


STATUS = {"queued", "running", "done", "failed", "tamper"}


def num(x, default=0):
    return x if isinstance(x, (int, float)) and not isinstance(x, bool) else default


def assignments(events):
    """Who the orchestrator gave what to, from `assign` events (make_brief writes one per tester; the fix and verify skills write the rest)."""
    out = []
    for e in events:
        if e["type"] == "assign" and e.get("to"):
            out.append({"ts": num(e["ts"]), "by": clean(e["actor"], 40), "to": clean(e.get("to"), 40), "kind": clean(e.get("kind"), 12), "ref": clean(e.get("ref"), 60), "family": clean(e.get("family"), 30),
                        "model": clean(e.get("model"), 60), "lane": clean(e.get("lane"), 10), "note": clean(e.get("note"), 200)})
    return out[-300:]


def found_by(r, c, cache):
    out = []
    for aid in c.get("agents", [])[:12]:
        aid = clean(aid, 40)
        if aid not in cache:
            i = agent_identity(r, aid)
            cache[aid] = {"id": aid, "family": i["family"], "model": i["model"], "planned_model": i["planned_model"], "lens": i["lens"], "lane": i["lane"], "charter": i["charter"],
                          "fallback": bool(i["planned_model"] and i["model"] != i["planned_model"])}
        out.append(cache[aid])
    return out


def workboard(plan, agents, assigns, fq, clusters, verdicts, life):
    """Planned -> in progress -> done, for tester charters, verifier jobs and fixes. Owner = the agent actually doing it."""
    items = []
    by_charter = {}
    for a in agents.values():
        if a.get("charter") and a.get("role") != "fix":
            by_charter.setdefault(a["charter"], []).append(a)
    for ch in plan.get("charters", [])[:200]:
        ags = by_charter.get(ch.get("id"), [])
        state = "doing" if any(a["status"] == "running" for a in ags) else "done" if ags else "planned"
        items.append({"kind": "test", "ref": clean(ch.get("id"), 40), "title": f"Test {clean(ch.get('area'), 60)} as a {clean(ch.get('lens'), 20)}", "state": state,
                      "owners": [{"id": a["id"], "family": a.get("family", ""), "model": a.get("model", ""), "lane": a.get("lane", ""), "status": a["status"]} for a in ags]})
    owner = {}
    for x in assigns:
        owner[(x["kind"], x["ref"])] = x
    for c in clusters:
        if c.get("verdict") == "needs_verifier" and c["cid"] not in verdicts:
            x = owner.get(("verify", c["cid"]))
            items.append({"kind": "verify", "ref": c["cid"], "title": f"Second opinion: {clean(c.get('title'), 100)}", "state": "doing" if x else "planned",
                          "owners": [{"id": x["to"], "family": x["family"], "model": x["model"], "lane": "", "status": "running"}] if x else []})
    for i in fq:
        l = life.get(i["cid"]) or ("open" if i["state"] == "proposed" else i["state"])
        if l in ("open", "wontfix", "not_seen"):
            continue
        x = owner.get(("fix", i["cid"]))
        state = "planned" if l == "approved" else "doing" if l == "fixing" else "done"
        items.append({"kind": "fix", "ref": clean(i["cid"], 20), "title": f"Fix: {clean(i.get('title'), 100)}", "state": state, "life": l,
                      "owners": [{"id": x["to"], "family": x["family"], "model": x["model"], "lane": "", "status": "running" if state == "doing" else "done"}] if x else [], "branch": clean(i.get("branch"), 80)})
    return items


def journey(phase, plan, agents, clusters, fq, life, has_report, audit_ok, running):
    n = lambda k: sum(1 for v in life.values() if v == k)
    prop = sum(1 for i in fq if i["state"] == "proposed")
    appr = sum(1 for i in fq if i["state"] in ("approved", "fixing"))
    rs = [
        ("setup", "Set up", "You choose where tests run and which agents to use.", bool(phase), "/uat-init", "you + orchestrator"),
        ("plan", "Plan", f"{len(plan.get('rules', []))} rules and {len(plan.get('charters', []))} test assignments written from your docs and code.", bool(plan.get("charters")), "/uat-plan", "orchestrator"),
        ("execute", "Test", f"{sum(1 for a in agents.values() if a['status'] == 'done')} of {len(agents)} testers finished, each in its own copy of the app.", bool(agents) and not running, "/uat-execute", "tester agents"),
        ("judge", "Judge", f"{len(clusters)} problem group(s) after re-running every finding; a script decides, not a model.", bool(clusters) or phase in ("reported", "fixed"), "/uat-feedback", "scripts + verifiers"),
        ("report", "Report", "A fix-ready report with the exact repro for each problem." if has_report else "Not built yet.", has_report, "/uat-feedback", "orchestrator"),
        ("decide", "You decide", f"{prop} waiting for your yes or no. Approvals happen in chat." if prop else "Nothing waiting.", has_report and prop == 0, "", "you"),
        ("fix", "Fix", f"{appr} in progress, {n('fixed') + n('verified')} fixed. Each fix first checks what else lives in the same code.", has_report and appr == 0 and (n("fixed") + n("verified")) > 0, "/uat-fix", "fix agents"),
        ("retest", "Re-test", f"{n('verified')} verified in a later run, {n('fixed')} waiting for it, {n('regressed')} came back.", n("fixed") == 0 and n("verified") > 0 and n("regressed") == 0, "/uat-execute", "testers again"),
    ]
    cur = next((i for i, x in enumerate(rs) if not x[3]), len(rs) - 1)
    if running:
        cur = 2
    return [{"id": a, "title": b, "text": c, "state": "done" if d and i != cur else "current" if i == cur else "todo", "cmd": e, "who": f} for i, (a, b, c, d, e, f) in enumerate(rs)]


def snapshot(r):
    """Everything here is rebuilt from allowlisted fields with bounded strings: agent-written files never reach the page raw."""
    u = udir(r)
    cfg = config(r)
    events = read_events(r)
    plan = jload(u / "plan" / "plan.json", {})
    agents = {}

    def agent(aid):
        if aid not in agents:
            agents[aid] = {"id": aid, "status": "queued", "findings": 0, "now": "", "checks": {}, "role": "tester",
                           **{k: v for k, v in agent_meta(r, aid).items() if v and k != "id"}}
        return agents[aid]

    for d in sorted((u / "agents").glob("*")) if (u / "agents").exists() else []:
        if d.is_dir() and not d.is_symlink() and d.name != "ping":          # `ping` = harness connectivity test, not a tester
            agent(clean(d.name, 40))["findings"] = len(finding_files_of(r, d))
    for e in events:
        t = e["type"]
        if t not in ("agent_start", "fix_start") and e["actor"] not in agents:
            continue
        a = agent(clean(e["actor"], 40))
        if t in ("agent_start", "fix_start"):
            a.update({"status": "running", "started": num(e["ts"]), "family": clean(e.get("family", a.get("family", "")), 30), "model": clean(e.get("model", a.get("model", "")), 60),
                      "planned_model": clean(e.get("model", a.get("model", "")), 60), "lane": clean(e.get("lane", ""), 10), "role": "fix" if t == "fix_start" else "tester", "charter": clean(e.get("charter", a.get("charter", "")), 40), "now": "starting"})
        elif t == "agent_status":
            a.update({"now": clean(e.get("now") or a["now"], 160), "findings": int(num(e.get("findings"), a["findings"]))})
        elif t == "step":
            a["now"] = clean(e.get("text", ""), 160)
        elif t == "check":
            a["checks"][clean(e.get("rule", ""), 40)] = "fail" if e.get("result") == "fail" else "pass"
        elif t in ("agent_done", "fix_done"):
            a.update({"status": "tamper" if e.get("tamper") else "done" if e.get("rc", 0) == 0 else "failed", "ended": num(e["ts"]), "rc": e.get("rc") if isinstance(e.get("rc"), int) else None, "model": clean(e.get("model") or a.get("model", ""), 60), "findings": int(num(e.get("findings"), a["findings"])), "now": clean(e.get("note", "finished"), 160)})
    for a in agents.values():
        a["status"] = a["status"] if a["status"] in STATUS else "queued"
        a["fallback"] = bool(a.get("planned_model") and a.get("model") and a["planned_model"] != a["model"])
        tail = clean(safe_read(u / "logs" / f"{a['id']}.log", tail=True)[-600:], 400) if a["status"] in ("failed", "tamper") else ""
        a["why"] = plain.agent(a["status"], a.get("rc"), a.get("now", ""), tail, a["status"] == "tamper")
        a["tech"] = ({"exit_code": a.get("rc"), "log_tail": tail} if tail or a.get("rc") not in (None, 0) else {})
    clusters = jload(u / "clusters.json", [])
    led = [x for x in (safe_json_line(ln) for ln in safe_read(u / "ledger.jsonl", tail=True).splitlines()) if x]
    rounds = [x for x in (safe_json_line(ln) for ln in safe_read(u / "rounds.jsonl").splitlines()) if x]
    orch = jload(u / "orchestrator.json", {})
    replays = []
    for f in sorted((u / "replays").glob("*/*.replay.json"))[:300]:
        o = jload(f, {}) or {}
        replays.append({"agent": clean(f.parent.name, 40), "finding": clean(f.name.removesuffix(".replay.json"), 60), "status": clean(o.get("status"), 20), "fail_rate": num(o.get("fail_rate")),
                        "runs": len(o.get("runs", [])), "touches_target": bool(o.get("touches_target")), "lane": clean(o.get("lane"), 10), "why": plain.replay(clean(o.get("status"), 20))})
    cov = coverage(r)
    ok, n_audit, head, bad = audit_verify(r)
    accepted = [c for c in clusters if c.get("verdict") == "accept"]
    first = min([e["ts"] for e in events] or [time.time()])
    fq = fixq.load(r)
    phase = clean(jload(u / "state.json", {}).get("phase", ""), 30)
    running = sum(1 for a in agents.values() if a["status"] == "running")
    proposed, approved = sum(1 for i in fq if i["state"] == "proposed"), sum(1 for i in fq if i["state"] == "approved")
    if running:
        nxt = {"text": f"{running} agent(s) running. Nothing to do until they finish.", "cmd": ""}
    elif approved:
        nxt = {"text": f"{approved} fix(es) approved and waiting.", "cmd": "/uat-fix"}
    elif proposed:
        nxt = {"text": f"{proposed} result(s) wait for your decision.", "cmd": "", "tab": "fixes"}
    else:
        nxt = {"text": {"": "Not started.", "initialized": "Environment ready.", "planned": "Plan approved.", "executing": "Run finished or paused.", "executed": "Testing done.", "reported": "Report ready.", "fixed": "All approved fixes are proven."}.get(phase, ""),
               "cmd": {"": "/uat-init", "initialized": "/uat-plan", "planned": "/uat-execute", "executing": "/uat-feedback", "executed": "/uat-feedback", "reported": "", "fixed": ""}.get(phase, "")}
    summary = {"agents": {"total": len(agents), "running": running, "done": sum(1 for a in agents.values() if a["status"] == "done"), "failed": sum(1 for a in agents.values() if a["status"] in ("failed", "tamper"))},
               "tests": {"rules": len(cov), **{k: sum(1 for c in cov if c["status"] == k) for k in ("passed", "violated", "failed-unconfirmed", "untested")}},
               "findings": {s: sum(1 for c in accepted if c.get("severity") == s) for s in SEV}, "elapsed_s": round(time.time() - first) if events else 0}
    g = kb.load(r)
    verdicts = jload(u / "verdicts.json", {}) or {}
    life = {k[2:]: v.get("status", "") for k, v in kb.issues(g).items()}
    assigns = assignments(events)
    cache = {}
    cl_out = []
    for c in clusters[:500]:
        ev = effective_verdict(r, c, verdicts)
        vv = verdicts.get(c.get("cid"), {})
        cl_out.append({**{k: clean(c.get(k, ""), 160) for k in ("cid", "title", "area", "rule_id", "replay", "category", "kind")}, "verdict": clean(ev, 20), "script_verdict": clean(c.get("verdict", ""), 20),
                       "severity": c["severity"] if c.get("severity") in SEV else "nit", "agents": [clean(x, 40) for x in c.get("agents", [])[:20]], "found_by": found_by(r, c, cache),
                       "merge_reasons": [clean(x, 40) for x in c.get("merge_reasons", [])], "fail_rate": num(c.get("fail_rate")), "why": plain.verdict(ev if ev in plain.VERDICT else c.get("verdict", "")),
                       "decided_by": ({"by": clean(vv.get("by"), 40), "family": clean(vv.get("family") or "", 30), "model": clean(vv.get("model") or "", 60), "note": clean(vv.get("note") or "", 300)} if vv else None)})
    board = workboard(plan, agents, assigns, fq, clusters, verdicts, life)
    jr = journey(phase, plan, agents, clusters, fq, life, (u / "report" / "report.md").exists(), ok, running)
    kbs = {"runs": g["runs"], "nodes": len(g["nodes"]), "edges": len(g["edges"]), "status": {}}
    for v in kb.issues(g).values():
        kbs["status"][v.get("status", "open")] = kbs["status"].get(v.get("status", "open"), 0) + 1
    return {
        "journey": jr, "board": board, "assignments": assigns, "audit_why": plain.audit(ok, bad), "life_why": {k: plain.kb_status(k) for k in ("open", "approved", "fixing", "fixed", "verified", "regressed", "wontfix", "not_seen")},
        "kb": kbs, "next": nxt, "summary": summary, "replays": replays, "tests": cov, "audit": {"ok": ok, "count": n_audit, "head": head[:16], "bad_seq": bad},
        "lanes": sorted({a.get("lane") for a in agents.values() if a.get("lane")}), "files": [f for f in ("report.md", "findings.json", "junit.xml") if (u / "report" / f).exists()],
        "now": time.time(), "project": clean(r.name, 60), "state": {"phase": clean(jload(u / "state.json", {}).get("phase", ""), 30)},
        "orchestrator": {"name": clean(orch.get("name", "Orchestrator"), 40), "model": clean(orch.get("model", "claude"), 60)},
        "target": {"mode": cfg["target"]["mode"], "host": clean(cfg["target"]["ssh"]["host"], 80) if is_remote(cfg) else "this computer", "kind": cfg["env"]["kind"]},
        "sandbox": sorted({clean(x.get("sandbox", "?"), 10) for x in led if not x.get("ping")}), "agents": list(agents.values()),
        "rules": [{"id": clean(x.get("id"), 40), "statement": clean(x.get("statement"), 200)} for x in plan.get("rules", [])[:300]],
        "charters": [{"id": clean(c.get("id"), 40), "area": clean(c.get("area"), 60), "lens": clean(c.get("lens"), 20), "rule_ids": [clean(x, 40) for x in c.get("rule_ids", [])[:20]]} for c in plan.get("charters", [])[:200]],
        "clusters": cl_out,
        "fixqueue": [{**{k: clean(i.get(k, ""), 160) for k in ("cid", "title", "area", "kind", "state", "branch")}, "severity": i["severity"] if i.get("severity") in SEV else "nit", "life": clean(g["nodes"].get("i:" + str(i.get("cid")), {}).get("status", ""), 20)} for i in fixq.load(r)[:500]],
        "rounds": [{"round": int(num(x.get("round"))), "new_clusters": int(num(x.get("new_clusters")))} for x in rounds[-30:]], "events": [{k: (clean(v, 160) if isinstance(v, str) else num(v) if k == "ts" else "") for k, v in e.items() if k in ("ts", "actor", "type", "text", "now", "rule", "result", "status", "state", "to", "ref", "kind", "model", "family", "findings", "rc", "verdict", "cid", "lane")} for e in events[-80:]],
        "report": (u / "report" / "report.md").exists(), "score": num((jload(u / "report" / "findings.json", {}) or {}).get("health_score"), None),
    }


def safe_json_line(ln):
    try:
        v = json.loads(ln)
        return v if isinstance(v, dict) else None
    except ValueError:
        return None


def finding_files_of(r, d):
    return [p for p in (d / "findings").glob("F-*.json") if not (d / "findings").is_symlink() and not p.is_symlink()] if (d / "findings").is_dir() else []


def cluster_detail(r, cid):
    c = next((x for x in jload(udir(r) / "clusters.json", []) if x.get("cid") == cid), None)
    if not c:
        return None
    members = []
    for rel in c.get("files", [])[:20]:
        f = (r / rel).resolve()
        if not f.is_relative_to((udir(r) / "trusted").resolve()):
            continue
        o = jload(f, {}) or {}
        agent, stem = f.parent.parent.name, f.parent.name
        rp = jload(udir(r) / "replays" / agent / f"{stem}.replay.json", {}) or {}
        members.append({"agent": clean(agent, 40), "id": clean(o.get("id"), 60), "title": clean(o.get("title"), 160), "severity": o.get("severity") if o.get("severity") in SEV else "nit",
                        "expected": clean(o.get("expected"), 600), "actual": clean(o.get("actual"), 600), "evidence": clean(o.get("evidence"), 600),
                        "repro_kind": clean(o.get("repro", {}).get("kind"), 20), "repro_cmd": str(o.get("repro", {}).get("cmd", ""))[:2000], "suspect": clean(json.dumps(o["suspect"]), 200) if o.get("suspect") else "",
                        "replay": {"status": clean(rp.get("status", "none"), 20), "why": plain.replay(clean(rp.get("status", "none"), 20)), "fail_rate": num(rp.get("fail_rate")), "touches_target": bool(rp.get("touches_target")),
                                   "runs": [{"rc": x.get("rc") if isinstance(x.get("rc"), int) else None, "secs": num(x.get("secs")), "tail": clean(x.get("tail", ""), 300)} for x in rp.get("runs", [])[:50]]}})
    v = (jload(udir(r) / "verdicts.json", {}) or {}).get(cid, {})
    return {"cid": cid, "title": clean(c.get("title"), 160), "severity": c.get("severity") if c.get("severity") in SEV else "nit", "area": clean(c.get("area"), 80), "rule_id": clean(c.get("rule_id"), 40),
            "verdict": clean(effective_verdict(r, c), 20), "script_verdict": clean(c.get("verdict"), 20), "found_by": found_by(r, c, {}), "why": plain.verdict(effective_verdict(r, c)),
            "replay": clean(c.get("replay"), 20), "replay_why": plain.replay(clean(c.get("replay"), 20)), "trace": kb.trace(r, cid), "merge_reasons": [clean(x, 40) for x in c.get("merge_reasons", [])], "agents": [clean(x, 40) for x in c.get("agents", [])],
            "families": [clean(x, 30) for x in c.get("families", [])], "members": members, "impact": kb.impact(r, cid), "kb_status": clean((kb.load(r)["nodes"].get(f"i:{cid}") or {}).get("status") or "", 20), "verifier": {"verdict": clean(v.get("verdict") or "", 10), "note": clean(v.get("note") or "", 300), "by": clean(v.get("by") or "", 40), "family": clean(v.get("family") or "", 30), "model": clean(v.get("model") or "", 60)}}


def audit_view(r, q="", limit=500):
    rows = audit_read(r)
    out = [{"seq": x.get("seq"), "ts": x.get("ts"), "actor": clean(x.get("actor"), 60), "type": clean(x.get("type"), 40), "data": clean(json.dumps(x.get("data", {}), sort_keys=True), 400), "hash": str(x.get("hash", ""))[:12], "source": "local"}
           for x in rows if isinstance(x, dict) and not x.get("broken")]
    ql = q.lower()
    out = [x for x in out if not ql or ql in (x["actor"] + " " + x["type"] + " " + x["data"]).lower()]
    return out[-limit:][::-1]


def change_signature(r):
    """Cheap fingerprint (mtime+size) of every file the page is built from."""
    u = udir(r)
    parts = []
    pats = ["events*.jsonl", "state.json", "clusters.json", "fixqueue.json", "verdicts.json", "ledger.jsonl", "audit.jsonl", "rounds.jsonl", "plan/plan.json", "report/*", "agents/*/events.jsonl", "agents/*/findings",
            "replays/*/*.json", "meta/*.json", "model_health.json", "team.json", "team.pending.json"]
    try:
        pats.append(str(kb.kbdir(r).relative_to(u)) + "/graph.json") if kb.kbdir(r).is_relative_to(u) else parts.append(str((kb.kbdir(r) / "graph.json").stat().st_mtime_ns))
    except OSError:
        pass
    for pat in pats:
        for f in sorted(u.glob(pat))[:400]:
            try:
                st = f.stat()
                parts.append(f"{f.name}{st.st_mtime_ns}{st.st_size}")
            except OSError:
                pass
    import hashlib
    return hashlib.sha1("|".join(parts).encode()).hexdigest()[:12]


DOWNLOADS = {"report.md": ("report", "text/plain; charset=utf-8"), "findings.json": ("report", "application/json"), "junit.xml": ("report", "application/xml")}


TOKEN = secrets.token_urlsafe(24)   # per dashboard process; never sent to the page, the user pastes it in
RECENT = []


def make_handler(r, port):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, body, ctype="application/json", download=""):
            b = body if isinstance(body, bytes) else body.encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(b)))
            self.send_header("Cache-Control", "no-store")
            if download:
                self.send_header("Content-Disposition", f'attachment; filename="{download}"')
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            if ctype.startswith("text/html"):
                self.send_header("Content-Security-Policy", f"default-src 'none'; script-src 'nonce-{self.nonce}'; style-src 'unsafe-inline'; font-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
            self.end_headers()
            self.wfile.write(b)

        def _stream(self):
            """Server-sent events: one tiny message whenever anything under .uat/ that the page shows has changed, so the page reloads at once."""
            self.send_response(200)
            for k, v in (("Content-Type", "text/event-stream"), ("Cache-Control", "no-store"), ("X-Accel-Buffering", "no"), ("X-Content-Type-Options", "nosniff")):
                self.send_header(k, v)
            self.end_headers()
            last, idle = None, 0
            try:
                while True:
                    sig = change_signature(r)
                    if sig != last:
                        self.wfile.write(f"data: {sig}\n\n".encode())
                        self.wfile.flush()
                        last, idle = sig, 0
                    elif idle >= 30:
                        self.wfile.write(b": keepalive\n\n")
                        self.wfile.flush()
                        idle = 0
                    idle += 1
                    time.sleep(0.5)
            except (BrokenPipeError, ConnectionResetError, OSError):
                return

        def _host_ok(self):
            return self.headers.get("Host", "") in (f"127.0.0.1:{port}", f"localhost:{port}")  # blocks DNS rebinding

        nonce = ""

        def do_POST(self):
            """The ONLY write the dashboard has: save a team PROPOSAL. It changes no configuration; the orchestrator applies it after you agree in chat."""
            self.nonce = secrets.token_urlsafe(12)
            if self.path != "/api/team/propose":
                return self._send(404, json.dumps({"error": "not found"}))
            origin = self.headers.get("Origin", "")
            if not self._host_ok() or origin not in (f"http://127.0.0.1:{port}", f"http://localhost:{port}"):   # blocks cross-site and rebinding
                return self._send(403, json.dumps({"error": "request not allowed from this page"}))
            if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json" or self.headers.get("X-Requested-With") != "uat-dashboard":
                return self._send(415, json.dumps({"error": "bad request type"}))
            if not secrets.compare_digest(self.headers.get("X-UAT-Token", ""), TOKEN):
                return self._send(401, json.dumps({"error": "wrong or missing edit token. Get it by running: python3 scripts/team.py token"}))
            now = time.time()
            RECENT[:] = [t for t in RECENT if now - t < 60]
            if len(RECENT) >= 10:
                return self._send(429, json.dumps({"error": "too many proposals, wait a minute"}))
            RECENT.append(now)
            try:
                n = int(self.headers.get("Content-Length", "0"))
                if not 0 < n <= 8192:
                    raise ValueError("body must be 1 byte to 8 KB")
                t = team.validate(json.loads(self.rfile.read(n)), config(r), strict_models=True)
            except (ValueError, json.JSONDecodeError) as e:
                return self._send(400, json.dumps({"error": clean(str(e), 300)}))
            tmp = team.path(r, True).with_suffix(".tmp")
            tmp.write_text(json.dumps(t, indent=1, sort_keys=True))
            os.replace(tmp, team.path(r, True))
            audit(r, "dashboard", "team_proposed", workers=len(t["workers"]), orchestrator=f"{t['orchestrator']['family']}/{t['orchestrator']['model']}")
            self._send(200, json.dumps({"ok": True, "next": "Ask the orchestrator in chat: /uat-full apply"}))

        def do_GET(self):
            self.nonce = secrets.token_urlsafe(12)
            if not self._host_ok():
                return self._send(403, "bad host")
            from urllib.parse import parse_qs, urlparse
            u = urlparse(self.path)
            if u.path == "/api/state":
                return self._send(200, json.dumps(snapshot(r)))
            m = re.fullmatch(r"/api/cluster/(C-[0-9a-f]{12})", u.path)
            if m:
                d = cluster_detail(r, m.group(1))
                return self._send(200 if d else 404, json.dumps(d or {"error": "unknown cluster"}))
            if u.path == "/api/trace":
                t = kb.trace(r, parse_qs(u.query).get("id", [""])[0][:200])
                return self._send(200 if t else 404, json.dumps(t or {"error": "not in the knowledge graph"}))
            if u.path == "/api/stream":
                return self._stream()
            if u.path == "/api/team":
                return self._send(200, json.dumps(team.view(r)))
            if u.path == "/api/kb":
                return self._send(200, json.dumps(kb.view(r)))
            if u.path in ("/vendor/iconoir.js", "/vendor/dicebear-gaze.js"):      # self-hosted front-end libraries, fixed allowlist
                return self._send(200, (WEB.parent / u.path.removeprefix("/")).read_bytes(), "application/javascript; charset=utf-8")
            if u.path == "/fonts/Figtree.ttf":                 # self-hosted (SIL OFL): the page makes no outside requests
                return self._send(200, (WEB.parent / "fonts" / "Figtree.ttf").read_bytes(), "font/ttf")
            if u.path == "/api/audit":
                qs = parse_qs(u.query)
                ok, n, head, bad = audit_verify(r)
                return self._send(200, json.dumps({"ok": ok, "count": n, "head": head, "bad_seq": bad, "rows": audit_view(r, qs.get("q", [""])[0][:80])}))
            if u.path == "/download/audit.json":
                ok, n, head, bad = audit_verify(r)
                return self._send(200, json.dumps({"verified": ok, "records": n, "head": head, "log": audit_read(r)}, indent=1), "application/json", "audit.json")
            m = re.fullmatch(r"/download/([a-z.]+)", u.path)
            if m and m.group(1) in DOWNLOADS:
                sub, ctype = DOWNLOADS[m.group(1)]
                body = safe_read(udir(r) / sub / m.group(1), limit=20_000_000)
                return self._send(200 if body else 404, body or "not found", ctype, m.group(1))
            if self.path in ("/", "/index.html"):
                return self._send(200, WEB.read_text().replace("__NONCE__", self.nonce), "text/html; charset=utf-8")
            self._send(404, "not found")

    return H


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=os.getcwd())
    ap.add_argument("--port", type=int)
    a = ap.parse_args()
    r = Path(a.root).resolve()
    cfg = config(r)
    port = a.port or cfg["dashboard"]["port"]
    if is_remote(cfg):
        def mirror():
            while True:
                try:
                    host.sync_down(cfg, r)
                except Exception:
                    pass
                time.sleep(3)
        threading.Thread(target=mirror, daemon=True).start()
    tp = team.token_path(r)
    fd = os.open(tp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    os.fchmod(fd, 0o600)   # owner-only file; read it with: python3 scripts/team.py token
    with os.fdopen(fd, "w") as f:
        f.write(TOKEN)
    srv = ThreadingHTTPServer(("127.0.0.1", port), make_handler(r, port))
    print(f"UAT dashboard (read-only except team proposals): http://127.0.0.1:{port}/  (Ctrl+C to stop)", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
