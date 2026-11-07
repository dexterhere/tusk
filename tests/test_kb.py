import json, os, sys, tempfile, time, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
os.environ.setdefault("UAT_STATE_DIR", tempfile.mkdtemp(prefix="uat-state-"))
import kb, uatlib


def cluster(r, cid, title, file, rule="R-1", area="cart", sev="major"):
    d = r / ".uat" / "trusted" / "A1" / cid
    d.mkdir(parents=True, exist_ok=True)
    (d / "finding.json").write_text(json.dumps({"expected": f"{title} works", "suspect": {"file": file, "line": 3}}))
    return {"cid": cid, "title": title, "severity": sev, "area": area, "rule_id": rule, "category": "functional", "verdict": "accept", "replay": "confirmed",
            "files": [f".uat/trusted/A1/{cid}/finding.json"], "digest": cid}


def project(t, clusters, fq=()):
    r = Path(t)
    u = r / ".uat"
    (u / "plan").mkdir(parents=True, exist_ok=True)
    (u / "plan" / "plan.json").write_text(json.dumps({"rules": [{"id": "R-1", "statement": "s"}], "charters": []}))
    (u / "clusters.json").write_text(json.dumps(clusters))
    (u / "fixqueue.json").write_text(json.dumps(list(fq)))
    return r


def fqitem(cid, state, sev="major"):
    return {"cid": cid, "state": state, "history": [], "severity": sev, "title": cid, "area": "cart", "kind": "defect"}


class KB(unittest.TestCase):
    def test_ingest_links_issue_file_rule_and_is_small(self):
        with tempfile.TemporaryDirectory() as t:
            r = project(t, [cluster(Path(t), "C-aaaaaaaaaaaa", "Zero qty", "app/cart.py")])
            g = kb.ingest(r)
            self.assertEqual(g["nodes"]["i:C-aaaaaaaaaaaa"]["status"], "open")
            self.assertIn(["i:C-aaaaaaaaaaaa", "in", "f:app/cart.py"], g["edges"])
            self.assertIn(["i:C-aaaaaaaaaaaa", "violates", "r:R-1"], g["edges"])
            self.assertLess((r / ".uat/kb/graph.json").stat().st_size, 5000)
            self.assertLess(len((r / ".uat/kb/DIGEST.md").read_text().splitlines()), 61)

    def test_impact_lists_related_issues_tests_and_risk(self):
        with tempfile.TemporaryDirectory() as t:
            r = Path(t)
            cl = [cluster(r, "C-aaaaaaaaaaaa", "Zero qty", "app/cart.py"), cluster(r, "C-bbbbbbbbbbbb", "Negative qty", "app/cart.py"), cluster(r, "C-cccccccccccc", "Other", "app/pay.py", rule="R-9", area="pay")]
            project(t, cl)
            (r / ".uat/regression/C-bbbbbbbbbbbb").mkdir(parents=True)
            kb.ingest(r)
            im = kb.impact(r, "C-aaaaaaaaaaaa")
            self.assertEqual([x["cid"] for x in im["related"]], ["C-bbbbbbbbbbbb"])
            self.assertEqual(im["rerun"], ["C-bbbbbbbbbbbb"])
            self.assertEqual(im["risk"], "medium")
            self.assertEqual(kb.impact(r, "C-cccccccccccc")["related"], [])

    def test_fixed_then_new_issue_in_same_file_is_flagged_and_risk_high(self):
        with tempfile.TemporaryDirectory() as t:
            r = Path(t)
            project(t, [cluster(r, "C-aaaaaaaaaaaa", "Zero qty", "app/cart.py")], [fqitem("C-aaaaaaaaaaaa", "approved")])
            kb.ingest(r)
            kb.fixed(r, "C-aaaaaaaaaaaa", "uat-fix/a", ["app/cart.py"])
            g = kb.load(r)
            self.assertEqual(g["nodes"]["i:C-aaaaaaaaaaaa"]["status"], "fixed")
            self.assertEqual(json.loads((r / ".uat/fixqueue.json").read_text())[0]["state"], "proven")
            (r / ".uat/clusters.json").write_text(json.dumps([cluster(r, "C-dddddddddddd", "Rounding broke", "app/cart.py")]))
            kb.ingest(r)
            g = kb.load(r)
            self.assertIn(["x:uat-fix/a", "suspected_cause", "i:C-dddddddddddd"], g["edges"])
            self.assertEqual(kb.impact(r, "C-dddddddddddd")["risk"], "high")      # the related issue is fixed and could regress

    def test_reappearing_issue_after_fix_is_regressed(self):
        with tempfile.TemporaryDirectory() as t:
            r = Path(t)
            project(t, [cluster(r, "C-aaaaaaaaaaaa", "Zero qty", "app/cart.py")], [fqitem("C-aaaaaaaaaaaa", "approved")])
            kb.ingest(r)
            kb.fixed(r, "C-aaaaaaaaaaaa", "uat-fix/a", ["app/cart.py"])
            time.sleep(0.05)
            cluster(r, "C-aaaaaaaaaaaa", "Zero qty", "app/cart.py")                # trusted snapshot rewritten by a later replay
            kb.ingest(r)
            self.assertEqual(kb.load(r)["nodes"]["i:C-aaaaaaaaaaaa"]["status"], "regressed")
            self.assertEqual(kb.retest_ids(r), [])

    def test_reverify_lines_only_for_fixed_in_area(self):
        with tempfile.TemporaryDirectory() as t:
            r = Path(t)
            project(t, [cluster(r, "C-aaaaaaaaaaaa", "Zero qty", "app/cart.py")], [fqitem("C-aaaaaaaaaaaa", "approved")])
            kb.ingest(r)
            self.assertEqual(kb.reverify_lines(r, "cart"), [])
            kb.fixed(r, "C-aaaaaaaaaaaa", "b", ["app/cart.py"])
            self.assertEqual(len(kb.reverify_lines(r, "cart")), 1)
            self.assertEqual(kb.reverify_lines(r, "pay"), [])

    def test_handoff_includes_impact(self):
        with tempfile.TemporaryDirectory() as t:
            r = Path(t)
            cl = [cluster(r, "C-aaaaaaaaaaaa", "Zero qty", "app/cart.py"), cluster(r, "C-bbbbbbbbbbbb", "Negative qty", "app/cart.py")]
            project(t, cl)
            kb.ingest(r)
            (r / ".uat/report").mkdir()
            (r / ".uat/report/findings.json").write_text(json.dumps({"findings": [{"cid": "C-aaaaaaaaaaaa", "title": "Zero qty", "severity": "major", "area": "cart", "rule_id": "R-1", "expected": "e", "actual": "a",
                                                                                   "evidence": "ev", "suspect": {"file": "app/cart.py", "line": 3}, "acceptance": "replay --only C-aaaaaaaaaaaa"}]}))
            text = kb.handoff(r, "C-aaaaaaaaaaaa").read_text()
            self.assertIn("Do not break", text)
            self.assertIn("C-bbbbbbbbbbbb", text)

    def test_link_gives_blind_findings_a_file_and_impact_sees_it(self):
        with tempfile.TemporaryDirectory() as t:
            r = Path(t)
            a, b = cluster(r, "C-aaaaaaaaaaaa", "Zero qty", ""), cluster(r, "C-bbbbbbbbbbbb", "Negative qty", "", rule="R-2", area="x")
            project(t, [a, b])
            kb.ingest(r)
            self.assertEqual(kb.impact(r, "C-aaaaaaaaaaaa")["related"], [])
            kb.link(r, "C-aaaaaaaaaaaa", ["app/cart.py"])
            kb.link(r, "C-bbbbbbbbbbbb", ["app/cart.py"])
            self.assertEqual([x["cid"] for x in kb.impact(r, "C-aaaaaaaaaaaa")["related"]], ["C-bbbbbbbbbbbb"])

    def test_kb_dir_must_stay_inside_project(self):
        with tempfile.TemporaryDirectory() as t:
            r = project(t, [])
            (r / ".uat/uat.json").write_text(json.dumps({"kb": {"dir": "../escape"}}))
            with self.assertRaises(SystemExit):
                kb.kbdir(r)


if __name__ == "__main__":
    unittest.main()
