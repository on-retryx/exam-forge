"""Windows and Linux behaviour. No Windows machine runs these: sys.platform, ctypes and code pages
are simulated, so a pass shows the logic is right, not that it was seen working on Windows."""
import argparse
import io
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path, PurePosixPath, PureWindowsPath
from unittest import mock

import conftest  # noqa: F401
import test_job_choices as jobs

ROOT = jobs.ROOT
BIN = jobs.BIN
FORGE = jobs.FORGE
STUDIO = ROOT / "skills" / "exam-forge" / "studio"
forge = jobs.forge


class Base(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.home = Path(td.name).resolve()
        patcher = mock.patch.dict(os.environ, {"HOME": str(self.home)})
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop("EXAM_FORGE_SETTINGS", None)
        os.environ.pop("EXAM_FORGE_BROWSER", None)
        forge.S = forge.load_settings()

    def cli(self, *args, **env):
        return subprocess.run([sys.executable, FORGE, *map(str, args)], capture_output=True,
                              env={**os.environ, **env})


class Skills(unittest.TestCase):
    def test_no_skill_assumes_python3(self):
        for skill in (ROOT / "skills").glob("*/SKILL.md"):
            self.assertNotIn('python3 "$FORGE"', skill.read_text(encoding="utf-8"), skill)

    def test_config_finds_python_in_the_same_words_as_studio(self):
        read = lambda name: (ROOT / "skills" / name / "SKILL.md").read_text(encoding="utf-8")
        line = re.search(r"`PY` is the first of .*", read("studio")).group(0)
        self.assertIn(line, read("config"))

    def test_winget_never_waits_for_a_prompt(self):
        chrome = next(r for r in forge.REQUIREMENTS if r["id"] == "browser")["install"]["win32"][0]
        python = (ROOT / "skills" / "exam-forge" / "SKILL.md").read_text(encoding="utf-8")
        for flag in ("--accept-source-agreements", "--accept-package-agreements"):
            self.assertIn(flag, chrome)
            self.assertRegex(python, rf"winget install [^`]*{flag}")


class Places(Base):
    def on(self, platform, mask=None):
        windll = mock.Mock()
        windll.kernel32.GetLogicalDrives.return_value = mask
        with mock.patch.object(forge.sys, "platform", platform), mock.patch("ctypes.windll", windll, create=True):
            return forge.places()

    def test_windows_lists_each_drive_from_the_mask(self):
        drives = [p for p in self.on("win32", 0b1101) if p["key"] == "drive"]  # A, C and D
        self.assertEqual([p["path"] for p in drives], ["A:\\", "C:\\", "D:\\"])
        self.assertEqual([p["label"] for p in drives], ["A:", "C:", "D:"])

    def test_a_drive_root_is_a_real_windows_root(self):
        self.assertEqual(PureWindowsPath("D:\\").anchor, "D:\\")
        self.assertEqual(PureWindowsPath("D:\\").parent, PureWindowsPath("D:\\"))

    def test_without_the_windows_api_there_are_no_drives(self):
        with mock.patch.object(forge.sys, "platform", "win32"):
            self.assertEqual(forge.windows_drives(), [])

    def test_only_a_mac_gets_volumes(self):
        volumes = mock.patch.object(forge.Path, "is_dir", return_value=True)
        with volumes:
            self.assertIn("volumes", [p["key"] for p in self.on("darwin")])
            self.assertNotIn("volumes", [p["key"] for p in self.on("linux")])
            self.assertNotIn("volumes", [p["key"] for p in self.on("win32", 0)])

    def test_the_page_can_name_every_place(self):
        i18n = (STUDIO / "i18n.js").read_text(encoding="utf-8")
        for key in ("home", "desktop", "documents", "downloads", "volumes"):
            self.assertEqual(i18n.count(f"place_{key}:"), 2, key)  # English and Turkish
        self.assertIn('pl.label || T("place_" + pl.key)', (STUDIO / "app.js").read_text(encoding="utf-8"))


class Browse(Base):
    def test_a_root_has_no_parent(self):
        self.assertIsNone(forge.list_folder(self.home.anchor)["parent"])

    def test_an_unreadable_place_is_a_message(self):
        error = OSError(21, "The device is not ready")
        with mock.patch.object(forge.Path, "is_file", side_effect=error):
            with self.assertRaisesRegex(forge.SettingsError, "cannot open E:.*not ready"):
                forge.list_folder("E:\\")

    def test_an_unknown_user_is_a_message(self):
        with mock.patch.object(forge.Path, "expanduser", side_effect=RuntimeError("no home for nobody")):
            with self.assertRaisesRegex(forge.SettingsError, "cannot open"):
                forge.list_folder("~nobody")

    def test_a_root_without_pdfs_still_names_itself(self):
        with mock.patch.object(forge, "pdfs_under", return_value=[]):
            with self.assertRaisesRegex(forge.SettingsError, "no PDFs in /"):
                forge.expand_sources([PurePosixPath("/")])


class Python(unittest.TestCase):
    def test_check_uses_the_minimum(self):
        for version, ok in (((3, 8, 20), False), ((3, 9, 0), True), ((3, 14, 0), True)):
            with mock.patch.object(forge.sys, "version_info", version):
                self.assertEqual(forge.check_python()[0], ok, version)

    def test_hint_and_readme_state_the_same_minimum(self):
        wanted = ".".join(map(str, forge.MIN_PYTHON))
        hint = next(r for r in forge.REQUIREMENTS if r["id"] == "python")["hint"]
        self.assertIn(f"Python {wanted} or newer", hint)
        self.assertIn(f"Python {wanted}+", (ROOT / "README.md").read_text(encoding="utf-8"))


@unittest.skipIf(sys.platform == "win32", "uses /bin/sh stand-ins")
class Hook(Base):
    def setUp(self):
        super().setUp()
        self.bin = self.home / "bin"
        self.bin.mkdir()
        self.run_dir = self.home / "Ders Notları”"
        self.run_dir.mkdir()
        (self.run_dir / "plan.json").write_text("{}")
        hooks = json.loads((ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))
        self.command = hooks["hooks"]["PreToolUse"][0]["hooks"][0]["command"]

    def shim(self, name, drop=0):
        p = self.bin / name
        p.write_text(f'#!/bin/sh\nshift {drop}\nexec "{sys.executable}" "$@"\n')
        p.chmod(p.stat().st_mode | stat.S_IEXEC)

    def fire(self, root=ROOT, **env):
        payload = {"tool_input": {"prompt": f"EXAM_FORGE: run={self.run_dir}|pdf=a|chunk=1|round=1"}, "tool_use_id": "t"}
        return subprocess.run(["/bin/sh", "-c", self.command], input=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                              capture_output=True, env={"PATH": str(self.bin), "CLAUDE_PLUGIN_ROOT": str(root), **env})

    def events(self):
        p = self.run_dir / "events.jsonl"
        return p.read_text(encoding="utf-8").splitlines() if p.exists() else []

    def test_only_py_still_records_the_event(self):
        self.shim("py", drop=1)
        self.assertEqual(self.fire().returncode, 0)
        self.assertEqual(len(self.events()), 1)

    def test_a_working_python3_records_it_once(self):
        for name in ("python3", "python"):
            self.shim(name)
        self.shim("py", drop=1)
        self.fire()
        self.assertEqual(len(self.events()), 1)

    def test_no_python_at_all_does_not_fail(self):
        self.assertEqual(self.fire().returncode, 0)

    def test_a_missing_script_never_blocks(self):
        # Python exits 2 on a missing script, and exit 2 from a PreToolUse hook blocks the tool.
        self.shim("python3")
        self.assertEqual(self.fire(root=self.home / "gone").returncode, 0)

    def test_non_ascii_paths_survive_an_ansi_pipe(self):
        self.shim("python3")
        self.assertEqual(self.fire(PYTHONIOENCODING="cp1252").returncode, 0)
        self.assertEqual(len(self.events()), 1)


class Output(Base):
    def test_turkish_output_survives_an_ansi_pipe(self):
        r = self.cli("config", "set", "field", "Tıp ≥ β", PYTHONIOENCODING="cp1252:strict")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Tıp ≥ β", r.stdout.decode("utf-8"))

    def test_errors_survive_an_ansi_pipe(self):
        r = self.cli("config", "get", "şifre", PYTHONIOENCODING="cp1252:strict")
        self.assertEqual(r.returncode, 2)
        self.assertIn("şifre", r.stderr.decode("utf-8"))

    def test_a_stream_without_reconfigure_is_left_alone(self):
        with mock.patch.object(forge.sys, "stdout", object()):
            forge.utf8_output()

    def test_child_output_in_any_encoding_is_not_a_crash(self):
        step = [sys.executable, "-c", "import sys; sys.stderr.buffer.write(b'boom \\xff'); sys.exit(1)"]
        with mock.patch.dict(forge.REQUIREMENTS[1], {"install": lambda: [step]}):
            with self.assertRaisesRegex(forge.SettingsError, "boom"):
                forge.install_requirement("pdf_tool")


class AtomicWrite(Base):
    def test_retries_while_a_file_is_held_open(self):
        real, calls = os.replace, []

        def busy(src, dst):
            calls.append(dst)
            if len(calls) < 3:
                raise PermissionError(13, "in use")
            real(src, dst)

        path = self.home / "a.json"
        with mock.patch.object(forge.os, "replace", busy), mock.patch.object(forge.time, "sleep"):
            forge.write_atomic(path, {"ok": True})
        self.assertEqual(len(calls), 3)
        self.assertEqual(json.loads(path.read_text()), {"ok": True})

    def test_gives_up_in_the_end(self):
        with mock.patch.object(forge.os, "replace", side_effect=PermissionError(13, "in use")) as replace, \
                mock.patch.object(forge.time, "sleep"):
            with self.assertRaises(PermissionError):
                forge.write_atomic(self.home / "a.json", {})
        self.assertEqual(replace.call_count, forge.REPLACE_TRIES)
        self.assertEqual(list(self.home.iterdir()), [])

    def test_parallel_writes_do_not_collide(self):
        errors = []

        def write(n):
            try:
                forge.write_atomic(self.home / "a.json", {"n": n})
            except OSError as e:
                errors.append(e)

        threads = [threading.Thread(target=write, args=(n,)) for n in range(30)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(errors, [])
        self.assertEqual([p.name for p in self.home.iterdir()], ["a.json"])


class Settings(Base):
    def test_parallel_writes_lose_nothing_and_never_show_half_a_file(self):
        keys = [("max_rounds", 3), ("topic_change_round", 2), ("studio_port", 39300)]
        errors, done = [], threading.Event()

        def write(key, value):
            try:
                for _ in range(10):
                    forge.write_user_setting(key, value)
            except (forge.SettingsError, OSError) as e:
                errors.append(e)

        def read():
            while not done.is_set():
                try:
                    forge.load_settings()
                except forge.SettingsError as e:
                    errors.append(e)

        reader = threading.Thread(target=read)
        reader.start()
        threads = [threading.Thread(target=write, args=k) for k in keys for _ in range(4)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        done.set()
        reader.join()
        self.assertEqual(errors, [])
        self.assertEqual(json.loads(forge.user_settings_path().read_text()), dict(keys))

    def test_the_file_stays_readable_and_a_link_stays_a_link(self):
        real = self.home / "dotfiles" / "settings.json"
        real.parent.mkdir()
        real.write_text("{}")
        forge.user_settings_path().parent.mkdir(parents=True)
        forge.user_settings_path().symlink_to(real)
        forge.write_user_setting("max_rounds", 3)
        self.assertTrue(forge.user_settings_path().is_symlink())
        self.assertEqual(real.read_text(), '{\n  "max_rounds": 3\n}\n')


class Sources(Base):
    def folder(self, name="f", good=True):
        d = self.home / name
        d.mkdir()
        (d / "fake.pdf").write_text("no")
        if good:
            (d / "good.pdf").write_bytes(b"%PDF-1.4\n")
        return d

    def locked(self, folder):
        folder.chmod(0)
        self.addCleanup(folder.chmod, 0o755)
        if os.access(folder, os.R_OK):
            self.skipTest("this user can read anywhere")

    def test_a_fake_pdf_is_left_out_and_named(self):
        skipped = []
        found = forge.expand_sources([self.folder()], skipped)
        self.assertEqual([Path(p).name for p in found], ["good.pdf"])
        self.assertEqual(skipped, ["fake.pdf"])

    def test_it_works_without_a_list_to_report_in(self):
        self.assertEqual(len(forge.expand_sources([self.folder()])), 1)

    def test_a_chosen_file_that_is_fake_is_still_an_error(self):
        with self.assertRaisesRegex(forge.SettingsError, "not a PDF"):
            forge.expand_sources([self.folder() / "fake.pdf"], [])

    def test_a_folder_of_fakes_has_no_pdfs(self):
        with self.assertRaisesRegex(forge.SettingsError, "no PDFs in f"):
            forge.expand_sources([self.folder(good=False)], [])

    def test_a_locked_folder_reports_the_real_cause(self):
        d = self.folder()
        self.locked(d)
        with self.assertRaisesRegex(forge.SettingsError, "cannot open .*ermission denied"):
            forge.expand_sources([d], [])

    def test_a_locked_subfolder_does_not_lose_the_rest(self):
        d = self.folder()
        (d / "sub").mkdir()
        self.locked(d / "sub")
        skipped = []
        self.assertEqual(len(forge.expand_sources([d], skipped)), 1)
        self.assertEqual(sorted(skipped), ["fake.pdf", "sub"])


class Waiter(Base):
    def test_a_process_that_ended_is_not_alive(self):
        proc = subprocess.Popen([sys.executable, "-c", "pass"])
        proc.wait()
        self.assertFalse(forge.pid_alive(proc.pid))
        self.assertTrue(forge.pid_alive(os.getpid()))

    def test_session_waiting_needs_a_fresh_file_and_a_live_pid(self):
        waiter = forge.intake_dir() / "waiter.json"
        self.assertFalse(forge.session_waiting())
        proc = subprocess.Popen([sys.executable, "-c", "pass"])
        proc.wait()
        for mark, expected in (({"pid": os.getpid()}, True), ({"pid": proc.pid}, False), ({}, True), ([1], True)):
            forge.write_atomic(waiter, mark)
            self.assertEqual(forge.session_waiting(), expected, mark)
        os.utime(waiter, (0, 0))
        self.assertFalse(forge.session_waiting())

    def test_windows_asks_the_system_and_never_signals(self):
        kernel = mock.Mock()
        kernel.OpenProcess.return_value = 7

        def exit_code(handle, ref):
            ref._obj.value = code
            return 1

        kernel.GetExitCodeProcess.side_effect = exit_code
        ctypes = __import__("ctypes")
        with mock.patch.object(forge.sys, "platform", "win32"), mock.patch.object(ctypes, "windll", mock.Mock(kernel32=kernel), create=True), \
                mock.patch.object(forge.os, "kill") as kill:
            code = 259
            self.assertTrue(forge.pid_alive(42))
            code = 0
            self.assertFalse(forge.pid_alive(42))
            kernel.OpenProcess.return_value = 0
            self.assertFalse(forge.pid_alive(42))
        kill.assert_not_called()
        self.assertEqual(kernel.CloseHandle.call_count, 2)

    def test_a_pid_too_big_for_the_system_is_not_alive(self):
        for pid in (2 ** 31, 2 ** 63, 2 ** 80):
            self.assertFalse(forge.pid_alive(pid))
            forge.write_atomic(forge.intake_dir() / "waiter.json", {"pid": pid})
            self.assertFalse(forge.session_waiting())

    def live_process(self):
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        self.addCleanup(proc.wait)
        self.addCleanup(proc.kill)
        return proc

    def test_a_job_for_a_live_wait_survives_a_second_wait(self):
        proc = self.live_process()
        for name, expected in (("job.json", 0), ("cancel.json", 5)):
            forge.write_atomic(forge.intake_dir() / "waiter.json", {"pid": proc.pid})
            forge.write_atomic(forge.intake_dir() / name, {"pdfs": ["a"]})
            self.assertEqual(self.run_wait(1)[0], expected, name)
            self.assertFalse((forge.intake_dir() / name).exists())

    def test_what_a_dead_wait_left_never_runs_whatever_its_mark_says(self):
        ended = subprocess.Popen([sys.executable, "-c", "pass"])
        ended.wait()
        for mark in ({"pid": ended.pid}, {}, [1], {"pid": "1"}, {"pid": 2 ** 40}, {"pid": os.getpid()}):
            waiter = forge.intake_dir() / "waiter.json"
            forge.write_atomic(waiter, mark)
            for name in ("job.json", "cancel.json"):
                forge.write_atomic(forge.intake_dir() / name, {"stale": True})
            code, out = self.run_wait(0)
            self.assertEqual((code, json.loads(out)), (4, {"timeout": True}), mark)
        proc = self.live_process()  # alive, but its mark is too old to be a wait: the number may have been reused
        forge.write_atomic(waiter, {"pid": proc.pid})
        os.utime(waiter, (0, 0))
        forge.write_atomic(forge.intake_dir() / "job.json", {"stale": True})
        self.assertEqual(self.run_wait(0)[0], 4)

    def test_only_one_of_many_waits_claims_a_job_or_a_cancel(self):
        proc = self.live_process()
        for name, code in (("job.json", 0), ("cancel.json", 5)):
            forge.write_atomic(forge.intake_dir() / "waiter.json", {"pid": proc.pid})
            forge.write_atomic(forge.intake_dir() / name, {"pdfs": ["a"]})
            waits = [subprocess.Popen([sys.executable, FORGE, "intake", "wait", "--timeout", "2"],
                                      stdout=subprocess.PIPE) for _ in range(8)]
            [w.communicate(timeout=15) for w in waits]
            self.assertEqual(sorted(w.returncode for w in waits), sorted([code] + [4] * 7), name)

    def run_wait(self, timeout, during=None):
        out = io.StringIO()
        with mock.patch.object(forge.sys, "stdout", out), mock.patch.object(forge.time, "sleep", during or (lambda s: None)):
            try:
                forge.intake_wait(argparse.Namespace(timeout=timeout))
                code = 0
            except SystemExit as e:
                code = e.code
        return code, out.getvalue()

    def test_a_job_left_behind_is_never_picked_up(self):
        for name in ("job.json", "cancel.json"):
            forge.write_atomic(forge.intake_dir() / name, {"stale": True})
        code, out = self.run_wait(0.05)
        self.assertEqual(code, 4)
        self.assertIn("timeout", out)
        self.assertEqual(sorted(p.name for p in forge.intake_dir().iterdir()), [])

    def test_a_job_that_lands_as_the_time_runs_out_is_taken(self):
        def late(seconds):
            forge.write_atomic(forge.intake_dir() / "job.json", {"pdfs": ["a"]})
            real_sleep(0.1)

        real_sleep = forge.time.sleep
        code, out = self.run_wait(0.05, late)
        self.assertEqual((code, json.loads(out)), (0, {"pdfs": ["a"]}))


class PageCount(Base):
    def test_uses_pymupdf_off_the_mac(self):
        doc = mock.MagicMock(page_count=7)
        doc.__enter__.return_value = doc
        fitz = mock.Mock()
        fitz.open.return_value = doc
        pdf = self.home / "a.pdf"
        pdf.write_bytes(b"%PDF-1.5\n")
        with mock.patch.object(forge.sys, "platform", "win32"), mock.patch.object(forge, "load_fitz", return_value=fitz):
            self.assertEqual(forge.page_count(pdf), 7)

    def test_falls_back_to_the_scan_when_pymupdf_fails(self):
        fitz = mock.Mock()
        fitz.open.side_effect = RuntimeError("broken")
        pdf = self.home / "a.pdf"
        pdf.write_bytes(b"%PDF-1.4\n<</Type /Pages /Count 3>>")
        with mock.patch.object(forge.sys, "platform", "linux"), mock.patch.object(forge, "load_fitz", return_value=fitz):
            self.assertEqual(forge.page_count(pdf), 3)


class Damage(Base):
    @unittest.skipIf(sys.platform == "win32", "no POSIX modes")
    def test_a_private_file_stays_private(self):
        forge.user_settings_path().parent.mkdir(parents=True)
        forge.user_settings_path().write_text("{}")
        forge.user_settings_path().chmod(0o600)
        forge.write_user_setting("mcq_options", 4)
        self.assertEqual(stat.S_IMODE(forge.user_settings_path().stat().st_mode), 0o600)
        self.assertEqual(json.loads(forge.user_settings_path().read_text()), {"mcq_options": 4})

    def test_a_read_only_config_folder_gets_a_clear_message(self):
        config = forge.config_dir()
        config.mkdir(parents=True)
        (config / "settings.json").write_text('{"mcq_options": 5}')
        config.chmod(0o555)
        self.addCleanup(config.chmod, 0o755)
        if os.access(config, os.W_OK):
            self.skipTest("this user can write anywhere")
        for args in (("set", "mcq_options", "4"), ("reset", "mcq_options"), ("reset",)):
            r = self.cli("config", *args)
            self.assertEqual(r.returncode, 2, r.stderr)
            self.assertNotIn(b"Traceback", r.stderr)
            self.assertIn(b"cannot save settings in", r.stderr)

    def test_plan_survives_any_runs_file(self):
        pdf = self.home / "a.pdf"
        pdf.write_bytes(b"%PDF-1.4\n")
        runs = forge.config_dir() / "runs.json"
        runs.parent.mkdir(parents=True)

        def plan():
            r = self.cli("plan", pdf, "--pages", "a.pdf=12", "--run", self.home / "run")
            self.assertEqual(r.returncode, 0, r.stderr)

        for text in ("5", '"x"', '{"a": 1}', "not json", "null"):
            runs.write_text(text)
            plan()
            self.assertEqual(json.loads(runs.read_text()), [str((self.home / "run").resolve())], text)
        runs.write_text("[]")
        runs.chmod(0)
        self.addCleanup(runs.chmod, 0o644)
        plan()
        runs.unlink()
        runs.mkdir()
        plan()

    def test_a_folder_of_only_fake_pdfs_names_them(self):
        folder = self.home / "f"
        folder.mkdir()
        (folder / "a.pdf").write_text("no")
        (folder / "b.pdf").write_text("no")
        with self.assertRaisesRegex(forge.SettingsError, "a.pdf, b.pdf"):
            forge.expand_sources([str(folder)])

    def test_paths_must_be_a_list_of_text(self):
        for paths in ("/tmp", [""], ["  "], [1], None, {"a": 1}):
            with self.assertRaisesRegex(forge.SettingsError, "list"):
                forge.expand_sources(paths)

    def test_parallel_commands_keep_every_setting(self):
        pairs = ["answer_placement=inline", "book_contents=always", "merge_books=always", "screener_enabled=false",
                 "studio_window=false", "studio_job_form=false", "studio_auto_open=false", "section_titles=filename"]
        path = forge.user_settings_path()
        for _ in range(3):
            path.unlink(missing_ok=True)
            procs = [subprocess.Popen([sys.executable, FORGE, "config", "set", *p.split("=")],
                                      stdout=subprocess.DEVNULL, stderr=subprocess.PIPE) for p in pairs]
            errors = [p.communicate(timeout=30)[1] for p in procs]
            self.assertEqual([p.returncode for p in procs], [0] * len(pairs), errors)
            self.assertEqual(sorted(json.loads(path.read_text())), sorted(p.split("=")[0] for p in pairs))
            self.assertFalse((forge.config_dir() / "settings.lock").exists())

    def test_a_lock_left_by_a_dead_command_does_not_block(self):
        lock = forge.config_dir() / "settings.lock"
        lock.parent.mkdir(parents=True)
        lock.touch()
        os.utime(lock, (0, 0))
        forge.write_user_setting("mcq_options", 4)
        self.assertFalse(lock.exists())


@unittest.skipIf(sys.platform == "win32", "uses a /bin/sh stand-in for the browser")
class Render(Base):
    def setUp(self):
        super().setUp()
        self.args = self.home / "args"
        browser = self.home / "browser"
        browser.write_text(f'#!/bin/sh\nprintf "%s\\n" "$@" > "{self.args}"\n'
                           'for a; do case "$a" in --print-to-pdf=*) : > "${a#--print-to-pdf=}";; esac; done\n')
        browser.chmod(browser.stat().st_mode | stat.S_IEXEC)
        os.environ["EXAM_FORGE_BROWSER"] = str(browser)
        book = {**json.loads(json.dumps(jobs.Contents.BOOK)), "format": "mixed"}
        book["parts"][0]["questions"][0]["verified"] = True
        self.book = self.home / "book.json"
        self.book.write_text(json.dumps(book))
        self.out = self.home / "books" / "out.pdf"

    def test_printing_never_touches_the_keychain(self):
        self.assertEqual(self.cli("render", self.book, self.out).returncode, 0)
        sent = self.args.read_text(encoding="utf-8").splitlines()
        self.assertIn("--use-mock-keychain", sent)
        self.assertIn("--password-store=basic", sent)

    def test_a_long_book_name_can_be_printed_again(self):
        out = self.out.with_name("k" * 248 + ".pdf")
        for _ in range(2):
            self.assertEqual(self.cli("render", self.book, out).returncode, 0)
        self.assertEqual(len(list(out.parent.glob("*.pdf"))), 2)

    def test_prints_from_next_to_the_book_and_cleans_up(self):
        r = self.cli("render", self.book, self.out)
        self.assertEqual(r.returncode, 0, r.stderr)
        sent = self.args.read_text(encoding="utf-8").splitlines()
        url = next(a for a in sent if a.startswith("file:"))
        self.assertTrue(url.startswith((self.out.parent / ".exam-forge-").as_uri()), url)
        self.assertTrue(url.endswith(".html"), url)
        self.assertEqual([p.name for p in self.out.parent.iterdir()], ["out.pdf"])
        profile = next(a for a in sent if a.startswith("--user-data-dir=")).split("=", 1)[1]
        self.assertFalse(Path(profile).parent.exists())

    def test_a_second_render_in_the_same_minute_keeps_working(self):
        for _ in range(3):
            r = self.cli("render", self.book, self.out)
            self.assertEqual(r.returncode, 0, r.stderr)

    def test_a_locked_book_gives_a_clear_message(self):
        self.out.parent.mkdir()
        self.out.write_bytes(b"%PDF-")
        argv = ["forge", "render", str(self.book), str(self.out)]
        with mock.patch.object(forge.sys, "argv", argv), mock.patch.object(forge.os, "replace", side_effect=PermissionError(13, "in use")), \
                mock.patch.object(forge.sys, "platform", "win32"), mock.patch.object(forge, "utf8_output"), \
                mock.patch.object(forge, "fail", side_effect=SystemExit(2)) as fail:
            with self.assertRaises(SystemExit):
                forge.main()
        self.assertIn("is in use, close it in your PDF viewer", fail.call_args[0][0])
        self.assertTrue(self.out.exists())

    def read_only(self, folder):
        folder.mkdir(exist_ok=True)
        folder.chmod(0o555)
        self.addCleanup(folder.chmod, 0o755)
        if os.access(folder, os.W_OK):
            self.skipTest("this user can write anywhere")
        return folder

    def test_a_read_only_folder_gives_a_clean_error(self):
        folder = self.read_only(self.home / "locked")
        for out in (folder / "k.pdf", folder / "new" / "k.pdf"):
            r = self.cli("render", self.book, out)
            self.assertEqual(r.returncode, 2, r.stderr)
            self.assertNotIn(b"Traceback", r.stderr)
            self.assertIn(b"ermission denied", r.stderr)

    def test_a_book_in_a_read_only_folder_names_the_real_cause(self):
        folder = self.home / "locked"
        folder.mkdir()
        (folder / "k.pdf").write_bytes(b"%PDF-")
        self.read_only(folder)
        r = self.cli("render", self.book, folder / "k.pdf")
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertNotIn(b"in use", r.stderr)
        self.assertIn(b"permissions", r.stderr)
        self.assertEqual((folder / "k.pdf").read_bytes(), b"%PDF-")

    def test_a_long_book_name_prints(self):
        out = self.home / "books" / ("x" * 250 + ".pdf")
        r = self.cli("render", self.book, out)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(out.exists())

    def test_a_read_only_folder_is_named_instead_of_the_temp_file(self):
        folder = self.read_only(self.home / "shut")
        r = self.cli("render", self.book, folder / "k.pdf")
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn(str(folder).encode(), r.stderr)
        for hidden in (b"Errno", b".html", b"Traceback"):
            self.assertNotIn(hidden, r.stderr)

    def test_a_html_path_that_cannot_be_written_gives_a_clean_error(self):
        r = self.cli("render", self.book, self.out, "--html", self.home / "no" / "such" / "x.html")
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertNotIn(b"Traceback", r.stderr)
        self.assertEqual([p.name for p in self.out.parent.iterdir()], [])

    def test_a_users_print_html_is_left_alone(self):
        self.out.parent.mkdir()
        mine = self.out.parent / "out.print.html"
        mine.write_text("mine")
        self.assertEqual(self.cli("render", self.book, self.out).returncode, 0)
        self.assertEqual(mine.read_text(), "mine")

    def test_html_may_use_the_print_name_and_is_a_normal_file(self):
        keep = self.out.parent / "out.print.html"
        self.out.parent.mkdir()
        r = self.cli("render", self.book, self.out, "--html", keep)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(b"<html", keep.read_bytes())
        self.assertNotEqual(keep.stat().st_mode & 0o777, 0o600)
        self.assertEqual(sorted(p.name for p in self.out.parent.iterdir()), ["out.pdf", "out.print.html"])

    def test_two_renders_of_one_book_do_not_share_a_file(self):
        slow = self.home / "slow"
        slow.write_text('#!/bin/sh\nsleep 0.3\nfor a; do case "$a" in file:*) test -s "${a#file://}" || exit 9;;'
                        ' --print-to-pdf=*) : > "${a#--print-to-pdf=}";; esac; done\n')
        slow.chmod(0o755)
        os.environ["EXAM_FORGE_BROWSER"] = str(slow)
        self.out.parent.mkdir()
        runs = [subprocess.Popen([sys.executable, FORGE, "render", self.book, self.out.with_name(f"{n}.pdf")],
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE) for n in "ab"]
        for r in runs:
            r.communicate()
            self.assertEqual(r.returncode, 0)
        self.assertEqual(sorted(p.name for p in self.out.parent.iterdir()), ["a.pdf", "b.pdf"])

    def test_a_backup_is_never_overwritten(self):
        self.out.parent.mkdir()
        when = 1700000000
        for version in ("one", "two", "three"):
            self.out.write_text(version)
            os.utime(self.out, (when, when))
            self.assertEqual(self.cli("render", self.book, self.out).returncode, 0)
        kept = sorted(p.read_text() for p in self.out.parent.iterdir() if p.name != "out.pdf")
        self.assertEqual(kept, ["one", "three", "two"])

    def test_a_windows_path_makes_a_valid_file_url(self):
        url = PureWindowsPath(r"C:\Users\Öğrenci\Ders Notları\out.print.html").as_uri()
        self.assertEqual(url, "file:///C:/Users/%C3%96%C4%9Frenci/Ders%20Notlar%C4%B1/out.print.html")


class Docs(unittest.TestCase):
    def test_update_needs_a_restart(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        for text in ("claude plugin marketplace update retryx", "claude plugin update exam-forge@retryx", "/plugin", "Restart Claude Code"):
            self.assertIn(text, readme)


if __name__ == "__main__":
    unittest.main()
