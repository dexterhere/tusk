import json, os, sys, tempfile, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
os.environ.setdefault("TUSK_STATE_DIR", tempfile.mkdtemp(prefix="tusk-state-"))
import dashboard, harness, plain, tusklib


class Harness(unittest.TestCase):
    cfg = {"sandbox": {"agent_home": {}}, "harness": {
        "mycli": {"cmd": ["mycli", "-m", "{model}", "-p", "{prompt}"]},
        "viastdin": {"cmd": ["x", "--model", "{model}"], "prompt": "stdin"},
        "viafile": {"cmd": ["x", "{prompt_file}"], "prompt": "file", "home_files": [".x/auth.json"]}}}

    def test_arg_stdin_and_file_prompt_modes(self):
        with tempfile.TemporaryDirectory() as t:
            argv, stdin = harness.command(self.cfg, "mycli", "m1", t, "do it", False)
            self.assertEqual((argv, stdin), (["mycli", "-m", "m1", "-p", "do it"], None))
            argv, stdin = harness.command(self.cfg, "viastdin", "m1", t, "do it", False)
            self.assertEqual((argv, stdin), (["x", "--model", "m1"], "do it"))
            argv, _ = harness.command(self.cfg, "viafile", "m1", t, "do it", False)
            self.assertEqual(Path(argv[1]).read_text(), "do it")
            self.assertEqual(harness.home_files(self.cfg, "viafile"), [".x/auth.json"])

    def test_unknown_harness_names_the_known_ones(self):
        with self.assertRaises(SystemExit) as e:
            harness.spec(self.cfg, "nope")
        self.assertIn("claude", str(e.exception))

    def test_codex_uses_confined_variant_and_prompt_stays_one_arg(self):
        argv, _ = harness.command({"sandbox": {"agent_home": {}}}, "codex", "m", "/a", "two words; rm -rf /", True)
        self.assertIn("danger-full-access", argv)
        self.assertEqual(argv[-1], "two words; rm -rf /")


class Plain(unittest.TestCase):
    def test_plain_sentences_exist_for_every_replay_status_and_failure(self):
        for st in ("confirmed", "flaky", "not_reproducible", "error", "tamper", "none"):
            self.assertTrue(plain.replay(st)["plain"])
        self.assertIn("quota", plain.agent("failed", 4)["plain"])
        self.assertIn("not logged in", plain.agent("failed", 1, tail="Error: Authentication required")["plain"])
        self.assertIn("not installed", plain.agent("failed", 127)["plain"])
        self.assertIn("sandbox", plain.agent("failed", 1, tail="bwrap: no permissions")["plain"])


class Snapshot(unittest.TestCase):
    def test_findings_show_agent_and_model_and_verifier_decides(self):
        with tempfile.TemporaryDirectory() as t:
            r = Path(t)
            u = r / ".tusk"
            (u / "meta").mkdir(parents=True)
            (u / "plan").mkdir()
            (u / "plan/plan.json").write_text(json.dumps({"rules": [], "charters": [{"id": "CH-1", "area": "fit", "lens": "journey", "rule_ids": []}]}))
            (u / "meta/A4.json").write_text(json.dumps({"id": "A4", "family": "codex", "model": "plan-m", "lens": "journey", "lane": "L1", "charter": "CH-1"}))
            (u / "ledger.jsonl").write_text(json.dumps({"id": "A4", "model": "used-m", "ping": False}) + "\n")
            (u / "clusters.json").write_text(json.dumps([{"cid": "C-aaaaaaaaaaaa", "title": "t", "severity": "major", "area": "fit", "rule_id": "R", "replay": "confirmed", "verdict": "needs_verifier", "category": "x", "kind": "defect",
                                                          "agents": ["A4"], "files": [], "digest": "d", "merge_reasons": [], "fail_rate": 1}]))
            tusklib.emit(r, "orchestrator", "assign", to="A4", kind="charter", ref="CH-1", family="codex", model="plan-m", lane="L1", note="n")
            s = dashboard.snapshot(r)
            c = s["clusters"][0]
            self.assertEqual((c["found_by"][0]["id"], c["found_by"][0]["model"], c["found_by"][0]["fallback"]), ("A4", "used-m", True))
            self.assertEqual(c["verdict"], "needs_verifier")
            self.assertEqual(s["assignments"][0]["to"], "A4")
            self.assertEqual([x["state"] for x in s["board"] if x["kind"] == "test"], ["done"] if s["agents"] and s["agents"][0]["status"] == "done" else ["planned"])
            dg = json.loads((u / "clusters.json").read_text())[0].get("digest", "")
            (u / "verdicts.json").write_text(json.dumps({"C-aaaaaaaaaaaa": {"digest": dg, "verdict": "reject", "by": "Elephant", "family": "claude", "model": "sonnet", "note": "by design"}}))
            c = dashboard.snapshot(r)["clusters"][0]
            self.assertEqual(c["verdict"], "reject")                         # the verifier's decision replaces needs_verifier
            self.assertEqual(c["decided_by"]["model"], "sonnet")
            self.assertEqual(len(s["journey"]), 8)

    def test_ping_directory_is_not_an_agent(self):
        with tempfile.TemporaryDirectory() as t:
            (Path(t) / ".tusk/agents/ping/findings").mkdir(parents=True)
            self.assertEqual(dashboard.snapshot(Path(t))["agents"], [])

    def test_change_signature_moves_when_state_changes(self):
        with tempfile.TemporaryDirectory() as t:
            (Path(t) / ".tusk").mkdir()
            a = dashboard.change_signature(Path(t))
            tusklib.emit(Path(t), "orchestrator", "phase", text="x")
            self.assertNotEqual(a, dashboard.change_signature(Path(t)))


if __name__ == "__main__":
    unittest.main()
