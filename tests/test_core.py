"""End-to-end check of the deterministic pipeline: validate -> replay -> dedupe -> report, plus guard/detect/run_agent.
Run: python3 -m unittest discover -s tests -v   (from the uat-swarm dir)
"""
import json
import os
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
        "repro": {"kind": "shell", "cmd": cmd or f'UAT_BASE_URL="$UAT_BASE_URL" {PY} {DEMO / "probe.py"} {probe}'}, "evidence": "probe exit 1",
    }


def race(f):
    f["category"] = "concurrency"      # stress replay: 12 runs, so a 50% failure cannot look stable
    return f


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
                   "start_cmd": "python3 demo_app/app.py {port}", "allow_denylist_copy": True}}
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
            (a2, race(finding("F-A2-2", "A2", "codex", "R-FLAKY-01", "Intermittent failure on checkout path", "x", area="flaky", cmd=': "$UAT_BASE_URL"; [ $((RANDOM % 2)) -eq 0 ]'))),
            (a3, finding("F-A3-1", "A3", "opencode", "R-CART-03", "Carts leak between different users", "cart_isolation", area="decoy")),  # false positive
            (a3, {"id": "F-A3-2", "agent": "A3", "title": "bad"}),  # invalid
        ]
        for d, f in fs:
            (d / (f["id"] + ".json")).write_text(json.dumps(f))
        for aid, fam in (("A1", "claude"), ("A2", "codex"), ("A3", "opencode")):
            (cls.root / ".uat/meta").mkdir(exist_ok=True)
            (cls.root / ".uat/meta" / f"{aid}.json").write_text(json.dumps({"id": aid, "family": fam, "model": "m", "lens": "breaker"}))

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
        self.assertTrue(all(len(c["cid"]) == 14 for c in clusters))
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


class Hardening(unittest.TestCase):
    """The five weaknesses from the first audit, each pinned by a test."""

    def test_dedupe_does_not_over_merge(self):
        # similar wording, different rule ids and different code: must stay two clusters
        with tempfile.TemporaryDirectory() as t:
            root = mkroot(t)
            for aid, rule, title in (("A1", "R-PRICE-01", "Order total shows wrong amount after coupon applied"), ("A2", "R-PRICE-07", "Order total shows wrong amount after refund applied")):
                d = root / ".uat/agents" / aid / "findings"
                d.mkdir(parents=True)
                (d / f"F-{aid}.json").write_text(json.dumps(finding(f"F-{aid}", aid, "claude", rule, title, "neg_qty", area="pricing", cmd=f"true {aid}")))
            run("replay.py", "--root", t)
            run("dedupe.py", "--root", t)
            self.assertEqual(len(json.loads((root / ".uat/clusters.json").read_text())), 2)
            run("uat_env.py", "lanes", "down", "--root", t)

    def test_single_agent_race_is_kept_not_dropped(self):
        with tempfile.TemporaryDirectory() as t:
            root = mkroot(t)
            d = root / ".uat/agents/A1/findings"
            d.mkdir(parents=True)
            f = finding("F-R1", "A1", "claude", "R-RACE-01", "Parallel checkout charges twice", "x", area="orders", cmd="exit 0")
            (root / ".uat/meta").mkdir(exist_ok=True)
            (root / ".uat/meta/A1.json").write_text(json.dumps({"family": "claude", "model": "m", "lens": "breaker"}))
            f["category"] = "concurrency"
            (d / "F-R1.json").write_text(json.dumps(f))
            run("replay.py", "--root", t)
            run("dedupe.py", "--root", t)
            c = json.loads((root / ".uat/clusters.json").read_text())[0]
            self.assertEqual(c["verdict"], "watch")       # not reproduced, but never silently dropped
            run("uat_env.py", "lanes", "down", "--root", t)

    def test_parallel_lanes_are_isolated(self):
        with tempfile.TemporaryDirectory() as t:
            root = mkroot(t, {"swarm": {"max_parallel": 3}})
            r = run("uat_env.py", "lanes", "up", "--root", t)
            self.assertEqual(r.returncode, 0, r.stderr)
            ports = {line.split()[2] for line in r.stdout.splitlines() if line.startswith("L")}
            self.assertEqual(len(ports), 3)
            run("uat_env.py", "lanes", "down", "--root", t)

    def test_tamper_is_attributed_and_reverted(self):
        with tempfile.TemporaryDirectory() as t:
            root = mkroot(t)
            sh = f"echo x >> $UAT_WS/demo_app/rules.json"
            r = run("run_agent.py", "--root", t, "--id", "A1", "--family", "codex", "--model", "m", "--lane", "L1", "--cmd", "bash -c true")
            self.assertEqual(r.returncode, 0, r.stderr)
            (root / ".uat/lanes/L1/ws/demo_app/rules.json").write_text("tampered")
            self.assertEqual(run("uat_env.py", "lane", "tamper", "L1", "--root", t).returncode, 2)
            self.assertEqual(run("uat_env.py", "lane", "tamper", "L1", "--root", t).returncode, 0)  # reverted
            run("uat_env.py", "lanes", "down", "--root", t)

    @unittest.skipUnless(shutil.which("bwrap"), "bubblewrap not installed")
    def test_sandbox_confines_agent_commands(self):
        sys.path.insert(0, str(S))
        import sandbox
        import uatlib
        cfg = uatlib.config(tempfile.gettempdir())
        with tempfile.TemporaryDirectory() as t:
            d = Path(t) / "a"
            d.mkdir()
            probe = "touch /usr/x 2>/dev/null; echo usr=$?; ls /run | grep -c docker; ls ~/.ssh 2>/dev/null | wc -l; touch ok; echo wrote=$?"
            p = subprocess.run(sandbox.wrap(cfg, ["bash", "-c", probe], cwd=d), capture_output=True, text=True)
            self.assertEqual(p.stdout.split(), ["usr=1", "0", "0", "wrote=0"], p.stderr)
            self.assertTrue((d / "ok").exists())
            env = sandbox.clean_env(cfg, {"UAT_X": "1"})
            os.environ["MY_API_KEY"] = "secret"
            self.assertNotIn("MY_API_KEY", sandbox.clean_env(cfg))
            self.assertIn("UAT_X", env)

    def test_symlinked_findings_dir_is_ignored(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            real = root / "elsewhere"
            real.mkdir()
            (real / "F-1.json").write_text("{}")
            agent = root / ".uat/agents/A1"
            agent.mkdir(parents=True)
            (agent / "findings").symlink_to(real)
            sys.path.insert(0, str(S))
            import uatlib
            self.assertEqual(uatlib.finding_files(root), [])

    def test_fix_queue_needs_approval(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            (root / ".uat").mkdir()
            c = {"cid": "C-1", "fp": "x", "title": "Totals wrong", "severity": "major", "category": "functional", "area": "pricing", "rule_id": "R-1", "agreement": 1,
                 "fail_rate": 1.0, "kind": "defect", "verdict": "accept", "members": [], "files": [], "digest": "d1"}
            (root / ".uat/clusters.json").write_text(json.dumps([c]))
            run("fixq.py", "sync", "--root", t)
            q = json.loads((root / ".uat/fixqueue.json").read_text())
            self.assertEqual(q[0]["state"], "proposed")        # nothing starts unasked
            run("fixq.py", "approve", "--severity", "blocker", "--root", t)
            self.assertEqual(json.loads((root / ".uat/fixqueue.json").read_text())[0]["state"], "proposed")
            run("fixq.py", "approve", "C-1", "--root", t)
            self.assertEqual(json.loads((root / ".uat/fixqueue.json").read_text())[0]["state"], "approved")

    def test_dashboard_rejects_foreign_host_and_missing_token(self):
        import urllib.request
        import urllib.error
        with tempfile.TemporaryDirectory() as t:
            (Path(t) / ".uat").mkdir()
            port = free_port_block(1)
            srv = subprocess.Popen([PY, str(S / "dashboard.py"), "--root", t, "--port", str(port)], stdout=subprocess.DEVNULL)
            try:
                for _ in range(40):
                    if not _free(port):
                        break
                    time.sleep(0.1)
                ok = urllib.request.urlopen(f"http://127.0.0.1:{port}/api/state").read()
                self.assertIn(b'"agents"', ok)
                page = urllib.request.urlopen(f"http://127.0.0.1:{port}/")
                html = page.read().decode()
                self.assertNotIn("TOKEN=\"", html)                                  # the approval token is never served
                self.assertIn("script-src 'nonce-", page.headers["Content-Security-Policy"])
                self.assertEqual(page.headers["X-Frame-Options"], "DENY")
                req = urllib.request.Request(f"http://127.0.0.1:{port}/api/state", headers={"Host": "evil.example"})
                with self.assertRaises(urllib.error.HTTPError) as e:
                    urllib.request.urlopen(req)
                self.assertEqual(e.exception.code, 403)
                post = urllib.request.Request(f"http://127.0.0.1:{port}/api/fix/approve", data=b"{}", method="POST")
                with self.assertRaises(urllib.error.HTTPError) as e:
                    urllib.request.urlopen(post)
                self.assertEqual(e.exception.code, 403)
            finally:
                srv.terminate()


class Review(unittest.TestCase):
    """Findings from the independent Codex / OpenCode security reviews, each pinned."""

    def _replay(self, t, fid, cmd, files=None, **over):
        root = mkroot(t)
        d = root / ".uat/agents/A1/findings"
        d.mkdir(parents=True)
        (root / ".uat/meta").mkdir(exist_ok=True)
        (root / ".uat/meta/A1.json").write_text(json.dumps({"family": "claude", "model": "m", "lens": "breaker"}))
        f = finding(fid, "A1", "claude", "R-X-01", "Something is wrong with the cart", "x", cmd=cmd)
        f.update(over)
        if files:
            f["repro"]["files"] = list(files)
            for n, body in files.items():
                (root / ".uat/agents/A1" / n).write_text(body)
        (d / f"{fid}.json").write_text(json.dumps(f))
        run("replay.py", "--root", t)
        run("dedupe.py", "--root", t)
        return root, json.loads((root / ".uat/clusters.json").read_text())[0]

    def test_exit_code_policy_and_target_check(self):
        with tempfile.TemporaryDirectory() as t:
            _, c = self._replay(t, "F-1", "exit 2")                       # broken repro (syntax-error style code)
            self.assertEqual(c["replay"], "error")
            run("uat_env.py", "lanes", "down", "--root", t)
        with tempfile.TemporaryDirectory() as t:
            _, c = self._replay(t, "F-1", "exit 1")                       # fails, never touches the target
            self.assertEqual((c["replay"], c["verdict"]), ("confirmed", "needs_verifier"))
            run("uat_env.py", "lanes", "down", "--root", t)

    def test_repro_cannot_rewrite_its_own_finding(self):
        with tempfile.TemporaryDirectory() as t:
            root = mkroot(t)
            d = root / ".uat/agents/A1/findings"
            d.mkdir(parents=True)
            evil = 'echo "$UAT_BASE_URL"; python3 -c "import json,glob;[open(p,\'w\').write(\'{}\') for p in glob.glob(\'../findings/*.json\')]"; exit 1'
            f = finding("F-1", "A1", "claude", "R-X-01", "Something is wrong with the cart", "x", cmd=evil)
            (d / "F-1.json").write_text(json.dumps(f))
            run("replay.py", "--root", t)
            self.assertEqual(json.loads((d / "F-1.json").read_text())["id"], "F-1")      # live file untouched (scratch cwd)
            trusted = json.loads((root / ".uat/trusted/A1/F-1/finding.json").read_text())
            self.assertEqual(trusted["repro"]["cmd"], evil)
            self.assertTrue((root / ".uat/replays/A1/F-1.replay.json").exists())
            run("uat_env.py", "lanes", "down", "--root", t)

    def test_empty_fix_proof_fails(self):
        with tempfile.TemporaryDirectory() as t:
            root = mkroot(t)
            (root / ".uat/clusters.json").write_text("[]")
            self.assertNotEqual(run("replay.py", "--root", t, "--only", "C-nope", "--passing").returncode, 0)
            self.assertNotEqual(run("replay.py", "--root", t, "--passing").returncode, 0)

    def test_identity_comes_from_directory_not_finding(self):
        with tempfile.TemporaryDirectory() as t:
            root, c = self._replay(t, "F-1", 'UAT_BASE_URL="$UAT_BASE_URL" true; exit 1', agent="A99", family="codex")
            self.assertEqual(c["agents"], ["A1"])
            self.assertEqual(c["families"], ["claude"])
            run("uat_env.py", "lanes", "down", "--root", t)

    def test_report_neutralises_agent_text(self):
        with tempfile.TemporaryDirectory() as t:
            root, c = self._replay(t, "F-1", 'echo $UAT_BASE_URL >/dev/null; exit 1', title="Totals wrong\n## C-fake [BLOCKER] pwned [x](http://e.com)",
                                   actual="seen `rm -rf ~` <img src=x onerror=alert(1)> \x1b[31m")
            run("report.py", "--root", t)
            md = (root / ".uat/report/report.md").read_text()
            self.assertNotIn("\n## C-fake", md)
            self.assertNotRegex(md, r"(?<!\\)<img")                       # only the backslash-escaped form may appear
            self.assertNotIn("\x1b", md)
            self.assertNotIn("rm -rf ~`", md.replace("```", ""))
            run("uat_env.py", "lanes", "down", "--root", t)

    def test_fix_approval_resets_when_content_changes(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            (root / ".uat").mkdir()
            c = {"cid": "C-aaaaaaaaaaaa", "fp": "x", "title": "Totals wrong", "severity": "major", "category": "functional", "area": "pricing", "rule_id": "R-1", "agreement": 1,
                 "fail_rate": 1.0, "kind": "defect", "verdict": "accept", "members": [], "files": [], "digest": "d1"}
            cl = root / ".uat/clusters.json"
            cl.write_text(json.dumps([c]))
            run("fixq.py", "sync", "--root", t)
            run("fixq.py", "approve", "C-aaaaaaaaaaaa", "--root", t)
            c["digest"] = "d2"
            c["title"] = "Something else entirely"
            cl.write_text(json.dumps([c]))
            run("fixq.py", "sync", "--root", t)
            self.assertEqual(json.loads((root / ".uat/fixqueue.json").read_text())[0]["state"], "proposed")
            cl.write_text("[]")
            run("fixq.py", "sync", "--root", t)
            self.assertEqual(json.loads((root / ".uat/fixqueue.json").read_text())[0]["state"], "stale")

    def test_hostile_events_do_not_break_or_inject(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            d = root / ".uat/agents/A1"
            d.mkdir(parents=True)
            (d / "events.jsonl").write_text("\n".join([
                '{"ts":"x","type":"step"}', "[1]", '{"ts":9e99,"type":"agent_status","findings":"<img src=x onerror=1>"}',
                '{"ts":1,"type":"step","text":"ok \u001b[31m probe"}']) + "\n")
            (d / "agent.json").symlink_to("/etc/passwd")
            sys.path.insert(0, str(S))
            import dashboard
            snap = dashboard.snapshot(root)
            a = next(x for x in snap["agents"] if x["id"] == "A1")
            self.assertIsInstance(a["findings"], int)
            self.assertNotIn("\x1b", json.dumps(snap))
            self.assertTrue(all(e["type"] in ("step", "check") for e in snap["events"] if e["actor"] == "A1"))

    def test_ssh_host_validation_and_mirror_allowlist(self):
        sys.path.insert(0, str(S))
        import host
        import uatlib
        cfg = uatlib.config(tempfile.gettempdir())
        cfg["target"]["ssh"]["host"] = "-oProxyCommand=evil"
        with self.assertRaises(SystemExit):
            host.ssh_argv(cfg)
        for forbidden in ("verdicts.json", "clusters.json", "fixqueue.json", "report/", "plan/", "state.json"):
            self.assertFalse(any(forbidden.startswith(x.rstrip("*/")) and x.rstrip("*/") for x in host.MIRROR_IN if x.startswith(forbidden[:4])), forbidden)
        cfg["target"]["ssh"]["host"] = "me@vps.example.com"
        self.assertEqual(host.ssh_argv(cfg)[-2:], ["--", "me@vps.example.com"])

    def test_scrub_skips_untracked_and_secret_names(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            subprocess.run(["git", "init", "-q"], cwd=t)
            (root / "app.py").write_text("x")
            (root / ".npmrc").write_text("//registry:_authToken=abc")
            (root / ".gitignore").write_text("local.env\n")
            (root / "local.env").write_text("SECRET=1")
            (root / ".uat").mkdir()
            run("uat_env.py", "workspace", "--root", t)
            ws = root / ".uat/workspace"
            self.assertTrue((ws / "app.py").exists())
            self.assertFalse((ws / ".npmrc").exists())
            self.assertFalse((ws / "local.env").exists())

    def test_stage_fails_closed_without_git_listing(self):
        with tempfile.TemporaryDirectory() as t:
            (Path(t) / "app.py").write_text("x")
            (Path(t) / "local.env").write_text("SECRET=1")
            (Path(t) / ".uat").mkdir()
            r = run("uat_env.py", "workspace", "--root", t)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("allow_denylist_copy", r.stderr)
            self.assertFalse((Path(t) / ".uat/workspace/local.env").exists())


class SshMode(unittest.TestCase):
    """Target = ssh host, exercised with a shim that runs the 'remote' side in a second local directory."""

    @unittest.skipUnless(shutil.which("rsync") and shutil.which("bwrap"), "needs rsync and bwrap")
    def test_remote_workspace_lane_agent_and_mirror(self):
        with tempfile.TemporaryDirectory() as t:
            shim = Path(t) / "fakessh"
            shim.write_text('#!/bin/bash\n[ "$1" = fake ] && shift\nif [ $# -eq 1 ]; then exec bash -c "$1"; else exec "$@"; fi\n')
            shim.chmod(0o755)
            local, remote = Path(t) / "local", Path(t) / "remote"
            local.mkdir()
            root = mkroot(str(local))
            port = free_port_block()
            cfg = json.loads((root / ".uat/uat.json").read_text())
            cfg.update({"target": {"mode": "ssh", "ssh": {"host": "fake", "port": 22, "identity": "", "dir": str(remote)}}})
            cfg["env"].update({"base_port": port, "base_url": f"http://127.0.0.1:{port}", "health_url": f"http://127.0.0.1:{port}/"})
            (root / ".uat/uat.json").write_text(json.dumps(cfg))
            shutil.rmtree(root / ".uat/workspace")
            env = {**os.environ, "UAT_SSH_CMD": str(shim)}

            def uat(*a):
                return subprocess.run([PY, str(S / "uat.py"), *a, "--root", str(root)], capture_output=True, text=True, env=env)

            r = uat("uat_env", "workspace")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertTrue((remote / ".uat/workspace/demo_app/app.py").exists())          # pushed, baseline made on the "host"
            self.assertTrue((remote / ".uat/workspace/.git").exists())
            (root / ".uat/agents/A1/findings").mkdir(parents=True)
            r = uat("run_agent", "--id", "A1", "--lane", "L1", "--family", "codex", "--model", "m", "--cmd",
                    "touch findings/F-1.json")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr + (root / ".uat/events.host.jsonl").read_text()[-600:])
            self.assertTrue((root / ".uat/ledger.jsonl").exists())                          # mirrored back
            self.assertTrue((root / ".uat/agents/A1/findings/F-1.json").exists())
            self.assertTrue(any(root.glob(".uat/events.host.jsonl")))                       # host events do not clobber local events.jsonl
            self.assertFalse((root / ".uat/lanes").exists())                                # heavy trees are never mirrored
            uat("uat_env", "lanes", "down")
