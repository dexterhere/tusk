#!/usr/bin/env python3
"""Local live dashboard: who is testing what, results, the fix queue. Stdlib only, binds to 127.0.0.1.

  dashboard.py [--root D] [--port 7777]      then open http://127.0.0.1:7777
Read side: polls files under .uat/ (in ssh mode a thread mirrors them from the host every 3 s).
Write side: only the fix queue (approve / reject), guarded by a per-run token and a Host/Origin check.
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
import host  # noqa: E402
import re  # noqa: E402
from uatlib import HOME, SEV, agent_meta, audit_read, audit_verify, clean, config, coverage, is_remote, jload, read_events, safe_read, udir  # noqa: E402

WEB = HOME / "web" / "index.html"


STATUS = {"queued", "running", "done", "failed", "tamper"}


def num(x, default=0):
    return x if isinstance(x, (int, float)) and not isinstance(x, bool) else default


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
        if d.is_dir() and not d.is_symlink():
            agent(clean(d.name, 40))["findings"] = len(finding_files_of(r, d))
    for e in events:
        t = e["type"]
        if t not in ("agent_start", "fix_start") and e["actor"] not in agents:
            continue
        a = agent(clean(e["actor"], 40))
        if t in ("agent_start", "fix_start"):
            a.update({"status": "running", "started": num(e["ts"]), "family": clean(e.get("family", a.get("family", "")), 30), "model": clean(e.get("model", a.get("model", "")), 60),
                      "lane": clean(e.get("lane", ""), 10), "role": "fix" if t == "fix_start" else "tester", "charter": clean(e.get("charter", a.get("charter", "")), 40), "now": "starting"})
        elif t == "agent_status":
            a.update({"now": clean(e.get("now") or a["now"], 160), "findings": int(num(e.get("findings"), a["findings"]))})
        elif t == "step":
            a["now"] = clean(e.get("text", ""), 160)
        elif t == "check":
            a["checks"][clean(e.get("rule", ""), 40)] = "fail" if e.get("result") == "fail" else "pass"
        elif t in ("agent_done", "fix_done"):
            a.update({"status": "tamper" if e.get("tamper") else "done" if e.get("rc", 0) == 0 else "failed", "ended": num(e["ts"]), "findings": int(num(e.get("findings"), a["findings"])), "now": clean(e.get("note", "finished"), 160)})
    for a in agents.values():
        a["status"] = a["status"] if a["status"] in STATUS else "queued"
    clusters = jload(u / "clusters.json", [])
    led = [x for x in (safe_json_line(ln) for ln in safe_read(u / "ledger.jsonl", tail=True).splitlines()) if x]
    rounds = [x for x in (safe_json_line(ln) for ln in safe_read(u / "rounds.jsonl").splitlines()) if x]
    orch = jload(u / "orchestrator.json", {})
    replays = []
    for f in sorted((u / "replays").glob("*/*.replay.json"))[:300]:
        o = jload(f, {}) or {}
        replays.append({"agent": clean(f.parent.name, 40), "finding": clean(f.name.removesuffix(".replay.json"), 60), "status": clean(o.get("status"), 20), "fail_rate": num(o.get("fail_rate")),
                        "runs": len(o.get("runs", [])), "touches_target": bool(o.get("touches_target")), "lane": clean(o.get("lane"), 10)})
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
    return {
        "next": nxt, "summary": summary, "replays": replays, "tests": cov, "audit": {"ok": ok, "count": n_audit, "head": head[:16], "bad_seq": bad},
        "lanes": sorted({a.get("lane") for a in agents.values() if a.get("lane")}), "files": [f for f in ("report.md", "findings.json", "junit.xml") if (u / "report" / f).exists()],
        "now": time.time(), "project": clean(r.name, 60), "state": {"phase": clean(jload(u / "state.json", {}).get("phase", ""), 30)},
        "orchestrator": {"name": clean(orch.get("name", "Orchestrator"), 40), "model": clean(orch.get("model", "claude"), 60)},
        "target": {"mode": cfg["target"]["mode"], "host": clean(cfg["target"]["ssh"]["host"], 80) if is_remote(cfg) else "this computer", "kind": cfg["env"]["kind"]},
        "sandbox": sorted({clean(x.get("sandbox", "?"), 10) for x in led if not x.get("ping")}), "agents": list(agents.values()),
        "rules": [{"id": clean(x.get("id"), 40), "statement": clean(x.get("statement"), 200)} for x in plan.get("rules", [])[:300]],
        "charters": [{"id": clean(c.get("id"), 40), "area": clean(c.get("area"), 60), "lens": clean(c.get("lens"), 20), "rule_ids": [clean(x, 40) for x in c.get("rule_ids", [])[:20]]} for c in plan.get("charters", [])[:200]],
        "clusters": [{**{k: clean(c.get(k, ""), 160) for k in ("cid", "title", "area", "rule_id", "replay", "verdict", "category", "kind")}, "severity": c["severity"] if c.get("severity") in SEV else "nit",
                      "agents": [clean(x, 40) for x in c.get("agents", [])[:20]], "merge_reasons": [clean(x, 40) for x in c.get("merge_reasons", [])], "fail_rate": num(c.get("fail_rate"))} for c in clusters[:500]],
        "fixqueue": [{**{k: clean(i.get(k, ""), 160) for k in ("cid", "title", "area", "kind", "state", "branch")}, "severity": i["severity"] if i.get("severity") in SEV else "nit"} for i in fixq.load(r)[:500]],
        "rounds": [{"round": int(num(x.get("round"))), "new_clusters": int(num(x.get("new_clusters")))} for x in rounds[-30:]], "events": [{k: (clean(v, 160) if isinstance(v, str) else num(v) if k == "ts" else "") for k, v in e.items() if k in ("ts", "actor", "type", "text", "now", "rule", "result", "status", "state")} for e in events[-80:]],
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
                        "repro_kind": clean(o.get("repro", {}).get("kind"), 20), "repro_cmd": str(o.get("repro", {}).get("cmd", ""))[:2000], "suspect": clean(json.dumps(o.get("suspect", "")), 200),
                        "replay": {"status": clean(rp.get("status", "none"), 20), "fail_rate": num(rp.get("fail_rate")), "touches_target": bool(rp.get("touches_target")),
                                   "runs": [{"rc": x.get("rc") if isinstance(x.get("rc"), int) else None, "secs": num(x.get("secs")), "tail": clean(x.get("tail", ""), 300)} for x in rp.get("runs", [])[:50]]}})
    v = (jload(udir(r) / "verdicts.json", {}) or {}).get(cid, {})
    return {"cid": cid, "title": clean(c.get("title"), 160), "severity": c.get("severity") if c.get("severity") in SEV else "nit", "area": clean(c.get("area"), 80), "rule_id": clean(c.get("rule_id"), 40),
            "verdict": clean(c.get("verdict"), 20), "replay": clean(c.get("replay"), 20), "merge_reasons": [clean(x, 40) for x in c.get("merge_reasons", [])], "agents": [clean(x, 40) for x in c.get("agents", [])],
            "families": [clean(x, 30) for x in c.get("families", [])], "members": members, "verifier": {"verdict": clean(v.get("verdict"), 10), "note": clean(v.get("note"), 300), "by": clean(v.get("by"), 40)}}


def audit_view(r, q="", limit=500):
    rows = audit_read(r)
    out = [{"seq": x.get("seq"), "ts": x.get("ts"), "actor": clean(x.get("actor"), 60), "type": clean(x.get("type"), 40), "data": clean(json.dumps(x.get("data", {}), sort_keys=True), 400), "hash": str(x.get("hash", ""))[:12], "source": "local"}
           for x in rows if isinstance(x, dict) and not x.get("broken")]
    for e in read_events(r):                       # host-side records of an ssh run: shown, but outside the verified chain
        pass
    ql = q.lower()
    out = [x for x in out if not ql or ql in (x["actor"] + " " + x["type"] + " " + x["data"]).lower()]
    return out[-limit:][::-1]


DOWNLOADS = {"report.md": ("report", "text/plain; charset=utf-8"), "findings.json": ("report", "application/json"), "junit.xml": ("report", "application/xml")}


def make_handler(r, token, port):
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

        def _host_ok(self):
            return self.headers.get("Host", "") in (f"127.0.0.1:{port}", f"localhost:{port}")  # blocks DNS rebinding

        nonce = ""

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

        def do_POST(self):
            origin = self.headers.get("Origin", "")
            if not self._host_ok() or (origin and origin not in (f"http://127.0.0.1:{port}", f"http://localhost:{port}")) or self.headers.get("X-UAT-Token") != token:
                return self._send(403, "forbidden")
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0)) or 0) or b"{}")
            valid = {i["cid"] for i in fixq.load(r)}
            ids = [i for i in body.get("ids", []) if i in valid]
            if self.path == "/api/fix/approve":
                done = fixq.approve(r, ids or None, body.get("severities") or None, by="user (dashboard)") if (ids or body.get("severities")) else []
            elif self.path == "/api/fix/reject":
                done = [fixq.set_state(r, c, "rejected", by="user (dashboard)")["cid"] for c in ids]
            else:
                return self._send(404, "not found")
            self._send(200, json.dumps({"changed": done}))

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
    token = secrets.token_urlsafe(16)
    srv = ThreadingHTTPServer(("127.0.0.1", port), make_handler(r, token, port))
    # the approval token is never served by the web server: only whoever sees this terminal line (or the /uat skill output) has it
    print(f"UAT dashboard: http://127.0.0.1:{port}/#{token}  (Ctrl+C to stop)", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
