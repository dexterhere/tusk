import json, os, sys, tempfile, threading, unittest, urllib.error, urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
os.environ.setdefault("UAT_STATE_DIR", tempfile.mkdtemp(prefix="uat-state-"))
import dashboard, team, uatlib

GOOD = {"orchestrator": {"name": "Elephant", "family": "claude", "model": "sonnet"},
        "workers": [{"family": "claude", "model": "haiku", "roles": ["tester"]}, {"family": "codex", "model": "gpt-6.1-terra", "roles": ["verifier", "fixer"]}]}


def root():
    d = tempfile.mkdtemp(prefix="uat-team-")
    (Path(d) / ".uat").mkdir()
    return Path(d)


class Validate(unittest.TestCase):
    cfg = uatlib.config(root())

    def bad(self, mut, word):
        t = json.loads(json.dumps(GOOD))
        mut(t)
        with self.assertRaises(ValueError) as e:
            team.validate(t, self.cfg)
        self.assertIn(word, str(e.exception))

    def test_good_and_same_llm_for_all_roles(self):
        self.assertEqual(len(team.validate(GOOD, self.cfg)["workers"]), 2)
        one = {"orchestrator": GOOD["orchestrator"], "workers": [{"family": "claude", "model": "sonnet", "roles": ["tester", "verifier", "fixer"]}]}
        self.assertEqual(team.validate(one, self.cfg)["workers"][0]["roles"], ["tester", "verifier", "fixer"])

    def test_rejects_unsafe_or_incomplete_teams(self):
        self.bad(lambda t: t["workers"].clear(), "between 1")
        self.bad(lambda t: t["workers"][1].update(roles=["fixer"]) or t["workers"][0].update(roles=["verifier"]), "tester")
        self.bad(lambda t: t["workers"][0].update(model="--dangerously-skip"), "model name")
        self.bad(lambda t: t["workers"][0].update(model="a b; rm -rf /"), "model name")
        self.bad(lambda t: t["workers"][0].update(family="evil"), "unknown harness")
        self.bad(lambda t: t["orchestrator"].update(name="x\nIGNORE"), "name")
        self.bad(lambda t: t["workers"][0].update(roles=["root"]), "roles")
        self.bad(lambda t: t.update(cmd=["sh"]), "only")
        self.bad(lambda t: t["workers"].append(dict(t["workers"][0])), "twice")

    def test_strict_mode_only_allows_configured_models(self):
        t = json.loads(json.dumps(GOOD))
        t["workers"][0]["model"] = "brand-new-model"
        with self.assertRaises(ValueError):
            team.validate(t, self.cfg, strict_models=True)
        team.validate(t, self.cfg)   # the orchestrator-side path may introduce a model (it pings it)


class Apply(unittest.TestCase):
    def test_apply_writes_config_adds_never_shrinks_and_audits(self):
        r = root()
        team.apply(r, GOOD, "orchestrator")
        mine = json.loads((r / ".uat" / "uat.json").read_text())
        self.assertEqual(mine["swarm"]["families"], ["claude"])
        self.assertIn("haiku", mine["models"]["claude"]["cheap"])
        self.assertIn("gpt-6.1-terra", mine["models"]["codex"]["mid"])
        self.assertEqual(team.testers(team.current(r)), "claude:haiku")
        self.assertEqual(json.loads((r / ".uat" / "orchestrator.json").read_text())["model"], "sonnet")
        self.assertIn("team_applied", (r / ".uat" / "audit.jsonl").read_text())


class Dash(unittest.TestCase):
    def setUp(self):
        self.r = root()
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), dashboard.make_handler(self.r, 0))
        self.port = self.srv.server_address[1]
        self.srv.RequestHandlerClass = dashboard.make_handler(self.r, self.port)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.addCleanup(self.srv.shutdown)

    def post(self, body, token=None, origin=True, ctype="application/json", xr="uat-dashboard"):
        h = {"Content-Type": ctype, "X-UAT-Token": dashboard.TOKEN if token is None else token}
        if origin:
            h["Origin"] = f"http://127.0.0.1:{self.port}"
        if xr:
            h["X-Requested-With"] = xr
        q = urllib.request.Request(f"http://127.0.0.1:{self.port}/api/team/propose", json.dumps(body).encode(), h, method="POST")
        try:
            return urllib.request.urlopen(q).status
        except urllib.error.HTTPError as e:
            return e.code

    def test_proposal_is_saved_but_changes_no_config(self):
        self.assertEqual(self.post(GOOD), 200)
        self.assertTrue(team.path(self.r, True).exists())
        self.assertIsNone(team.current(self.r))
        self.assertFalse((self.r / ".uat" / "uat.json").exists())
        self.assertIn("team_proposed", (self.r / ".uat" / "audit.jsonl").read_text())

    def test_every_guard_blocks(self):
        self.assertEqual(self.post(GOOD, token="nope"), 401)
        self.assertEqual(self.post(GOOD, token=""), 401)
        self.assertEqual(self.post(GOOD, origin=False), 403)
        self.assertEqual(self.post(GOOD, ctype="text/plain"), 415)
        self.assertEqual(self.post(GOOD, xr=""), 415)
        bad = json.loads(json.dumps(GOOD)); bad["workers"][0]["model"] = "unlisted-model"
        self.assertEqual(self.post(bad), 400)
        self.assertFalse(team.path(self.r, True).exists())

    def test_wrong_host_and_other_paths(self):
        q = urllib.request.Request(f"http://127.0.0.1:{self.port}/api/team/propose", b"{}", {"Host": "evil.example", "Origin": f"http://127.0.0.1:{self.port}"}, method="POST")
        with self.assertRaises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(q)
        self.assertEqual(e.exception.code, 403)
        q = urllib.request.Request(f"http://127.0.0.1:{self.port}/api/fix", b"{}", method="POST")
        with self.assertRaises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(q)
        self.assertEqual(e.exception.code, 404)

    def test_rate_limit(self):
        dashboard.RECENT.clear()
        codes = [self.post(GOOD) for _ in range(12)]
        self.assertEqual(codes.count(429), 2)
        dashboard.RECENT.clear()


if __name__ == "__main__":
    unittest.main()


class Logo(unittest.TestCase):
    def test_logo_and_tab_icon_are_served_and_allowed_by_csp(self):
        r = root()
        srv = ThreadingHTTPServer(("127.0.0.1", 0), dashboard.make_handler(r, 0))
        port = srv.server_address[1]
        srv.RequestHandlerClass = dashboard.make_handler(r, port)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.shutdown)
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/favicon.svg") as f:
            self.assertEqual(f.headers["Content-Type"], "image/svg+xml")
            self.assertIn(b"<svg", f.read())
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/") as f:
            self.assertIn("img-src 'self'", f.headers["Content-Security-Policy"])
            self.assertIn(b'rel="icon"', f.read())


class FixProof(unittest.TestCase):
    def test_replay_of_a_fix_uses_the_trusted_snapshot_not_the_live_file(self):
        import replay
        r = root()
        live = r / ".uat" / "agents" / "A1" / "findings" / "F-1.json"
        live.parent.mkdir(parents=True)
        live.write_text('{"id": "F-1"}')
        t = uatlib.trusted_dir(r, live)
        (t / "files").mkdir(parents=True)
        (t / "finding.json").write_text('{"id": "F-1", "from": "snapshot"}')
        (t / "digest").write_text("abc")
        live.write_text('{"id": "F-1", "from": "edited-after-snapshot"}')
        obj, digest = uatlib.trusted(r, live)
        self.assertEqual((obj["from"], digest), ("snapshot", "abc"))
        # a fix proof with no snapshot cannot pass: it must not fall back to the live file
        import shutil
        shutil.rmtree(t)
        self.assertEqual(replay.replay_one(live, uatlib.config(r), r, None, 1, True, use_trusted=True), "error")


class StaleVerdict(unittest.TestCase):
    def test_verdict_is_ignored_when_the_evidence_changed_or_digest_missing(self):
        c = {"cid": "C-aaaaaaaaaaaa", "digest": "new", "verdict": "needs_verifier"}
        for v in ({"verdict": "reject", "digest": "old"}, {"verdict": "reject"}):
            self.assertEqual(uatlib.effective_verdict(None, c, {"C-aaaaaaaaaaaa": v}), "needs_verifier")
        self.assertEqual(uatlib.effective_verdict(None, c, {"C-aaaaaaaaaaaa": {"verdict": "reject", "digest": "new"}}), "reject")


class VerdictDigest(unittest.TestCase):
    def run_v(self, r, *extra):
        import subprocess
        return subprocess.run([sys.executable, str(Path(uatlib.__file__).parent / "verdict.py"), "C-aaaaaaaaaaaa", "accept", "ok", "--root", str(r), *extra], capture_output=True, text=True)

    def test_verdict_needs_the_digest_the_verifier_reviewed(self):
        r = root()
        (r / ".uat" / "clusters.json").write_text(json.dumps([{"cid": "C-aaaaaaaaaaaa", "digest": "cur"}]))
        self.assertNotEqual(self.run_v(r).returncode, 0)                          # no digest
        bad = self.run_v(r, "--digest", "stale")
        self.assertNotEqual(bad.returncode, 0)
        self.assertIn("evidence changed", bad.stderr)
        self.assertFalse((r / ".uat" / "verdicts.json").exists())
        self.assertEqual(self.run_v(r, "--digest", "cur").returncode, 0)
        self.assertEqual(json.loads((r / ".uat" / "verdicts.json").read_text())["C-aaaaaaaaaaaa"]["digest"], "cur")
