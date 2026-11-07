"""The score ratchet: progress is pushed, a step back is undone, and only a full pass may merge."""
import conftest  # noqa: F401
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

RATCHET = Path(__file__).resolve().parent.parent / "tools" / "ratchet.py"

HELPER = """def good():
    return {good}

def also():
    return {also}
"""
OLD_TEST = """import unittest
class Old(unittest.TestCase):
    def test_old(self):
        self.assertTrue({old})
"""
NEW_TEST = """import sys, unittest
sys.path.insert(0, ".")
import helper
class New(unittest.TestCase):
    def test_one(self):
        self.assertTrue(helper.good())
    def test_two(self):
        self.assertTrue(helper.also())
"""


class Repo:
    def __init__(self, root):
        self.root = Path(root)
        self.remote = self.root / "remote.git"
        self.work = self.root / "work"
        self.git_at(self.root, "init", "-q", "--bare", "-b", "main", str(self.remote))
        self.git_at(self.root, "init", "-q", "-b", "main", str(self.work))
        self.git("config", "user.email", "t@example.com")
        self.git("config", "user.name", "T")
        self.git("remote", "add", "origin", str(self.remote))
        (self.work / "tests").mkdir()
        self.write("tests/test_old.py", OLD_TEST.format(old="True"))
        self.write("helper.py", HELPER.format(good="False", also="False"))
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "start")
        self.git("push", "-q", "origin", "main")
        self.git("checkout", "-q", "-b", "feature")

    def git_at(self, cwd, *args):
        return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()

    def git(self, *args):
        return self.git_at(self.work, *args)

    def write(self, name, text):
        (self.work / name).write_text(text, encoding="utf-8")

    def ratchet(self, *args):
        p = subprocess.run([sys.executable, str(RATCHET), *args], cwd=self.work, capture_output=True, text=True,
                           env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
        out = p.stdout.strip().splitlines()
        return p.returncode, json.loads(out[-1]) if out else {}, p.stderr

    def start(self):
        self.write("tests/test_feature_x.py", NEW_TEST)
        return self.ratchet("start", "--feature", "x", "--tests", "test_feature_x.py", "-m", "Add tests")

    def pushed(self):
        return self.git("ls-remote", "origin", "refs/heads/feature").split()[0]


class Ratchet(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.r = Repo(self.tmp.name)

    def test_start_records_a_failing_baseline_and_pushes_the_tests(self):
        code, out, _ = self.r.start()
        self.assertEqual(code, 0)
        self.assertEqual(out["decision"], "baseline")
        self.assertEqual(out["score"], 30.0)          # 0 of 2 new, all old
        self.assertEqual(self.r.pushed(), self.r.git("rev-parse", "HEAD"))

    def test_start_refuses_tests_that_already_pass(self):
        self.r.write("helper.py", HELPER.format(good="True", also="True"))
        code, out, _ = self.r.start()
        self.assertNotEqual(code, 0)
        self.assertIn("already pass", out["error"])

    def test_better_score_is_committed_and_pushed(self):
        self.r.start()
        self.r.write("helper.py", HELPER.format(good="True", also="False"))
        code, out, _ = self.r.ratchet("step", "--feature", "x", "-m", "First half")
        self.assertEqual((code, out["decision"], out["score"]), (0, "improved", 65.0))
        self.assertEqual(self.r.pushed(), self.r.git("rev-parse", "HEAD"))

    def test_same_score_stays_local(self):
        self.r.start()
        pushed = self.r.pushed()
        self.r.write("notes.txt", "x")
        code, out, _ = self.r.ratchet("step", "--feature", "x", "-m", "Tidy")
        self.assertEqual((code, out["decision"]), (0, "held"))
        self.assertEqual(self.r.pushed(), pushed)
        self.assertNotEqual(self.r.git("rev-parse", "HEAD"), pushed)

    def test_worse_score_is_undone_and_kept_as_a_patch(self):
        self.r.start()
        self.r.write("helper.py", HELPER.format(good="True", also="False"))
        self.r.ratchet("step", "--feature", "x", "-m", "First half")
        best = self.r.git("rev-parse", "HEAD")
        self.r.write("helper.py", HELPER.format(good="False", also="False"))
        code, out, _ = self.r.ratchet("step", "--feature", "x", "-m", "Break it")
        self.assertEqual((code, out["decision"]), (3, "reverted"))
        self.assertEqual(self.r.git("rev-parse", "HEAD"), best)
        self.assertEqual(self.r.pushed(), best)
        self.assertIn("good", Path(out["patch"]).read_text(encoding="utf-8"))

    def test_broken_old_tests_cost_points_too(self):
        self.r.start()
        self.r.write("helper.py", HELPER.format(good="True", also="False"))
        self.r.ratchet("step", "--feature", "x", "-m", "First half")
        self.r.write("tests/test_old.py", OLD_TEST.format(old="False"))
        code, out, _ = self.r.ratchet("step", "--feature", "x", "-m", "Regress")
        self.assertEqual(out["decision"], "reverted")
        self.assertEqual(code, 3)

    def test_removing_feature_tests_cannot_raise_the_score(self):
        self.r.start()
        self.r.write("helper.py", HELPER.format(good="True", also="False"))
        text = NEW_TEST.replace("    def test_two(self):\n        self.assertTrue(helper.also())\n", "")
        self.r.write("tests/test_feature_x.py", text)
        code, out, _ = self.r.ratchet("step", "--feature", "x", "-m", "Drop a test")
        self.assertEqual((code, out["decision"]), (3, "reverted"))
        self.assertIn("fewer feature tests", out["reason"])

    def test_more_tests_are_allowed_to_lower_the_ratio(self):
        self.r.start()
        self.r.write("helper.py", HELPER.format(good="True", also="True"))
        self.r.ratchet("step", "--feature", "x", "-m", "All pass")
        self.r.write("tests/test_feature_x.py", NEW_TEST + "    def test_three(self):\n        self.assertTrue(False)\n")
        code, out, _ = self.r.ratchet("step", "--feature", "x", "-m", "Harder test")
        self.assertEqual((code, out["decision"]), (0, "raised_bar"))
        self.assertEqual(self.r.pushed(), self.r.git("rev-parse", "HEAD"))

    def test_step_needs_a_start(self):
        code, out, _ = self.r.ratchet("step", "--feature", "x", "-m", "Early")
        self.assertNotEqual(code, 0)
        self.assertIn("start", out["error"])

    def test_refuses_to_run_on_main(self):
        self.r.git("checkout", "-q", "main")
        code, out, _ = self.r.start()
        self.assertNotEqual(code, 0)
        self.assertIn("main", out["error"])

    def test_gate_passes_only_when_everything_passes(self):
        code, out, _ = self.r.ratchet("gate")
        self.assertEqual((code, out["passed"]), (0, True))
        self.r.write("tests/test_bad.py", OLD_TEST.format(old="False"))
        code, out, _ = self.r.ratchet("gate")
        self.assertEqual((code, out["passed"]), (3, False))
        self.assertTrue(any("test_bad" in n or "Old" in n for n in out["failed"]))

    def test_gate_fails_on_a_module_that_cannot_be_imported(self):
        self.r.write("tests/test_bad.py", "import no_such_module_here\n")
        code, out, _ = self.r.ratchet("gate")
        self.assertEqual((code, out["passed"]), (3, False))

    def test_a_test_with_many_failing_subtests_counts_once(self):
        self.r.write("tests/test_sub.py", """import unittest
class Sub(unittest.TestCase):
    def test_many(self):
        for i in range(5):
            with self.subTest(i=i):
                self.assertTrue(False)
""")
        code, out, _ = self.r.ratchet("start", "--feature", "s", "--tests", "test_sub.py", "-m", "Tests")
        self.assertEqual(code, 0)
        self.assertEqual((out["feature"], out["score"]), ("0/1", 30.0))

    def test_status_reports_the_history(self):
        self.r.start()
        code, out, _ = self.r.ratchet("status", "--feature", "x")
        self.assertEqual(code, 0)
        self.assertEqual(out["best_score"], 30.0)
        self.assertEqual(out["history"][0]["decision"], "baseline")


if __name__ == "__main__":
    unittest.main()
