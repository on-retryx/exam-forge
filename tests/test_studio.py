"""Studio server tests, on a temporary port and a temporary home."""
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

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
        cls.env = {**os.environ, "HOME": str(cls.home)}
        cls.env.pop("EXAM_FORGE_SETTINGS", None)
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
        self.assertEqual(self.post({}, token="wrong", path="/api/pick")[0], 403)
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


if __name__ == "__main__":
    unittest.main()
