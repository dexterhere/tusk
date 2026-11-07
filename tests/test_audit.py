import json, sys, tempfile, threading, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import os
os.environ.setdefault('TUSK_STATE_DIR', tempfile.mkdtemp(prefix='tusk-state-'))
import tusklib, dashboard


def project(tmp):
    r = Path(tmp)
    (r / ".tusk" / "plan").mkdir(parents=True)
    (r / ".tusk" / "plan" / "plan.json").write_text(json.dumps({"rules": [{"id": "R-1", "area": "a", "statement": "s", "source": "x"}, {"id": "R-2", "area": "a", "statement": "t", "source": "x"}], "charters": []}))
    return r


class AuditChain(unittest.TestCase):
    def test_chain_ok_then_tamper(self):
        with tempfile.TemporaryDirectory() as t:
            r = project(t)
            for i in range(3):
                tusklib.audit(r, "orch", "t", n=i)
            self.assertEqual(tusklib.audit_verify(r)[:2], (True, 3))
            p = r / ".tusk" / "audit.jsonl"
            lines = p.read_text().splitlines()
            p.write_text("\n".join([lines[0], lines[1].replace('"n": 1', '"n": 9').replace('"n":1', '"n":9'), lines[2]]) + "\n")
            ok, _, _, bad = tusklib.audit_verify(r)
            self.assertFalse(ok)
            self.assertEqual(bad, 2)

    def test_deleted_line_detected(self):
        with tempfile.TemporaryDirectory() as t:
            r = project(t)
            for i in range(3):
                tusklib.audit(r, "orch", "t", n=i)
            p = r / ".tusk" / "audit.jsonl"
            l = p.read_text().splitlines()
            p.write_text("\n".join([l[0], l[2]]) + "\n")
            self.assertFalse(tusklib.audit_verify(r)[0])

    def test_truncated_tail_detected(self):
        with tempfile.TemporaryDirectory() as t:
            r = project(t)
            for i in range(3):
                tusklib.audit(r, "orch", "t", n=i)
            p = r / ".tusk" / "audit.jsonl"
            p.write_text("\n".join(p.read_text().splitlines()[:2]) + "\n")   # still a valid chain, but shorter than the anchor
            self.assertFalse(tusklib.audit_verify(r)[0])

    def test_missing_anchor_is_not_ok_and_next_write_adopts_visibly(self):
        with tempfile.TemporaryDirectory() as t:
            r = project(t)
            tusklib.audit(r, "orch", "t")
            tusklib._anchor(r).unlink()
            self.assertEqual(tusklib.audit_verify(r)[3], 0)
            self.assertFalse(tusklib.audit_verify(r)[0])
            tusklib.audit(r, "orch", "t")
            self.assertTrue(tusklib.audit_verify(r)[0])
            self.assertIn("audit_anchor_adopted", [x["type"] for x in tusklib.audit_read(r)])

    def test_anchor_not_advanced_over_tampered_log(self):
        with tempfile.TemporaryDirectory() as t:
            r = project(t)
            for i in range(3):
                tusklib.audit(r, "orch", "t", n=i)
            p = r / ".tusk" / "audit.jsonl"
            p.write_text("\n".join(p.read_text().splitlines()[:2]) + "\n")
            tusklib.audit(r, "orch", "t", n=9)          # attacker-triggered write must not launder the truncation
            self.assertFalse(tusklib.audit_verify(r)[0])

    def test_deleted_log_detected_and_not_laundered(self):
        with tempfile.TemporaryDirectory() as t:
            r = project(t)
            tusklib.audit(r, "orch", "t")
            (r / ".tusk" / "audit.jsonl").unlink()
            self.assertFalse(tusklib.audit_verify(r)[0])
            tusklib.audit(r, "orch", "t")               # fresh log must not reset the anchor
            self.assertFalse(tusklib.audit_verify(r)[0])

    def test_malformed_anchor_fails(self):
        with tempfile.TemporaryDirectory() as t:
            r = project(t)
            tusklib.audit(r, "orch", "t")
            tusklib._anchor(r).write_text("garbage")
            self.assertFalse(tusklib.audit_verify(r)[0])

    def test_concurrent_writers_keep_chain(self):
        with tempfile.TemporaryDirectory() as t:
            r = project(t)
            ts = [threading.Thread(target=lambda k=k: [tusklib.audit(r, f"w{k}", "t", i=i) for i in range(10)]) for k in range(4)]
            [x.start() for x in ts]
            [x.join() for x in ts]
            self.assertEqual(tusklib.audit_verify(r)[:2], (True, 40))


class Views(unittest.TestCase):
    def test_coverage_untested_and_passed(self):
        with tempfile.TemporaryDirectory() as t:
            r = project(t)
            tusklib.emit(r, "A1", "check", rule="R-1", result="pass")
            st = {x["id"]: x["status"] for x in tusklib.coverage(r)}
            self.assertEqual(st, {"R-1": "passed", "R-2": "untested"})

    def test_dashboard_snapshot_and_audit_view(self):
        with tempfile.TemporaryDirectory() as t:
            r = project(t)
            tusklib.audit(r, "orch", "verdict", note="<script>x</script>")
            s = dashboard.snapshot(r)
            self.assertTrue(s["audit"]["ok"])
            self.assertEqual(s["summary"]["tests"]["rules"], 2)
            v = dashboard.audit_view(r, "verdict")
            self.assertEqual(len(v), 1)
            self.assertEqual(len(dashboard.audit_view(r, "nomatch")), 0)

    def test_cluster_detail_rejects_bad_id(self):
        with tempfile.TemporaryDirectory() as t:
            r = project(t)
            self.assertIsNone(dashboard.cluster_detail(r, "../../etc/passwd"))


if __name__ == "__main__":
    unittest.main()
