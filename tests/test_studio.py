"""Studio server tests, on a temporary port and a temporary home."""
import io
import http.client
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from unittest import mock

import conftest  # noqa: F401

ROOT = Path(__file__).resolve().parent.parent
FORGE = ROOT / "skills" / "exam-forge" / "bin" / "forge.py"
HOOK = FORGE.parent / "hook_event.py"


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Studio(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.td = tempfile.TemporaryDirectory()
        cls.home = Path(cls.td.name)
        cls.port = free_port()
        settings = cls.home / "port.json"  # `ask` looks for the Studio, and must find this one
        settings.write_text(json.dumps({"studio_port": cls.port}))
        cls.env = {**os.environ, "HOME": str(cls.home), "EXAM_FORGE_SETTINGS": str(settings)}
        pdf = cls.home / "lesson.pdf"
        pdf.write_bytes(b"%PDF-1.4\n")
        cls.run_dir = cls.home / "run"
        subprocess.run([sys.executable, FORGE, "plan", pdf, "--pages", "lesson.pdf=12", "--run", cls.run_dir],
                       env=cls.env, capture_output=True, check=True)
        cls.proc = subprocess.Popen([sys.executable, FORGE, "studio", "--no-open", "--port", str(cls.port)],
                                    env=cls.env, stdout=subprocess.DEVNULL)
        for _ in range(50):
            try:
                cls.get("/api/ping")
                break
            except OSError:
                time.sleep(0.1)

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        cls.proc.wait()
        cls.td.cleanup()

    @classmethod
    def get(cls, path, host=None):
        req = urllib.request.Request(f"http://127.0.0.1:{cls.port}{path}")
        if host:
            req.add_header("Host", host)
        with urllib.request.urlopen(req, timeout=3) as r:
            return r.read().decode("utf-8")

    def token(self):
        html = self.get("/")
        return html.split('name="forge-token" content="')[1].split('"')[0]

    def post(self, body, token=None, origin=None, path="/api/settings"):
        data = json.dumps(body).encode()
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=data, method="POST")
        req.add_header("Content-Type", "application/json")
        req.add_header("Origin", origin or f"http://127.0.0.1:{self.port}")
        req.add_header("X-Forge-Token", token if token is not None else self.token())
        try:
            with urllib.request.urlopen(req, timeout=3) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_a_body_that_will_never_arrive_is_refused_at_once(self):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=3)
        c.putrequest("POST", "/api/settings")
        c.putheader("Origin", f"http://127.0.0.1:{self.port}")
        c.putheader("X-Forge-Token", self.token())
        c.putheader("Content-Length", str(10 ** 15))
        c.endheaders()
        self.assertEqual(c.getresponse().status, 400)
        c.close()

    def test_a_body_that_is_not_an_object_gets_an_error_reply(self):
        for body in ([1], 5, "x", None):
            self.assertEqual(self.post(body)[0], 400, body)

    def test_every_route_wants_an_object_before_anything_else(self):
        token = self.token()
        for path in ("/api/settings", "/api/jobs", "/api/cancel", "/api/answer", "/api/browse", "/api/sources",
                     "/api/install", "/api/estimate"):
            for body in ([1], 5, "x", None, True):
                code, d = self.post(body, token=token, path=path)
                self.assertEqual((code, d["ok"]), (400, False), (path, body))
        self.assertEqual(self.post({}, token=token, path="/api/cancel")[0], 409)  # an object goes on to the route

    def test_sources_want_a_list_of_paths(self):
        for paths in ("/tmp", [""], ["  "], [1]):
            self.assertEqual(self.post({"paths": paths}, path="/api/sources")[0], 400, paths)

    def test_the_guard_keeps_intake_open_from_the_cli(self):
        folder = self.home / "fake-cli"
        folder.mkdir()
        marker = self.home / "cli-ran"
        (folder / "claude").write_text(f'#!/bin/sh\n: > "{marker}"\n')
        (folder / "claude").chmod(0o755)
        env = {**self.env, "PATH": f"{folder}{os.pathsep}/usr/bin{os.pathsep}/bin"}
        models = self.home / ".config" / "exam-forge" / "models.json"
        models.unlink(missing_ok=True)
        self.addCleanup(models.unlink, missing_ok=True)
        self.addCleanup(marker.unlink, missing_ok=True)

        def open_form():
            r = subprocess.run([sys.executable, FORGE, "intake", "open", "--no-open"], env=env, capture_output=True,
                               text=True, timeout=20)
            self.assertEqual(r.returncode, 0, r.stderr)

        self.assertEqual(env["EXAM_FORGE_NO_PROBE"], "1")
        open_form()
        time.sleep(2)
        self.assertFalse(marker.exists())
        self.assertFalse(models.exists())
        del env["EXAM_FORGE_NO_PROBE"]  # the control: the same stand-in does run without the guard, so this test can fail
        open_form()
        for _ in range(100):
            if models.exists():
                break
            time.sleep(0.1)
        self.assertTrue(marker.exists())

    def test_a_second_wait_never_costs_the_first_its_job(self):
        token, job = self.token(), self.job()
        for _ in range(5):
            first = self.forge("intake", "wait", "--timeout", "2")
            self.addCleanup(self.stop, first)
            for _ in range(60):
                if self.intake()["session"]:
                    break
                time.sleep(0.05)
            self.assertEqual(self.post(job, token=token, path="/api/jobs")[0], 200)
            second = self.forge("intake", "wait", "--timeout", "2")
            self.addCleanup(self.stop, second)
            outs = [p.communicate(timeout=10)[0] for p in (first, second)]
            self.assertEqual(sorted((first.returncode, second.returncode)), [0, 4], outs)
            self.assertEqual(sum('"pdfs"' in o for o in outs), 1, outs)

    def test_intake_open_leaves_a_job_for_a_live_wait_alone(self):
        box = self.home / ".config" / "exam-forge" / "intake"
        box.mkdir(parents=True, exist_ok=True)
        (box / "waiter.json").write_text(json.dumps({"pid": os.getpid()}))
        (box / "job.json").write_text('{"live": true}')
        self.addCleanup((box / "job.json").unlink, missing_ok=True)
        self.addCleanup((box / "waiter.json").unlink, missing_ok=True)
        r = subprocess.run([sys.executable, FORGE, "intake", "open", "--no-open"], env=self.env, capture_output=True,
                           text=True, timeout=20)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue((box / "job.json").exists())

    def test_a_damaged_waiter_pid_does_not_drop_the_page(self):
        box = self.home / ".config" / "exam-forge" / "intake"
        box.mkdir(parents=True, exist_ok=True)
        (box / "waiter.json").write_text(json.dumps({"pid": 2 ** 31}))
        self.addCleanup((box / "waiter.json").unlink, missing_ok=True)
        self.assertFalse(self.intake()["session"])

    def test_a_read_only_config_folder_gets_a_clear_reply(self):
        config = self.home / ".config" / "exam-forge"
        config.mkdir(parents=True, exist_ok=True)
        config.chmod(0o555)
        self.addCleanup(config.chmod, 0o755)
        if os.access(config, os.W_OK):
            self.skipTest("this user can write anywhere")
        code, d = self.post({"key": "mcq_options", "value": 4})
        self.assertEqual((code, d["ok"]), (400, False))
        self.assertIn("cannot save settings in", d["error"])
        self.assertNotIn("Errno", d["error"])

    def test_damaged_settings_get_a_clear_answer(self):
        path = self.home / ".config" / "exam-forge" / "settings.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        self.addCleanup(path.unlink, missing_ok=True)
        for text in ("[1]", "5", '"x"', "{oops", "null"):
            path.write_text(text)
            for route in ("/api/settings", "/api/doctor"):
                with self.assertRaises(urllib.error.HTTPError) as c:
                    self.get(route)
                self.assertEqual(c.exception.code, 500, (route, text))
                self.assertIn("settings.json", json.loads(c.exception.read())["error"])
            d = self.intake()
            self.assertEqual((d["session"], d["defaults"]), (False, None), text)
            r = subprocess.run([sys.executable, FORGE, "config", "set", "mcq_options", "4"], env=self.env,
                               capture_output=True, text=True, timeout=20)
            self.assertEqual(r.returncode, 2, r.stderr)
            self.assertNotIn("Traceback", r.stderr)
            self.assertIn("settings.json", r.stderr)

    def test_static_files(self):
        for path in ("/", "/app.js", "/office.js", "/sprites.js", "/font.js", "/i18n.js", "/style.css"):
            self.assertTrue(self.get(path))
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/pixel.ttf")
        with urllib.request.urlopen(req, timeout=3) as r:
            self.assertEqual(r.headers["Content-Type"], "font/ttf")
            self.assertTrue(r.read().startswith(b"\x00\x01\x00\x00"))
        self.assertNotIn("{{TOKEN}}", self.get("/"))

    def test_no_external_assets(self):
        for name in ("index.html", "style.css", "app.js", "office.js", "sprites.js"):
            text = (ROOT / "skills/exam-forge/studio" / name).read_text(encoding="utf-8")
            self.assertNotIn("http://", text.replace("http://127", ""), name)
            self.assertNotIn("https://", text, name)

    def test_runs_and_events(self):
        runs = json.loads(self.get("/api/runs"))["runs"]
        self.assertEqual(runs[0]["pdfs"], ["lesson"])
        d = json.loads(self.get(f"/api/events?run={runs[0]['id']}&since=0"))
        self.assertEqual(d["events"][0]["type"], "plan")
        self.assertEqual(d["total"], len(d["events"]))

    def test_events_hide_transcript_paths(self):
        with open(self.run_dir / "events.jsonl", "a") as f:
            f.write(json.dumps({"t": "2026-11-06T10:00:00", "type": "agent_start", "role": "editor",
                                "transcript": "/secret/path", "tool_use_id": "x"}) + "\n")
        self.assertNotIn("/secret/path", self.get("/api/events?run=latest&since=0"))

    def test_settings_read_and_write(self):
        d = json.loads(self.get("/api/settings"))
        self.assertEqual(d["values"]["answer_placement"], "book_end")
        code, r = self.post({"key": "answer_placement", "value": "inline"})
        self.assertEqual((code, r["ok"]), (200, True))
        self.assertEqual(r["values"]["answer_placement"], "inline")
        code, r = self.post({"key": "answer_placement", "reset": True})
        self.assertEqual(r["values"]["answer_placement"], "book_end")

    def test_settings_rejects_bad_value_and_unknown_key(self):
        self.assertEqual(self.post({"key": "answer_placement", "value": "nowhere"})[0], 400)
        self.assertEqual(self.post({"key": "nope", "value": 1})[0], 400)

    def test_post_needs_token_and_origin(self):
        self.assertEqual(self.post({"key": "mcq_options", "value": 4}, token="wrong")[0], 403)
        self.assertEqual(self.post({"key": "mcq_options", "value": 4}, origin="http://evil.example")[0], 403)

    def test_foreign_host_rejected(self):
        with self.assertRaises(urllib.error.HTTPError) as c:
            self.get("/api/ping", host="evil.example")
        self.assertEqual(c.exception.code, 403)

    def test_second_start_reuses_running_server(self):
        r = subprocess.run([sys.executable, FORGE, "studio", "--no-open", "--port", str(self.port)],
                           env=self.env, capture_output=True, text=True, timeout=10)
        self.assertIn("already running", r.stdout)


    def forge(self, *args, **kw):
        return subprocess.Popen([sys.executable, FORGE, *map(str, args)], env=self.env,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, **kw)

    def stop(self, proc):
        proc.kill()
        proc.communicate()
        (self.home / ".config/exam-forge/intake/waiter.json").unlink(missing_ok=True)

    def intake(self):
        return json.loads(self.get("/api/intake"))

    def waiter(self):
        proc = self.forge("intake", "wait", "--timeout", "20")
        self.addCleanup(self.stop, proc)
        for _ in range(50):
            if self.intake()["session"]:
                return proc
            time.sleep(0.1)
        self.fail("session never showed up")

    def job(self, **over):
        return {"pdfs": [str(self.home / "lesson.pdf")], "format": "mcq", "mcq_options": 5,
                "answer_placement": "inline", "merge": True, "notes": " chapter 2 ", "field": "", **over}

    def test_job_needs_a_waiting_session(self):
        self.assertFalse(self.intake()["session"])
        code, r = self.post(self.job(), path="/api/jobs")
        self.assertEqual((code, r["error"]), (409, "no_session"))

    def test_job_reaches_the_waiting_session(self):
        proc = self.waiter()
        code, r = self.post(self.job(), path="/api/jobs")
        self.assertEqual((code, r["ok"]), (200, True))
        out, _ = proc.communicate(timeout=10)
        self.assertEqual(proc.returncode, 0)
        job = json.loads(out)
        self.assertEqual(job["pdfs"], [str((self.home / "lesson.pdf").resolve())])
        self.assertEqual((job["answer_placement"], job["notes"], job["merge"]), ("inline", "chapter 2", None))
        self.assertFalse(self.intake()["session"])

    def test_a_killed_session_takes_no_job_and_a_later_wait_ignores_one_left_behind(self):
        proc = self.waiter()
        proc.kill()
        proc.wait()
        self.assertFalse(self.intake()["session"])
        self.assertEqual(self.post(self.job(), path="/api/jobs")[0], 409)
        box = self.home / ".config" / "exam-forge" / "intake"
        (box / "job.json").write_text('{"stale": true}')
        later = self.forge("intake", "wait", "--timeout", "2")
        self.addCleanup(self.stop, later)
        out, _ = later.communicate(timeout=10)
        self.assertEqual((later.returncode, json.loads(out)), (4, {"timeout": True}))

    def test_parallel_jobs_and_settings_all_get_a_true_answer(self):
        box = self.home / ".config" / "exam-forge" / "intake"
        box.mkdir(parents=True, exist_ok=True)
        waiter = box / "waiter.json"
        waiter.write_text(json.dumps({"pid": os.getpid()}))
        self.addCleanup(lambda: (box / "job.json").unlink(missing_ok=True))
        self.addCleanup(lambda: waiter.unlink(missing_ok=True))
        token = self.token()

        def together(n, call):
            codes = []
            threads = [threading.Thread(target=lambda i=i: codes.append(call(i))) for i in range(n)]
            [t.start() for t in threads]
            [t.join() for t in threads]
            return codes

        self.assertEqual(together(30, lambda i: self.post(self.job(), token=token, path="/api/jobs")[0]), [200] * 30)
        self.assertEqual(together(40, lambda i: self.post({"key": "max_rounds", "value": 3 + i % 2}, token=token)[0]), [200] * 40)
        self.assertEqual(sorted(p.name for p in box.glob("*.tmp")), [])
        self.post({"key": "max_rounds", "reset": True}, token=token)

    def test_a_disk_error_gets_a_reply_instead_of_a_dropped_connection(self):
        config = self.home / ".config" / "exam-forge"
        token = self.token()
        config.chmod(0o555)
        self.addCleanup(config.chmod, 0o755)
        if os.access(config, os.W_OK):
            self.skipTest("this user can write anywhere")
        code, r = self.post({"key": "max_rounds", "value": 4}, token=token)
        self.assertEqual((code, r["ok"]), (400, False))
        self.assertIn("ermission denied", r["error"])

    def test_job_rejects_bad_input_and_keeps_waiting(self):
        proc = self.waiter()
        not_pdf = self.home / "note.pdf"
        not_pdf.write_text("hello")
        for over in ({"pdfs": []}, {"pdfs": ["/no/such.pdf"]}, {"pdfs": [str(not_pdf)]}, {"format": "essay"},
                     {"mcq_options": 3}, {"answer_placement": "nowhere"}, {"notes": "x" * 4001},
                     {"pdfs": [str(self.home / "lesson.pdf")] * 2}):
            self.assertEqual(self.post(self.job(**over), path="/api/jobs")[0], 400, over)
        self.assertIsNone(proc.poll())

    def test_job_post_needs_token(self):
        proc = self.waiter()
        self.assertEqual(self.post(self.job(), token="wrong", path="/api/jobs")[0], 403)
        self.assertEqual(self.post({}, token="wrong", path="/api/browse")[0], 403)
        self.assertEqual(self.post({"paths": []}, token="wrong", path="/api/sources")[0], 403)
        self.assertIsNone(proc.poll())

    def test_cancel_ends_the_wait(self):
        proc = self.waiter()
        self.assertEqual(self.post({}, path="/api/cancel")[0], 200)
        proc.communicate(timeout=10)
        self.assertEqual(proc.returncode, 5)

    def test_wait_times_out(self):
        r = subprocess.run([sys.executable, FORGE, "intake", "wait", "--timeout", "0"], env=self.env,
                           capture_output=True, text=True, timeout=10)
        self.assertEqual(r.returncode, 4)

    def test_intake_open_prefills_the_form(self):
        cfg = self.home / "port.json"
        cfg.write_text(json.dumps({"studio_port": self.port}))
        r = subprocess.run([sys.executable, FORGE, "intake", "open", "--no-open", "--pdf", self.home / "lesson.pdf",
                            "--notes", "chapter 2"], env={**self.env, "EXAM_FORGE_SETTINGS": str(cfg)},
                           capture_output=True, text=True, timeout=10)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout)["url"], f"http://127.0.0.1:{self.port}/?tab=job")
        req = self.intake()["request"]
        self.assertEqual((req["notes"], len(req["pdfs"])), ("chapter 2", 1))

    def test_ask_is_answered_from_the_studio(self):
        proc = self.forge("ask", self.run_dir, "--id", "r1", "--text", "Drop it?", "--option", "drop=Drop",
                          "--option", "go=Go on", "--timeout", "20")
        self.addCleanup(self.stop, proc)
        for _ in range(50):
            ask = self.intake()["ask"]
            if ask:
                break
            time.sleep(0.1)
        self.assertEqual((ask["text"], ask["options"][1]["label"]), ("Drop it?", "Go on"))
        self.assertEqual(self.post({"run": ask["run"], "id": "r1", "value": "nope"}, path="/api/answer")[0], 400)
        self.assertEqual(self.post({"run": ask["run"], "id": "r1", "value": "go"}, path="/api/answer")[0], 200)
        out, _ = proc.communicate(timeout=10)
        self.assertEqual((proc.returncode, out.strip()), (0, "go"))
        self.assertIsNone(self.intake()["ask"])

    def sources(self):
        base = self.home / "courses"
        (base / "week1").mkdir(parents=True, exist_ok=True)
        (base / ".hidden").mkdir(exist_ok=True)
        for f in ("a.pdf", "week1/b.PDF", ".hidden/c.pdf"):
            (base / f).write_bytes(b"%PDF-1.4\n")
        (base / "notes.txt").write_text("skip me")
        return base

    def test_browse_shows_only_folders_and_pdfs(self):
        base = self.sources()
        code, d = self.post({"path": str(base)}, path="/api/browse")
        self.assertEqual(code, 200)
        self.assertEqual([e["name"] for e in d["entries"]], ["week1", "a.pdf"])
        self.assertEqual(d["parent"], str(base.resolve().parent))
        self.assertEqual(self.post({}, path="/api/browse")[1]["path"], str(self.home.resolve()))
        self.assertEqual(self.post({"path": str(base / "gone")}, path="/api/browse")[0], 400)
        d = self.post({"path": str(base / "a.pdf")}, path="/api/browse")[1]
        self.assertEqual(Path(d["file"]).name, "a.pdf")

    def test_sources_expand_folders_and_files(self):
        base = self.sources()
        code, d = self.post({"paths": [str(base), str(base / "a.pdf")]}, path="/api/sources")
        self.assertEqual(code, 200)
        self.assertEqual(sorted(Path(p).name for p in d["pdfs"]), ["a.pdf", "b.PDF"])
        empty = base / "empty"
        empty.mkdir(exist_ok=True)
        self.assertEqual(self.post({"paths": [str(empty)]}, path="/api/sources")[0], 400)
        self.assertEqual(self.post({"paths": [str(base / "notes.txt")]}, path="/api/sources")[0], 400)
        (base / "fake.pdf").write_text("no")
        self.addCleanup((base / "fake.pdf").unlink)
        code, d = self.post({"paths": [str(base)]}, path="/api/sources")
        self.assertEqual((code, d["skipped"]), (200, ["fake.pdf"]))
        self.assertTrue(d["pdfs"])

    def test_intake_open_names_what_it_left_out(self):
        base = self.sources()
        (base / "fake.pdf").write_text("no")
        self.addCleanup((base / "fake.pdf").unlink)
        box = self.home / ".config" / "exam-forge" / "intake"
        box.mkdir(parents=True, exist_ok=True)
        (box / "job.json").write_text('{"stale": true}')
        r = subprocess.run([sys.executable, FORGE, "intake", "open", "--no-open", "--pdf", base],
                           env=self.env, capture_output=True, text=True, timeout=20)
        self.assertEqual((r.returncode, json.loads(r.stdout)["skipped"]), (0, ["fake.pdf"]), r.stderr)
        self.assertFalse((box / "job.json").exists())

    def upload(self, data, name, batch="123", token=None):
        q = urllib.parse.urlencode({"batch": batch, "name": name})
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/api/upload?{q}", data=data, method="POST")
        req.add_header("Origin", f"http://127.0.0.1:{self.port}")
        req.add_header("X-Forge-Token", token if token is not None else self.token())
        try:
            with urllib.request.urlopen(req, timeout=3) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_upload_saves_a_pdf_without_overwriting(self):
        code, d = self.upload(b"%PDF-1.4 x", "ders notu.pdf")
        self.assertEqual(code, 200, d)
        first = Path(d["path"])
        self.assertEqual(first.parent, self.home / "Exam Forge" / "123")
        self.assertEqual(first.read_bytes(), b"%PDF-1.4 x")
        self.assertEqual(Path(self.upload(b"%PDF-1.4 y", "ders notu.pdf")[1]["path"]).name, "ders notu-2.pdf")

    def test_parallel_uploads_of_one_name_all_arrive(self):
        token, got = self.token(), {}
        bodies = [b"%PDF-1.4 " + str(i).encode() for i in range(10)]

        def send(i):
            got[i] = self.upload(bodies[i], "same.pdf", batch="777", token=token)

        threads = [threading.Thread(target=send, args=(i,)) for i in range(10)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual([got[i][0] for i in range(10)], [200] * 10, got)
        folder = self.home / "Exam Forge" / "777"
        self.addCleanup(shutil.rmtree, folder)
        self.assertEqual({Path(got[i][1]["path"]).read_bytes() for i in range(10)}, set(bodies))
        self.assertEqual(len(list(folder.iterdir())), 10)

    def test_a_failed_upload_leaves_nothing(self):
        self.assertEqual(self.upload(b"hello", "fake.pdf", batch="778")[0], 400)
        self.assertFalse((self.home / "Exam Forge" / "778").exists())

    def test_an_upload_name_too_long_gets_a_clean_reply(self):
        code, d = self.upload(b"%PDF-1.4", "a" * 260 + ".pdf", batch="779")
        self.assertEqual((code, d["ok"]), (400, False))
        self.assertLess(len(d["error"]), 100)
        self.assertFalse((self.home / "Exam Forge" / "779").exists())

    def test_upload_rejects_bad_input(self):
        self.assertEqual(self.upload(b"%PDF-1.4", "a.pdf", token="wrong")[0], 403)
        self.assertEqual(self.upload(b"hello", "fake.pdf", batch="456")[0], 400)
        self.assertEqual(self.upload(b"%PDF-1.4", "a.txt")[0], 400)
        self.assertEqual(self.upload(b"%PDF-1.4", "a.pdf", batch="../up")[0], 400)
        code, d = self.upload(b"%PDF-1.4", "../../evil.pdf", batch="789")
        self.assertEqual(Path(d["path"]).parent.name, "789")
        self.assertFalse((self.home / "Exam Forge" / "456").exists())

    def test_older_studio_is_not_reused(self):
        import http.server
        import threading

        class Old(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"app": "exam-forge"}')

            def log_message(self, *args):
                pass

        old = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Old)
        threading.Thread(target=old.serve_forever, daemon=True).start()
        self.addCleanup(old.server_close)
        self.addCleanup(old.shutdown)
        r = subprocess.run([sys.executable, "-c", (
            "import sys; sys.path[:0] = [sys.argv[1], sys.argv[2]]\n"
            "import server; print(server.is_studio(int(sys.argv[3])), server.is_studio(int(sys.argv[4])))")
            , FORGE.parent.parent / "studio", FORGE.parent, str(old.server_address[1]), str(self.port)],
            env=self.env, capture_output=True, text=True, timeout=10)
        self.assertEqual(r.stdout.split(), ["False", "True"], r.stderr)

    def test_book_is_served_only_from_render_events(self):
        book = self.home / "lesson-questions.pdf"
        book.write_bytes(b"%PDF-1.4 book")
        rid = json.loads(self.get("/api/runs"))["runs"][0]["id"]
        with self.assertRaises(urllib.error.HTTPError):
            self.get(f"/book?run={rid}&n=99")
        with open(self.run_dir / "events.jsonl", "a") as f:
            f.write(json.dumps({"t": "2026-11-06T10:00:00", "type": "render", "out": str(book)}) + "\n")
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/book?run={rid}&n=0", timeout=3) as r:
            self.assertEqual((r.headers["Content-Type"], r.read()), ("application/pdf", b"%PDF-1.4 book"))
        with self.assertRaises(urllib.error.HTTPError):
            self.get(f"/book?run={rid}&n=x")


class Page(unittest.TestCase):
    """What only shows in the source; these were run by hand in a headless Chrome."""
    STUDIO = FORGE.parent.parent / "studio"

    def test_the_eventsource_is_kept(self):
        self.assertIn("presence = new EventSource(", (self.STUDIO / "app.js").read_text(encoding="utf-8"))

    def test_the_gone_message_exists_in_both_languages(self):
        text = (self.STUDIO / "i18n.js").read_text(encoding="utf-8")
        for key in ("studio_gone", "skipped_pdfs"):
            self.assertEqual(text.count(f"{key}:"), 2)
        self.assertIn('id="gone"', (self.STUDIO / "index.html").read_text(encoding="utf-8"))


class Hook(unittest.TestCase):
    def test_records_agent_start_and_ignores_others(self):
        with tempfile.TemporaryDirectory() as td:
            run = Path(td) / "my run"
            run.mkdir()
            (run / "plan.json").write_text("{}")

            def fire(prompt):
                payload = {"tool_input": {"prompt": prompt, "subagent_type": "exam-forge:reviewer", "model": "opus"},
                           "tool_use_id": "toolu_1", "transcript_path": "/x/s.jsonl"}
                r = subprocess.run([sys.executable, HOOK], input=json.dumps(payload), capture_output=True, text=True)
                self.assertEqual((r.returncode, r.stdout), (0, ""))

            fire("something unrelated")
            self.assertFalse((run / "events.jsonl").exists())
            fire(f"EXAM_FORGE: run={run}|pdf=lesson|chunk=2|round=3\nReview this.")
            e = json.loads((run / "events.jsonl").read_text())
            self.assertEqual((e["role"], e["pdf"], e["chunk"], e["round"]), ("reviewer", "lesson", 2, 3))
            self.assertEqual(e["tool_use_id"], "toolu_1")


class Lifecycle(unittest.TestCase):
    """When a Studio stops by itself, on short timers. Each test has its own home."""
    FAST = "WATCH_SECONDS=0.1; s.RETIRE_SECONDS=0.3; s.HEARTBEAT_SECONDS=0.1"

    def setUp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.home = Path(td.name)
        self.config = self.home / ".config" / "exam-forge"
        self.env = {**os.environ, "HOME": str(self.home)}
        self.env.pop("EXAM_FORGE_SETTINGS", None)

    def studio(self, build=None, idle=1000):
        """Start a Studio with short timers and wait until it answers."""
        port = free_port()
        code = (f"import sys; sys.path[:0] = [{str(FORGE.parent.parent / 'studio')!r}, {str(FORGE.parent)!r}]\n"
                f"import server as s; s.{self.FAST}; s.IDLE_SECONDS = {idle}\n"
                + (f"s.BUILD = {build!r}\n" if build else "") + f"s.main(port={port}, open_browser=False)")
        proc = subprocess.Popen([sys.executable, "-c", code], env=self.env, stdout=subprocess.PIPE, text=True)
        self.addCleanup(proc.stdout.close)
        self.addCleanup(self.stop, proc)
        port = int(re.search(r":(\d+)", proc.stdout.readline()).group(1))
        self.assertEqual(self.ping(port)["pid"], proc.pid)
        return proc, port

    def stop(self, proc):
        proc.kill()
        proc.wait()

    def ping(self, port, query=""):
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/ping{query}", timeout=2) as r:
            return json.load(r)

    def old_and_new(self):
        old = self.studio(build="old")
        return old, self.studio()

    def goes_away(self, proc, seconds=6):
        try:
            proc.wait(seconds)
            return True
        except subprocess.TimeoutExpired:
            return False

    def stays(self, proc, seconds=1.2):
        time.sleep(seconds)
        return proc.poll() is None

    def open_page(self, port, answer=b"200"):
        """A browser's EventSource, without any polling."""
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=2) as r:
            token = re.search(r'forge-token" content="([^"]+)', r.read().decode()).group(1)
        page = socket.create_connection(("127.0.0.1", port), timeout=3)
        self.addCleanup(page.close)
        page.sendall(f"GET /api/presence?token={token} HTTP/1.0\r\nHost: 127.0.0.1:{port}\r\n\r\n".encode())
        self.assertIn(answer, page.recv(100))
        return page

    def job(self, age=0):
        run = self.home / "run"
        run.mkdir(exist_ok=True)
        (run / "plan.json").write_text("{}")
        events = run / "events.jsonl"
        events.write_text("{}\n")
        when = time.time() - age
        os.utime(events, (when, when))
        (self.config / "runs.json").write_text(json.dumps([str(run)]))
        return run

    def test_a_replaced_studio_stops_and_the_new_one_stays(self):
        (old, _), (new, _) = self.old_and_new()
        self.assertTrue(self.goes_away(old))
        self.assertTrue(self.stays(new))

    def test_a_replaced_studio_stays_while_a_page_is_open(self):
        (old, port), _ = self.old_and_new()
        page = self.open_page(port)
        self.assertTrue(self.stays(old))
        self.assertTrue(self.ping(port)["watching"])  # the page sends nothing, and its timers may be asleep
        page.close()
        self.assertTrue(self.goes_away(old))
        self.assertFalse(self.ping(self.new_port())["watching"])

    def new_port(self):
        return json.loads((self.config / "studio.json").read_text())["port"]

    def test_a_polling_page_alone_also_keeps_it(self):
        (old, port), _ = self.old_and_new()
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/intake", timeout=2):
            pass
        self.assertTrue(self.ping(port)["watching"])
        self.assertTrue(self.stays(old, 3))

    def test_a_replaced_studio_stays_during_a_job(self):
        (old, _), _ = self.old_and_new()
        run = self.job()
        self.assertTrue(self.stays(old))
        self.job(age=16 * 60)
        self.assertTrue(self.goes_away(old))
        self.assertTrue(run.is_dir())

    def test_a_replaced_studio_stays_while_a_question_is_open(self):
        (old, _), _ = self.old_and_new()
        run = self.job(age=16 * 60)
        (run / "ask.json").write_text(json.dumps({"id": "a", "text": "?", "options": []}))
        self.assertTrue(self.stays(old))
        (run / "ask.json").unlink()
        self.assertTrue(self.goes_away(old))

    def test_a_waiting_session_holds_only_the_newest(self):
        (old, _), (new, _) = self.old_and_new()
        (self.config / "intake").mkdir()
        (self.config / "intake" / "waiter.json").write_text("{}")
        self.assertTrue(self.goes_away(old))
        self.assertTrue(self.stays(new))

    def test_a_studio_stays_when_its_replacement_is_gone(self):
        (old, _), (new, _) = self.old_and_new()
        new.kill()
        new.wait()
        self.assertTrue(self.stays(old))

    def test_a_bad_file_never_stops_the_stopping(self):
        for name, text in (("runs.json", "[1]"), ("runs.json", "5"), ("studio.json", '{"pid": 1}'), ("studio.json", "[1]")):
            with self.subTest(file=name, text=text):
                proc, _ = self.studio(idle=0.5)
                (self.config / name).write_text(text)
                self.assertTrue(self.goes_away(proc))
                (self.config / name).unlink()

    def test_an_unreadable_runs_file_counts_as_no_runs(self):
        server = self.server()
        self.config.mkdir(parents=True)
        runs = self.config / "runs.json"
        runs.write_text("[]")
        runs.chmod(0)
        self.addCleanup(runs.chmod, 0o644)
        with mock.patch.dict(os.environ, {"HOME": str(self.home)}):
            self.assertEqual(server.known_runs(), [])
            runs.chmod(0o644)
            good = self.job()
            runs.write_text(json.dumps([1, str(good)]))
            self.assertEqual(server.known_runs(), [good])

    def test_the_watcher_survives_a_failing_check(self):
        server = self.server()
        host = mock.Mock()
        host.should_stop.side_effect = [TypeError("bad"), PermissionError("bad"), False, True]
        with mock.patch.object(server, "WATCH_SECONDS", 0):
            server.Server.watch(host)
        self.assertEqual(host.should_stop.call_count, 4)
        host.shutdown.assert_called_once_with()

    def test_the_mark_is_read_through_one_guard(self):
        server = self.server()
        self.config.mkdir(parents=True)
        mark = self.config / "studio.json"
        with mock.patch.dict(os.environ, {"HOME": str(self.home)}):
            for text in ("[1]", "5", "null", '{"pid": 1}', '{"port": 40000}', '{"pid": "1", "port": 40000}',
                         '{"pid": 1, "port": true}', '{"pid": 1, "port": 99999999}', "not json"):
                mark.write_text(text)
                self.assertEqual(server.read_mark(), {}, text)
                with mock.patch.object(server, "is_studio", return_value=False):
                    self.assertEqual(server.find_studio(40000), 0, text)
            mark.write_text('{"pid": 1, "port": 40000}')
            self.assertEqual(server.read_mark(), {"pid": 1, "port": 40000})

    def test_intake_open_works_with_a_list_for_a_mark(self):
        self.config.mkdir(parents=True)
        (self.config / "studio.json").write_text("[1]")
        port = free_port()
        (self.config / "settings.json").write_text(json.dumps({"studio_port": port}))
        r = subprocess.run([sys.executable, FORGE, "intake", "open", "--no-open"], env=self.env,
                           capture_output=True, text=True, timeout=30)
        self.addCleanup(self.stop_marked)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(f":{port}/", r.stdout)

    def stop_marked(self):
        os.kill(json.loads((self.config / "studio.json").read_text())["pid"], signal.SIGTERM)

    def test_ask_starts_a_studio_that_has_closed(self):
        self.config.mkdir(parents=True)
        run = self.job()
        port = free_port()
        (self.config / "settings.json").write_text(json.dumps({"studio_port": port}))
        r = subprocess.run([sys.executable, FORGE, "ask", run, "--id", "a", "--text", "Go on?", "--option", "y=Yes",
                            "--timeout", "1"], env=self.env, capture_output=True, text=True, timeout=30)
        self.addCleanup(self.stop_marked)
        self.assertEqual(r.returncode, 4, r.stderr)
        self.assertEqual(self.ping(port)["app"], "exam-forge")

    def test_a_proxy_does_not_hide_a_studio(self):
        _, port = self.studio()
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/intake", timeout=2):
            pass
        code = (f"import sys; sys.path[:0] = [{str(FORGE.parent.parent / 'studio')!r}, {str(FORGE.parent)!r}]\n"
                f"import forge, server; print(server.ping({port}).get('app'), forge.page_open('http://127.0.0.1:{port}'))")
        dead = "http://127.0.0.1:9"
        env = {**self.env, "http_proxy": dead, "HTTP_PROXY": dead, "no_proxy": "", "NO_PROXY": ""}
        r = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=20)
        self.assertEqual(r.stdout.split(), ["exam-forge", "True"], r.stderr)

    def test_something_else_on_a_port_is_not_a_studio(self):
        server = self.server()
        listener = socket.create_server(("127.0.0.1", 0))
        self.addCleanup(listener.close)

        def answer(text):
            conn, _ = listener.accept()
            conn.recv(1000)
            conn.sendall(text)
            conn.close()

        for text in (b"garbage\r\n\r\n", b"HTTP/1.0 200 OK\r\n\r\n[1]"):
            thread = threading.Thread(target=answer, args=(text,))
            thread.start()
            self.assertEqual(server.ping(listener.getsockname()[1]), {})
            thread.join()

    def test_a_full_port_range_stops_with_a_message(self):
        server = self.server()
        with mock.patch.object(server, "find_studio", return_value=0), mock.patch.object(server, "free_port", return_value=0), \
                mock.patch.object(server, "Server") as start, mock.patch.dict(os.environ, {"HOME": str(self.home)}), \
                mock.patch("sys.stderr", new_callable=io.StringIO) as err:
            with self.assertRaises(SystemExit) as stop:
                server.main(port=40000, open_browser=False)
        self.assertEqual(stop.exception.code, 2)
        self.assertIn("no free port from 40000 to 40029", err.getvalue())
        start.assert_not_called()

    def test_only_a_few_pages_hold_a_connection_and_the_others_still_count(self):
        proc, port = self.studio(idle=1000)
        pages = [self.open_page(port) for _ in range(3)]
        late = self.open_page(port, b"204")
        while late.recv(100):  # the connection ends; a held one would time out here
            pass
        for page in pages:
            page.close()
        for _ in range(50):
            if not self.ping(port)["watching"]:
                break
            time.sleep(0.1)
        self.assertFalse(self.ping(port)["watching"])
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/intake", timeout=2):
            pass
        self.assertTrue(self.ping(port)["watching"])  # a page with no held connection, polling
        self.open_page(port)  # a closed page gave its place back

    def test_a_second_studio_command_opens_no_second_window(self):
        server = self.server()
        for watching, opened in ((True, 0), (False, 1)):
            with mock.patch.object(server, "find_studio", return_value=39999), \
                    mock.patch.object(server.forge, "page_open", return_value=watching), \
                    mock.patch.object(server.forge, "open_window") as window, mock.patch("builtins.print"):
                server.main(port=39999)
            self.assertEqual(window.call_count, opened)

    @unittest.skipIf(sys.platform == "win32", "uses RLIMIT_NOFILE")
    def test_a_page_on_a_high_file_descriptor_is_held(self):
        import resource
        server = self.server()
        soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
        wanted = 2000
        if hard != resource.RLIM_INFINITY and hard <= wanted:
            self.skipTest("cannot raise the file limit")
        resource.setrlimit(resource.RLIMIT_NOFILE, (wanted + 1, hard))
        self.addCleanup(resource.setrlimit, resource.RLIMIT_NOFILE, (soft, hard))
        a, b = socket.socketpair()
        self.addCleanup(a.close)
        high = socket.socket(fileno=os.dup2(b.detach(), wanted))
        self.addCleanup(high.close)
        a.close()
        handler = mock.Mock(spec=server.Handler, connection=high, wfile=io.BytesIO())
        handler.server = mock.Mock(pages=set(), holds=threading.Semaphore(1))
        server.Handler.hold_page(handler)  # select.select raised ValueError here
        handler.server.touch.assert_called_once_with()

    def server(self):
        sys.path.insert(0, str(FORGE.parent.parent / "studio"))
        sys.path.insert(0, str(FORGE.parent))
        import server
        return server

    def test_a_mark_that_is_broken_replaces_nothing(self):
        proc, _ = self.studio()
        (self.config / "studio.json").write_text("not json")
        self.assertTrue(self.stays(proc))

    def test_an_idle_studio_stops_even_while_others_ping_it(self):
        proc, port = self.studio(idle=1)
        deadline = time.time() + 6
        while proc.poll() is None and time.time() < deadline:
            try:
                self.ping(port, "?build=another")
            except OSError:
                break
            time.sleep(0.1)
        self.assertTrue(self.goes_away(proc, 1))

    def test_a_caller_that_can_use_the_studio_keeps_it(self):
        proc, port = self.studio(idle=1)
        build = self.ping(port)["build"]
        for _ in range(16):
            self.ping(port, f"?build={build}")
            time.sleep(0.1)
        self.assertIsNone(proc.poll())

    def test_an_idle_studio_stays_during_a_job(self):
        self.config.mkdir(parents=True)
        self.job()
        proc, _ = self.studio(idle=0.2)
        self.assertTrue(self.stays(proc))

    def test_presence_needs_the_host_and_the_token(self):
        _, port = self.studio()
        for headers, path in (({}, "/api/presence?token=wrong"), ({}, "/api/presence"),
                              ({"Host": "evil.example"}, "/api/presence?token=x")):
            req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", headers=headers)
            with self.assertRaises(urllib.error.HTTPError) as e:
                urllib.request.urlopen(req, timeout=2)
            self.assertEqual(e.exception.code, 403)
        self.assertFalse(self.ping(port)["watching"])

    def test_an_open_page_gets_a_heartbeat(self):
        _, port = self.studio()
        page = self.open_page(port)
        got = b""
        while b": here" not in got:
            got += page.recv(100)

    def test_a_folder_at_the_mark_does_not_stop_a_studio(self):
        (self.config / "studio.json").mkdir(parents=True)
        self.studio()

    def silent_range(self, size):
        """A whole port range of listeners that accept a connection and never answer."""
        for start in range(42000, 60000, size):
            held = []
            try:
                for port in range(start, start + size):
                    s = socket.socket()
                    held.append(s)
                    s.bind(("127.0.0.1", port))
                    s.listen(5)
                [self.addCleanup(s.close) for s in held]
                return start
            except OSError:
                [s.close() for s in held]
        self.skipTest("no free port range")

    def test_a_full_range_of_silent_ports_fails_fast_and_says_why(self):
        self.config.mkdir(parents=True)
        start = self.silent_range(self.server().PORT_RANGE)
        (self.config / "settings.json").write_text(json.dumps({"studio_port": start}))
        began = time.monotonic()
        r = subprocess.run([sys.executable, FORGE, "studio-url"], env=self.env, capture_output=True, text=True, timeout=60)
        self.assertLess(time.monotonic() - began, 10)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("no free port", r.stderr)
        self.assertNotIn("Traceback", r.stderr)
        self.assertFalse((self.config / "studio.json").exists())

    def test_a_studio_that_never_answers_is_stopped_before_the_failure_is_told(self):
        server = self.server()
        pids, hang = self.home / "pid", self.home / "hang"
        hang.write_text(f'#!/bin/sh\necho $$ > "{pids}"\nexec sleep 60\n')
        hang.chmod(0o755)
        with mock.patch.dict(os.environ, {"HOME": str(self.home)}), mock.patch.object(server.forge, "S", {"studio_port": 40000}), \
                mock.patch.object(server, "find_studio", return_value=0), mock.patch.object(server.forge.sys, "executable", str(hang)), \
                mock.patch.object(server.forge, "STUDIO_START_TICKS", 15), mock.patch("sys.stderr", new_callable=io.StringIO) as err:
            with self.assertRaises(SystemExit):
                server.forge.ensure_studio()
        self.assertIn("Studio did not start", err.getvalue())
        self.assertFalse(server.forge.pid_alive(int(pids.read_text())))

    def test_the_newest_studio_is_tried_first(self):
        sys.path.insert(0, str(FORGE.parent.parent / "studio"))
        sys.path.insert(0, str(FORGE.parent))
        import server
        self.config.mkdir(parents=True)
        (self.config / "studio.json").write_text(json.dumps({"pid": 1, "port": 40007}))
        with mock.patch.dict(os.environ, {"HOME": str(self.home)}):
            with mock.patch.object(server, "is_studio", return_value=True) as check:
                self.assertEqual(server.find_studio(40000), 40007)
            check.assert_called_once_with(40007)
            with mock.patch.object(server, "is_studio", return_value=False) as check:
                self.assertEqual(server.find_studio(40000), 0)  # still looks at every port
            self.assertEqual(check.call_count, server.PORT_RANGE + 1)
            with mock.patch.object(server, "is_studio", return_value=True) as check:
                self.assertEqual(server.find_studio(50000), 50000)  # another range: the setting changed


@unittest.skipUnless(shutil.which("node"), "needs node")
class OfficeLoop(unittest.TestCase):
    """office.js with a stand-in canvas: only the frame loop is under test."""
    STUB = r"""
      const fs = require("fs"), vm = require("vm");
      let queued = [], draws = 0;
      const pen = new Proxy({}, { get: (o, k) => (k in o ? o[k] : () => ({})), set: (o, k, v) => (o[k] = v, true) });
      const canvas = { getContext: () => pen, clientWidth: 360 };
      const window = { matchMedia: () => ({ matches: true }) };
      vm.runInNewContext(fs.readFileSync(process.argv[1], "utf8"),
        { window, Sprites: pen, requestAnimationFrame: (f) => queued.push(f), Math, Date, console });
      const office = new window.Office(canvas);
      office.draw = () => { draws++; };
      office.update = () => {};
      const run = (t) => { const q = queued; queued = []; q.forEach((f) => f(t)); };
      let t = 1000;
      for (let i = 0; i < 60; i++) run(t += 1000 / 60);
      const shown = draws;
      canvas.clientWidth = 0;
      for (let i = 0; i < 60; i++) run(t += 1000 / 60);
      const slept = queued.length;
      const away = draws - shown;
      canvas.clientWidth = 360;
      office.wake(); office.wake();
      const woke = queued.length;
      for (let i = 0; i < 60; i++) run(t += 1000 / 60);
      console.log(JSON.stringify({ shown, away, slept, woke, again: draws - shown - away }));
    """

    def test_arrow_keys_on_a_slider_go_their_way_and_land_on_a_step(self):
        text = (FORGE.parent.parent / "studio" / "app.js").read_text(encoding="utf-8")
        snap = re.search(r"const snapStep = .*", text).group(0)
        balance = re.search(r"function rebalance[\s\S]*?\n  }\n", text).group(0)
        self.assertIn("snapStep(mix[k], +i.value)", text)
        moves = [[34, 33], [26, 27], [34, 35], [26, 25], [30, 35], [30, 25], [33, 33], [0, 5], [100, 95]]
        js = snap + balance + f"""
          console.log(JSON.stringify({{
            snapped: {moves}.map(([a, b]) => snapStep(a, b)),
            left: rebalance({{ easy: 34, medium: 33, hard: 33 }}, "easy", snapStep(34, 33)),
          }}));"""
        r = subprocess.run(["node", "-e", js], capture_output=True, text=True, timeout=20)
        self.assertEqual(r.returncode, 0, r.stderr)
        d = json.loads(r.stdout)
        self.assertEqual(d["snapped"], [30, 30, 35, 25, 35, 25, 33, 5, 95])
        self.assertEqual(d["left"], {"easy": 30, "medium": 35, "hard": 35})

    def test_it_draws_about_thirty_frames_and_none_on_another_tab(self):
        r = subprocess.run(["node", "-e", self.STUB, "--", FORGE.parent.parent / "studio" / "office.js"],
                           capture_output=True, text=True, timeout=20)
        self.assertEqual(r.returncode, 0, r.stderr)
        d = json.loads(r.stdout)
        self.assertLessEqual(abs(d["shown"] - 30), 2)
        self.assertEqual((d["away"], d["slept"], d["woke"]), (0, 0, 1))
        self.assertLessEqual(abs(d["again"] - 30), 2)

    BACKLOG = r"""
      const fs = require("fs"), vm = require("vm");
      let queued = [];
      const pen = new Proxy({}, { get: (o, k) => (k in o ? o[k] : () => ({})), set: (o, k, v) => (o[k] = v, true) });
      const canvas = { getContext: () => pen, clientWidth: 360 };
      const document = { createElement: () => ({ getContext: () => pen }) };
      const window = { matchMedia: () => ({ matches: false }) };
      vm.runInNewContext(fs.readFileSync(process.argv[1], "utf8"),
        { window, document, Sprites: pen, requestAnimationFrame: (f) => queued.push(f), Math, Date, console });
      const office = new window.Office(canvas);
      office.draw = () => {};
      const run = (t) => { const q = queued; queued = []; q.forEach((f) => f(t)); };
      const [a, b] = office.staff();
      let t = 1000;
      run(t);
      b.work = "type"; b.pose = "type";
      const queue = () => a.q.length;
      const away = (hide) => {
        if (hide) canvas.clientWidth = 0; else queued = [];   // a hidden browser tab: no frames at all
        for (let i = 0; i < 20; i++) office.deliver(a, b.desk);
        const held = queue();
        office.update(0.5);                                   // safe with no canvas size
        t += 60000;
        if (hide) { run(t); canvas.clientWidth = 360; office.wake(); office.wake(); } else queued = [(n) => office.frame(n)];
        const loops = queued.length;
        run(t);
        return { held, after: queue(), loops, left: queued.length, work: b.work, carrying: a.carrying, stack: b.desk.stack };
      };
      console.log(JSON.stringify([away(true), away(false)]));
    """

    def test_nothing_replays_late_after_a_hidden_tab(self):
        r = subprocess.run(["node", "-e", self.BACKLOG, "--", FORGE.parent.parent / "studio" / "office.js"],
                           capture_output=True, text=True, timeout=20)
        self.assertEqual(r.returncode, 0, r.stderr)
        for d in json.loads(r.stdout):
            self.assertGreater(d["held"], 60)
            self.assertLess(d["after"], 8)  # what is left is a bot starting something new
            self.assertEqual((d["loops"], d["left"]), (1, 1))  # one frame loop, not two
            self.assertEqual((d["work"], d["carrying"], d["stack"]), ("type", False, 5))


if __name__ == "__main__":
    unittest.main()
