import os, subprocess, sys, tempfile, unittest
from pathlib import Path

TUSK = Path(__file__).resolve().parent.parent / "bin" / "tusk"


def tusk(home, *a):
    return subprocess.run([sys.executable, str(TUSK), *a], capture_output=True, text=True, env={**os.environ, "HOME": home})


class Cli(unittest.TestCase):
    def test_install_links_skills_is_repeatable_and_uninstall_removes_only_ours(self):
        with tempfile.TemporaryDirectory() as h:
            self.assertEqual(tusk(h, "install").returncode, 0)
            sk = Path(h) / ".claude" / "skills"
            self.assertTrue((sk / "uat-full").is_symlink())
            self.assertEqual((Path(h) / ".tusk").resolve(), TUSK.parent.parent)
            self.assertTrue((sk / "uat-full" / "SKILL.md").exists())
            self.assertEqual(tusk(h, "install").returncode, 0)                     # idempotent
            (sk / "mine").mkdir()                                                   # someone else's skill
            (sk / "uat").unlink()
            (sk / "uat").mkdir()                                                    # a real folder is never overwritten
            out = tusk(h, "install")
            self.assertIn("SKIPPED", out.stdout)
            self.assertTrue((sk / "uat").is_dir() and not (sk / "uat").is_symlink())
            tusk(h, "uninstall")
            self.assertFalse((sk / "uat-full").exists())
            self.assertTrue((sk / "mine").exists() and (sk / "uat").exists())

    def test_doctor_lists_requirements(self):
        with tempfile.TemporaryDirectory() as h:
            r = tusk(h, "doctor")
            self.assertIn("bwrap", r.stdout)
            self.assertIn(r.returncode, (0, 1))

    def test_refuses_to_link_into_npx_cache(self):
        import importlib.machinery, importlib.util
        l = importlib.machinery.SourceFileLoader("tusk_cli", str(TUSK))
        m = importlib.util.module_from_spec(importlib.util.spec_from_loader("tusk_cli", l))
        l.exec_module(m)
        m.ROOT = Path("/home/x/.npm/_npx/abc/node_modules/@dexterhere/tusk")
        with self.assertRaises(SystemExit) as e:
            m.install()
        self.assertIn("temporary cache", str(e.exception))


if __name__ == "__main__":
    unittest.main()
