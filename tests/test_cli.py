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
            self.assertTrue((sk / "tusk-full").is_symlink())
            self.assertEqual((Path(h) / ".tusk").resolve(), TUSK.parent.parent)
            self.assertTrue((sk / "tusk-full" / "SKILL.md").exists())
            self.assertEqual(tusk(h, "install").returncode, 0)                     # idempotent
            (sk / "mine").mkdir()                                                   # someone else's skill
            (sk / "tusk").unlink()
            (sk / "tusk").mkdir()                                                    # a real folder is never overwritten
            out = tusk(h, "install")
            self.assertIn("SKIPPED", out.stdout)
            self.assertTrue((sk / "tusk").is_dir() and not (sk / "tusk").is_symlink())
            tusk(h, "uninstall")
            self.assertFalse((sk / "tusk-full").exists())
            self.assertTrue((sk / "mine").exists() and (sk / "tusk").exists())

    def test_doctor_lists_requirements(self):
        with tempfile.TemporaryDirectory() as h:
            r = tusk(h, "doctor")
            self.assertIn("bwrap", r.stdout)
            self.assertIn(r.returncode, (0, 1))


if __name__ == "__main__":
    unittest.main()
