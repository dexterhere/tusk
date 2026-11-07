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
from uatlib import HOME, config, is_remote, jload, read_events, udir  # noqa: E402

WEB = HOME / "web" / "index.html"


def snapshot(r):
    u = udir(r)
    cfg = config(r)
    events = read_events(r)
    plan = jload(u / "plan" / "plan.json", {})
    agents = {}
    for d in sorted((u / "agents").glob("*")) if (u / "agents").exists() else []:
        meta = jload(d / "agent.json", {})
        agents[d.name] = {"id": d.name, "status": "queued", "findings": len(list((d / "findings").glob("F-*.json"))), "now": "", "checks": {}, **meta}
    for e in events:
        a = agents.setdefault(e["actor"], {"id": e["actor"], "status": "queued", "findings": 0, "now": "", "checks": {}}) if e["type"] in ("agent_start", "fix_start") or e["actor"] in agents else None
        if a is None:
            continue
        t = e["type"]
        if t in ("agent_start", "fix_start"):
            a.update({"status": "running", "started": e["ts"], "family": e.get("family", a.get("family")), "model": e.get("model", a.get("model")), "lane": e.get("lane"), "role": "fix" if t == "fix_start" else "tester",
                      "charter": e.get("charter", a.get("charter")), "now": "starting"})
        elif t == "agent_status":
            a.update({"now": e.get("now") or a["now"], "findings": e.get("findings", a["findings"])})
        elif t == "step":
            a["now"] = e["text"]
        elif t == "check":
            a["checks"][e["rule"]] = e["result"]
        elif t in ("agent_done", "fix_done"):
            a.update({"status": "tamper" if e.get("tamper") else "done" if e.get("rc", 0) == 0 else "failed", "ended": e["ts"], "findings": e.get("findings", a["findings"]), "now": e.get("note", "finished")})
    clusters = jload(u / "clusters.json", [])
    led = [json.loads(x) for x in (u / "ledger.jsonl").read_text().splitlines()] if (u / "ledger.jsonl").exists() else []
    rounds = [json.loads(x) for x in (u / "rounds.jsonl").read_text().splitlines()] if (u / "rounds.jsonl").exists() else []
    return {
        "now": time.time(), "project": r.name, "state": jload(u / "state.json", {}), "orchestrator": jload(u / "orchestrator.json", {"name": "Orchestrator", "model": "claude"}),
        "target": {"mode": cfg["target"]["mode"], "host": cfg["target"]["ssh"]["host"] if is_remote(cfg) else "this computer", "kind": cfg["env"]["kind"]},
        "sandbox": sorted({x.get("sandbox", "?") for x in led if not x.get("ping")}), "agents": list(agents.values()), "rules": plan.get("rules", []), "charters": plan.get("charters", []),
        "clusters": clusters, "fixqueue": fixq.load(r), "rounds": rounds, "events": events[-80:], "report": (u / "report" / "report.md").exists(),
        "score": (jload(u / "report" / "findings.json", {}) or {}).get("health_score"),
    }


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
            self.end_headers()
            self.wfile.write(b)

        def _host_ok(self):
            return self.headers.get("Host", "") in (f"127.0.0.1:{port}", f"localhost:{port}")  # blocks DNS rebinding

        def do_GET(self):
            if not self._host_ok():
                return self._send(403, "bad host")
            if self.path == "/api/state":
                return self._send(200, json.dumps(snapshot(r)))
            if self.path in ("/", "/index.html"):
                return self._send(200, WEB.read_text().replace("__TOKEN__", token), "text/html; charset=utf-8")
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
    print(f"UAT dashboard: http://127.0.0.1:{port}  (Ctrl+C to stop)", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
