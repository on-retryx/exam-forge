"""Requirements, estimates, the budget solver and per-job choices."""
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
BIN = ROOT / "skills" / "exam-forge" / "bin"
FORGE = BIN / "forge.py"
sys.path.insert(0, str(BIN))
import forge  # noqa: E402

TEAM = {"editor": "sonnet", "screener": "haiku", "reviewer": "opus", "chief_editor": ""}
HIGH = {r: "high" for r in forge.ROLES}


class Base(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        self.home = Path(self.td.name)
        patcher = mock.patch.dict(os.environ, {"HOME": str(self.home)})
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop("EXAM_FORGE_SETTINGS", None)
        forge.S = self.s = forge.load_settings()
        self.tokens = {r: forge.role_tokens(r, 30, 40, self.s["chief_sample_rate"]) for r in forge.ROLES}

    def cli(self, *args, path=None):
        env = {**os.environ, "HOME": str(self.home)}
        if path is not None:
            env["PATH"] = path
        return subprocess.run([sys.executable, FORGE, *map(str, args)], env=env, capture_output=True, text=True)

    def set_access(self, opus):
        forge.config_dir().mkdir(parents=True, exist_ok=True)
        forge.models_path().write_text(json.dumps({"opus": opus, "t": forge.now_iso()}))


class Estimate(Base):
    def test_matches_the_job_it_was_measured_on(self):
        usd = forge.estimate(self.tokens, TEAM, HIGH, self.s["pricing"])["usd"]
        self.assertLess(abs(usd - 5.04) / 5.04, 0.1)

    def test_off_roles_cost_nothing(self):
        e = forge.estimate(self.tokens, TEAM, HIGH, self.s["pricing"])
        self.assertEqual(e["by_role"]["chief_editor"], {"tokens": 0, "usd": 0.0})

    def test_effort_changes_the_price(self):
        low = forge.estimate(self.tokens, TEAM, {r: "low" for r in forge.ROLES}, self.s["pricing"])["usd"]
        top = forge.estimate(self.tokens, TEAM, {r: "max" for r in forge.ROLES}, self.s["pricing"])["usd"]
        self.assertLess(low, top)


class Budget(Base):
    def solve(self, amount, unit="usd", opus=True):
        return forge.solve_budget(self.tokens, unit, amount, self.s["pricing"], opus)

    def spent(self, r, key="usd"):
        return forge.estimate(self.tokens, r["team"], r["effort"], self.s["pricing"])[key]

    def test_stays_under_the_target(self):
        for amount in (3, 5, 8, 12):
            r = self.solve(amount)
            self.assertTrue(r["reachable"])
            self.assertLessEqual(self.spent(r), amount + 0.01)

    def test_more_money_never_means_a_weaker_team(self):
        scores = [forge.quality(*(lambda r: (r["team"], r["effort"]))(self.solve(a))) for a in (3, 5, 8, 12)]
        self.assertEqual(scores, sorted(scores))

    def test_reviewer_is_never_below_sonnet(self):
        for amount in (0.1, 3, 20):
            r = self.solve(amount)
            self.assertIn(r["team"]["reviewer"], ("sonnet", "opus"))
            self.assertIn(r["team"]["editor"], ("sonnet", "opus"))

    def test_impossible_target_gives_the_cheapest_team_and_says_so(self):
        r = self.solve(0.1)
        self.assertFalse(r["reachable"])
        self.assertAlmostEqual(self.spent(r), r["minimum"], delta=0.01)

    def test_without_opus_access_no_role_gets_opus(self):
        self.assertNotIn("opus", self.solve(50, opus=False)["team"].values())

    def test_token_target(self):
        r = self.solve(3_000_000, unit="tokens")
        self.assertLessEqual(self.spent(r, "tokens"), 3_000_000)


class Defaults(Base):
    def test_chief_editor_starts_on_opus_with_access(self):
        self.set_access(True)
        self.assertEqual(forge.default_team(self.s)["team"]["chief_editor"], "opus")

    def test_everyone_starts_on_sonnet_without_access(self):
        for opus in (False, None):
            self.set_access(opus)
            team = forge.default_team(self.s)["team"]
            self.assertEqual(team["chief_editor"], "sonnet")
            self.assertNotIn("opus", team.values())

    def test_unknown_access_is_sonnet(self):
        self.assertEqual(forge.default_team(self.s)["team"]["chief_editor"], "sonnet")


class Probe(Base):
    def fake_claude(self, body):
        folder = self.home / "bin"
        folder.mkdir(exist_ok=True)
        p = folder / "claude"
        p.write_text("#!/bin/sh\n" + body)
        p.chmod(p.stat().st_mode | stat.S_IEXEC)
        return f"{folder}{os.pathsep}/usr/bin:/bin"

    def probe(self, body):
        return json.loads(self.cli("models", "--probe", path=self.fake_claude(body)).stdout)["opus"]

    def test_answer_means_access(self):
        self.assertIs(self.probe("""echo '{"is_error": false, "result": "ok"}'"""), True)

    def test_model_refusal_means_no_access(self):
        self.assertIs(self.probe("""echo '{"is_error": true, "result": "Model opus is not available on your plan"}'; exit 1"""), False)

    def test_other_failure_is_unknown(self):
        self.assertIsNone(self.probe("echo 'network down' >&2; exit 1"))

    def test_known_answer_is_not_asked_again(self):
        self.set_access(True)
        with mock.patch.object(forge.subprocess, "Popen") as popen:
            forge.probe_in_background()
        popen.assert_not_called()

    def test_first_run_asks(self):
        with mock.patch.object(forge.subprocess, "Popen") as popen:
            forge.probe_in_background()
        popen.assert_called_once()

    def test_no_cli_is_unknown(self):
        self.assertIsNone(json.loads(self.cli("models", "--probe", path="/nonexistent").stdout)["opus"])


class Doctor(Base):
    def test_lists_every_requirement(self):
        ids = [i["id"] for i in json.loads(self.cli("doctor", "--json").stdout)["items"]]
        self.assertEqual(ids, [r["id"] for r in forge.REQUIREMENTS])

    def test_refuses_anything_outside_the_table(self):
        with self.assertRaises(forge.SettingsError):
            forge.install_requirement("rm -rf /")

    def test_python_cannot_be_installed_from_python(self):
        with self.assertRaises(forge.SettingsError):
            forge.install_requirement("python")

    def test_failed_step_reports_it(self):
        with mock.patch.dict(forge.REQUIREMENTS[1], {"install": lambda: [[sys.executable, "-c", "raise SystemExit('boom')"]]}):
            with self.assertRaisesRegex(forge.SettingsError, "boom"):
                forge.install_requirement("pdf_tool")


class Window(Base):
    def test_opens_an_app_window_with_its_own_profile(self):
        with mock.patch.object(forge, "find_browser", return_value="/x/brave"), \
                mock.patch.object(forge.subprocess, "Popen") as popen, mock.patch.object(forge.webbrowser, "open") as tab:
            forge.open_window("http://127.0.0.1:1/?tab=job")
        args = popen.call_args[0][0]
        self.assertIn("--app=http://127.0.0.1:1/?tab=job", args)
        self.assertIn(f"--user-data-dir={forge.config_dir() / 'profile'}", args)
        tab.assert_not_called()

    def test_a_mac_app_goes_through_open_so_it_comes_to_the_front(self):
        browser = "/Applications/Chrome.app/Contents/MacOS/Chrome"
        with mock.patch.object(forge, "find_browser", return_value=browser), mock.patch.object(forge.sys, "platform", "darwin"), \
                mock.patch.object(forge.subprocess, "Popen") as popen:
            forge.open_window("http://x")
        self.assertEqual(popen.call_args[0][0][:5], ["open", "-n", "-a", "/Applications/Chrome.app", "--args"])

    def test_falls_back_to_a_tab(self):
        with mock.patch.object(forge, "find_browser", return_value=None), mock.patch.object(forge.webbrowser, "open") as tab:
            forge.open_window("http://x")
        tab.assert_called_once()

    def test_setting_turns_the_window_off(self):
        forge.S = {**self.s, "studio_window": False}
        with mock.patch.object(forge.webbrowser, "open") as tab, mock.patch.object(forge.subprocess, "Popen") as popen:
            forge.open_window("http://x")
        tab.assert_called_once()
        popen.assert_not_called()

    def test_brave_opens_the_window_but_does_not_print(self):
        brave = str(self.home / "Brave Browser")
        Path(brave).touch()
        with mock.patch.object(forge, "BROWSER_CANDIDATES", [brave]):
            self.assertEqual(forge.find_browser(printing=False), brave)
            self.assertIsNone(forge.find_browser())

    def test_a_named_browser_is_always_used(self):
        brave = str(self.home / "Brave Browser")
        Path(brave).touch()
        os.environ["EXAM_FORGE_BROWSER"] = brave
        self.addCleanup(os.environ.pop, "EXAM_FORGE_BROWSER", None)
        self.assertEqual(forge.find_browser(), brave)


class Job(Base):
    def job(self, **over):
        pdf = self.home / "a.pdf"
        pdf.write_bytes(b"%PDF-1.4\n")
        return {"pdfs": [str(pdf)], "format": "mcq", "mcq_options": 5, "answer_placement": "book_end",
                "team": TEAM, "effort": HIGH, **over}

    def test_keeps_the_choices(self):
        job = forge.validate_job(self.job(budget={"unit": "usd", "amount": 5}, contents=False,
                                          difficulty_mix={"easy": 0.2, "medium": 0.5, "hard": 0.3}), self.s)
        self.assertEqual(job["team"], TEAM)
        self.assertEqual(job["budget"], {"unit": "usd", "amount": 5})
        self.assertFalse(job["contents"])
        self.assertEqual(job["difficulty_mix"]["hard"], 0.3)

    def test_reviewer_cannot_be_off(self):
        with self.assertRaisesRegex(forge.SettingsError, "cannot be off"):
            forge.validate_job(self.job(team={**TEAM, "reviewer": ""}), self.s)

    def test_rejects_bad_values(self):
        for bad in ({"effort": {**HIGH, "editor": "huge"}}, {"team": {**TEAM, "editor": "gpt"}},
                    {"budget": {"unit": "usd", "amount": -1}}, {"budget": {"unit": "eur", "amount": 1}},
                    {"difficulty_mix": {"easy": 0.5, "medium": 0.5, "hard": 0.5}}):
            with self.assertRaises(forge.SettingsError):
                forge.validate_job(self.job(**bad), self.s)

    def test_setting_overrides_the_form_for_contents(self):
        self.assertFalse(forge.validate_job(self.job(contents=True), {**self.s, "book_contents": "never"})["contents"])
        self.assertTrue(forge.validate_job(self.job(contents=False), {**self.s, "book_contents": "always"})["contents"])


class Plan(Base):
    def plan(self, *extra):
        pdf = self.home / "n.pdf"
        pdf.write_bytes(b"%PDF-1.4\n")
        run = self.home / "run"
        r = self.cli("plan", pdf, "--pages", "n.pdf=30", "--run", run, *extra)
        return r, run

    def test_records_the_form_choices(self):
        r, run = self.plan("--mix", "0.2,0.5,0.3", "--team", "editor=opus,screener=,reviewer=sonnet,chief_editor=sonnet",
                           "--effort", "editor=max,screener=low,reviewer=high,chief_editor=low", "--budget", "usd=5")
        self.assertEqual(r.returncode, 0, r.stderr)
        p = json.loads(r.stdout)
        self.assertEqual(p["team"]["editor"], "opus")
        self.assertEqual(p["team"]["screener"], "")
        self.assertEqual(p["effort"]["editor"], "max")
        self.assertEqual(p["budget"], {"unit": "usd", "amount": 5.0})
        self.assertEqual(p["pdfs"][0]["split"]["30"], {"easy": 6, "medium": 15, "hard": 9})

    def test_rejects_a_bad_mix(self):
        self.assertNotEqual(self.plan("--mix", "0.5,0.5,0.5")[0].returncode, 0)

    def test_budget_alarm_fires_once(self):
        _, run = self.plan("--budget", "usd=1")
        cost = {"total": {"usd": 1.3, "input": 0, "output": 0, "cache_read": 0, "cache_write": 0}}
        self.assertTrue(forge.budget_passed(run, cost))
        self.assertFalse(forge.budget_passed(run, cost))

    def test_budget_alarm_waits_for_120_percent(self):
        _, run = self.plan("--budget", "usd=1")
        cost = {"total": {"usd": 1.1, "input": 0, "output": 0, "cache_read": 0, "cache_write": 0}}
        self.assertFalse(forge.budget_passed(run, cost))

    def test_learning_from_a_run_moves_the_estimate_once(self):
        _, run = self.plan()
        tokens = sum(forge.role_tokens("editor", 30, 30, 0.25)) * 2
        cost = {"by_role": {"editor": {"input": tokens, "output": 0}}}
        forge.record_calibration(run, cost)
        first = forge.calibration()["editor"]
        self.assertAlmostEqual(first, 1.333, places=2)
        forge.record_calibration(run, cost)
        self.assertEqual(forge.calibration()["editor"], first)


class Contents(Base):
    BOOK = {"title": "T", "format": "mcq", "language": "en", "date": "2026-11-06", "parts": [
        {"name": "A", "pdf": "a.pdf", "questions": [
            {"id": "q1", "section": "S", "type": "open", "difficulty": "easy", "question": "Q?", "answer": "A",
             "explanation": "E", "source": {"pdf": "a.pdf", "section": "S", "page": 1}, "quote": "q"}]}]}

    def test_page_can_be_left_out(self):
        page, _, _ = forge.build_book_html(self.BOOK, "book_end", False)
        self.assertNotIn('class="contents"', page)
        self.assertIn('class="guide"', page)

    def test_page_is_there_by_default(self):
        page, _, _ = forge.build_book_html(self.BOOK, "book_end")
        self.assertIn('class="contents"', page)
        self.assertEqual(page.count('class="guide"'), 1)


class Render(Base):
    def render(self, *extra):
        """Through the real command line, with a stand-in for the browser."""
        browser = self.home / "browser"
        browser.write_text('#!/bin/sh\nfor a; do case "$a" in --print-to-pdf=*) : > "${a#--print-to-pdf=}";; esac; done\n')
        browser.chmod(browser.stat().st_mode | stat.S_IEXEC)
        book = {**json.loads(json.dumps(Contents.BOOK)), "format": "mixed"}
        book["parts"][0]["questions"][0]["verified"] = True
        path = self.home / "book.json"
        path.write_text(json.dumps(book))
        html = self.home / "book.html"
        os.environ["EXAM_FORGE_BROWSER"] = str(browser)
        self.addCleanup(os.environ.pop, "EXAM_FORGE_BROWSER", None)
        r = self.cli("render", path, self.home / "out.pdf", "--html", html, *extra)
        self.assertEqual(r.returncode, 0, r.stderr)
        return html.read_text(encoding="utf-8")

    def test_contents_flag(self):
        self.assertNotIn('class="contents"', self.render("--no-contents"))

    def test_contents_by_default(self):
        self.assertIn('class="contents"', self.render())


if __name__ == "__main__":
    unittest.main()
