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
from uatlib import HOME, SEV, agent_meta, clean, config, is_remote, jload, read_events, safe_read, udir  # noqa: E402

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
    return {
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


def make_handler(r, token, port):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, body, ctype="application/json"):
            b = body if isinstance(body, bytes) else body.encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(b)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            if ctype.startswith("text/html"):
                self.send_header("Content-Security-Policy", f"default-src 'none'; script-src 'nonce-{self.nonce}'; style-src 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
            self.end_headers()
            self.wfile.write(b)

        def _host_ok(self):
            return self.headers.get("Host", "") in (f"127.0.0.1:{port}", f"localhost:{port}")  # blocks DNS rebinding

        nonce = ""

        def do_GET(self):
            self.nonce = secrets.token_urlsafe(12)
            if not self._host_ok():
                return self._send(403, "bad host")
            if self.path == "/api/state":
                return self._send(200, json.dumps(snapshot(r)))
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
                done = fixq.approve(r, ids or None, body.get("severities") or None) if (ids or body.get("severities")) else []
            elif self.path == "/api/fix/reject":
                done = [fixq.set_state(r, c, "rejected", by="user")["cid"] for c in ids]
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
