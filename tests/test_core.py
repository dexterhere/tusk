"""End-to-end check of the deterministic pipeline: validate -> replay -> dedupe -> report, plus guard/detect/run_agent.
Run: python3 -m unittest discover -s tests -v   (from the uat-swarm dir)
"""
import json
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

HOME = Path(__file__).resolve().parent.parent
S = HOME / "scripts"
DEMO = HOME / "tests" / "demo_app"
PY = sys.executable


def run(script, *args, cwd=None):
    return subprocess.run([PY, str(S / script), *args], capture_output=True, text=True, cwd=cwd)


def finding(fid, agent, family, rule, title, probe, sev="major", area="cart", cmd=None):
    return {
        "id": fid, "agent": agent, "family": family, "model": "m", "lens": "breaker", "area": area, "rule_id": rule, "title": title,
        "severity": sev, "category": "functional", "expected": "rule holds", "actual": "rule violated",
        "repro": {"kind": "shell", "cmd": cmd or f"{PY} {DEMO / 'probe.py'} {probe}"}, "evidence": "probe exit 1",
    }


def free_port_block(n=4):
    while True:
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        p = s.getsockname()[1]
        s.close()
        if p < 60000 and all(_free(p + i) for i in range(n)):
            return p


def _free(port):
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port)) != 0


def mkroot(tmp, over=None):
    """A UAT project dir whose golden workspace holds the demo app; lanes run it on their own ports."""
    root = Path(tmp)
    (root / ".uat").mkdir(exist_ok=True)
    port = free_port_block()
    cfg = {"env": {"base_port": port, "base_url": f"http://127.0.0.1:{port}", "health_url": f"http://127.0.0.1:{port}/", "health_timeout_s": 15,
                   "start_cmd": "python3 demo_app/app.py {port}"}}
    for k, v in (over or {}).items():
        cfg[k] = {**cfg.get(k, {}), **v}
    (root / ".uat" / "uat.json").write_text(json.dumps(cfg))
    shutil.copytree(DEMO, root / "demo_app", ignore=shutil.ignore_patterns("__pycache__"))
    r = run("uat_env.py", "workspace", "--root", str(root))
    assert r.returncode == 0, r.stderr
    return root


class Pipeline(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = mkroot(cls.tmp.name, {"swarm": {"max_parallel": 3}, "replay": {"runs": 3}})
        a1 = cls.root / ".uat/agents/A1/findings"
        a2 = cls.root / ".uat/agents/A2/findings"
        a3 = cls.root / ".uat/agents/A3/findings"
        for d in (a1, a2, a3):
            d.mkdir(parents=True)
        fs = [
            (a1, finding("F-A1-1", "A1", "claude", "R-CART-01", "Negative quantity accepted in cart", "neg_qty")),
            (a1, finding("F-A1-2", "A1", "claude", "R-PRICE-02", "Coupon stacks when applied twice", "double_coupon", area="pricing")),
            (a1, finding("F-A1-3", "A1", "claude", "R-ORDER-01", "Empty cart checkout succeeds", "empty_checkout", area="orders")),
            (a1, finding("F-A1-4", "A1", "claude", "R-PRICE-01", "Order total not rounded to cents", "float_total", sev="minor", area="pricing")),
            (a1, finding("F-A1-5", "A1", "claude", "R-AUTH-01", "Any user can read another user's order", "idor", sev="blocker", area="orders")),
            (a2, finding("F-A2-1", "A2", "codex", "R-PRICE-01", "Totals show floating point noise like 0.30000000000000004", "float_total", sev="minor", area="pricing")),  # duplicate cluster
            (a2, finding("F-A2-2", "A2", "codex", "R-FLAKY-01", "Intermittent failure on checkout path", "x", area="flaky", cmd=f"{PY} -c \"import pathlib,sys;p=pathlib.Path('n');n=int(p.read_text()) if p.exists() else 0;p.write_text(str(n+1));sys.exit(int(n%3==0))\"")),
            (a3, finding("F-A3-1", "A3", "opencode", "R-CART-03", "Carts leak between different users", "cart_isolation", area="decoy")),  # false positive
            (a3, {"id": "F-A3-2", "agent": "A3", "title": "bad"}),  # invalid
        ]
        for d, f in fs:
            (d / (f["id"] + ".json")).write_text(json.dumps(f))

    @classmethod
    def tearDownClass(cls):
        run("uat_env.py", "lanes", "down", "--root", str(cls.root))
        cls.tmp.cleanup()

    def test_pipeline(self):
        r = str(self.root)
        v = run("validate_findings.py", "--root", r)
        self.assertIn("valid=8 rejected=1", v.stdout)
        rp = run("replay.py", "--root", r)
        self.assertEqual(rp.returncode, 0, rp.stderr)
        self.assertIn("'confirmed': 6", rp.stdout)  # 5 bugs + 1 duplicate
        self.assertIn("'not_reproducible': 1", rp.stdout)  # the decoy
        self.assertIn("'flaky': 1", rp.stdout)
        d = run("dedupe.py", "--root", r)
        self.assertEqual(d.returncode, 0, d.stderr)
        clusters = json.loads((self.root / ".uat/clusters.json").read_text())
        self.assertEqual(len(clusters), 7)  # 5 bugs + flaky + decoy; duplicate merged
        price = next(c for c in clusters if c["rule_id"] == "R-PRICE-01")
        self.assertEqual(price["agreement"], 2)
        self.assertEqual(price["families"], ["claude", "codex"])
        verdicts = {c["rule_id"]: c["verdict"] for c in clusters}
        self.assertEqual(verdicts["R-CART-03"], "drop")  # single-agent false positive
        self.assertEqual(verdicts["R-FLAKY-01"], "needs_verifier")
        rep = run("report.py", "--root", r, "--save-baseline")
        self.assertEqual(rep.returncode, 0, rep.stderr)
        out = json.loads((self.root / ".uat/report/findings.json").read_text())
        self.assertGreaterEqual(len(out["findings"]), 5)
        self.assertTrue((self.root / ".uat/report/report.md").exists())
        self.assertTrue((self.root / ".uat/report/junit.xml").exists())
        self.assertTrue(any((self.root / ".uat/regression").iterdir()))
        sev = {f["rule_id"]: f["severity"] for f in out["findings"]}
        self.assertEqual(sev["R-AUTH-01"], "blocker")
        cal = run("calibrate.py", "--root", r, "--seeded", str(DEMO / "seeded_bugs.json"))
        self.assertIn("claude", cal.stdout)
        self.assertIn("5/5", cal.stdout)  # claude found all 5 seeded bugs, no false ones


class Misc(unittest.TestCase):
    def test_guard(self):
        with tempfile.TemporaryDirectory() as t:
            (Path(t) / ".env").write_text("DATABASE_URL=postgres://u:p@prod-db.example.com:5432/app\n")
            self.assertEqual(run("uat_env.py", "guard", "--root", t).returncode, 1)
            (Path(t) / ".env").write_text("DATABASE_URL=postgres://u:p@localhost:5432/app\n")
            self.assertEqual(run("uat_env.py", "guard", "--root", t).returncode, 0)
            (Path(t) / "c.json").write_text('{"k": "AKIAABCDEFGHIJKLMNOP"}')
            self.assertEqual(run("uat_env.py", "guard", "--root", t).returncode, 1)

    def test_detect(self):
        with tempfile.TemporaryDirectory() as t:
            (Path(t) / "package.json").write_text('{"dependencies": {"react": "1", "express": "4"}}')
            (Path(t) / "src").mkdir()
            (Path(t) / "src" / "a.js").write_text("x")
            out = json.loads(run("uat_detect.py", "--root", t).stdout)
            self.assertIn("web-ui", out["tags"])
            self.assertIn("api", out["tags"])
            self.assertEqual(out["recommended_agents"] % 3, 0)
            self.assertTrue(any(x["name"] == "playwright" for x in out["tools"]))

    def test_run_agent_ledger_and_timeout(self):
        with tempfile.TemporaryDirectory() as t:
            mkroot(t)
            r = run("run_agent.py", "--root", t, "--id", "A9", "--family", "codex", "--model", "m", "--lane", "L1", "--cmd", "echo hi")
            self.assertEqual(r.returncode, 0, r.stderr)
            row = json.loads((Path(t) / ".uat/ledger.jsonl").read_text().splitlines()[0])
            self.assertEqual(row["id"], "A9")
            self.assertFalse(row["tamper"])
            r = run("run_agent.py", "--root", t, "--id", "A8", "--family", "codex", "--model", "m", "--lane", "L1", "--cmd", "sleep 30", "--timeout", "1")
            self.assertEqual(r.returncode, 1)
            run("uat_env.py", "lanes", "down", "--root", t)

    def test_tools_dry_run(self):
        r = run("uat_tools.py", "install", "schemathesis", "k6", "hurl", "--dry-run", "--root", str(HOME))
        self.assertIn("pip", r.stdout)
        self.assertIn("docker pull grafana/k6", r.stdout)
        self.assertIn("manual", r.stdout)


if __name__ == "__main__":
    unittest.main()
