"""Exam Forge Studio: a local dashboard and job form (standard library only).

Binds to 127.0.0.1 only. Checks the Host header on every request, and requires a
same-origin session token for every POST. The server never runs agents: a job from
the form goes to a Claude Code session that is waiting in `forge.py intake wait`.
The file browser shows only folders and PDFs, and only to the page holding the token.
"""
import hashlib
import json
import secrets
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "bin"))
import forge  # noqa: E402

STATIC = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
    "/i18n.js": ("i18n.js", "text/javascript; charset=utf-8"),
    "/font.js": ("font.js", "text/javascript; charset=utf-8"),
    "/pixel.ttf": ("pixel.ttf", "font/ttf"),
    "/sprites.js": ("sprites.js", "text/javascript; charset=utf-8"),
    "/office.js": ("office.js", "text/javascript; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
}
HIDDEN_FIELDS = {"transcript", "tool_use_id"}
# Changes with the code, so a Studio left running by an older version is never reused.
BUILD = hashlib.sha1((HERE / "server.py").read_bytes() + Path(forge.__file__).read_bytes()).hexdigest()[:10]
IDLE_SECONDS = 30 * 60
PAGE_SEEN_SECONDS = 6  # the page polls /api/intake every 2 seconds
PORT_RANGE = 30
MAX_UPLOAD_BYTES = 200 * 1024 * 1024
UPLOAD_CHUNK = 1024 * 1024


def run_id(path):
    return hashlib.sha1(str(path).encode()).hexdigest()[:10]


def known_runs():
    p = forge.config_dir() / "runs.json"
    try:
        runs = json.loads(p.read_text(encoding="utf-8")) if p.is_file() else []
    except ValueError:
        return []
    return [Path(r) for r in runs if (Path(r) / "plan.json").is_file()]


def read_events(run):
    p = run / "events.jsonl"
    if not p.is_file():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def run_summary(run):
    events = read_events(run)
    first = events[0] if events else {}
    done = sum(1 for e in events if e.get("type") == "render")
    return {
        "id": run_id(run), "name": run.name, "started": first.get("t"),
        "last": events[-1].get("t") if events else None,
        "pdfs": [p.get("stem") for p in first.get("pdfs", [])],
        "questions": sum(p.get("questions", 0) for p in first.get("pdfs", [])),
        "books": done, "preset": first.get("preset"),
    }


def open_ask():
    for run in known_runs()[:5]:
        ask = forge.pending_ask(run)
        if ask:
            return {**ask, "run": run_id(run)}
    return None


def public(event):
    return {k: v for k, v in event.items() if k not in HIDDEN_FIELDS}


class Handler(BaseHTTPRequestHandler):
    server_version = "ExamForgeStudio"

    def log_message(self, *args):
        pass

    def send_json(self, data, code=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def allowed_host(self):
        port = self.server.server_address[1]
        return self.headers.get("Host", "") in (f"127.0.0.1:{port}", f"localhost:{port}")

    def find_run(self, wanted):
        runs = known_runs()
        if not runs:
            return None
        if not wanted or wanted == "latest":
            return runs[0]
        return next((r for r in runs if run_id(r) == wanted), None)

    def books_of(self, run):
        return [e["out"] for e in read_events(run) if e.get("type") == "render" and e.get("out")]

    def send_book(self, run, n):
        books = self.books_of(run) if run else []
        path = Path(books[n]) if 0 <= n < len(books) else None
        if not path or path.suffix != ".pdf" or not path.is_file():
            return self.send_json({"error": "not found"}, 404)
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "application/pdf")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    def settings_payload(self):
        values = forge.load_settings()
        return {"schema": forge.load_schema(), "values": values, "team": forge.resolve_team(values)}

    def do_GET(self):
        self.server.touch()
        if not self.allowed_host():
            return self.send_json({"error": "forbidden host"}, 403)
        url = urlparse(self.path)
        q = parse_qs(url.query)
        wanted = q.get("run", [""])[0]
        if url.path in STATIC:
            name, ctype = STATIC[url.path]
            data = (HERE / name).read_bytes()
            if name == "index.html":
                data = data.replace(b"{{TOKEN}}", self.server.token.encode())
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Security-Policy",
                             "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(data)
        elif url.path == "/api/ping":
            self.send_json({"app": "exam-forge", "build": BUILD,
                            "watching": time.time() - self.server.page_seen < PAGE_SEEN_SECONDS})
        elif url.path == "/api/runs":
            self.send_json({"runs": [run_summary(r) for r in known_runs()]})
        elif url.path == "/api/events":
            run = self.find_run(wanted)
            if not run:
                return self.send_json({"run": None, "events": [], "total": 0})
            since = int(q.get("since", ["0"])[0] or 0)
            events = read_events(run)
            self.send_json({"run": run_id(run), "events": [public(e) for e in events[since:]],
                            "total": len(events)})
        elif url.path == "/api/cost":
            run = self.find_run(wanted)
            try:
                cost = forge.run_cost(run, forge.load_settings()["pricing"]) if run else None
            except forge.SettingsError:
                cost = None
            self.send_json({"cost": cost})
        elif url.path == "/api/settings":
            try:
                self.send_json(self.settings_payload())
            except forge.SettingsError as e:
                self.send_json({"error": str(e)}, 500)
        elif url.path == "/api/doctor":
            self.send_json(forge.doctor())
        elif url.path == "/api/intake":
            self.server.page_seen = time.time()
            self.send_json({"session": forge.session_waiting(), "request": forge.intake_request(),
                            "ask": open_ask()})
        elif url.path == "/book":
            n = q.get("n", ["0"])[0]
            self.send_book(self.find_run(wanted), int(n) if n.isdigit() else -1)
        else:
            self.send_json({"error": "not found"}, 404)

    def do_POST(self):
        self.server.touch()
        if not self.allowed_host():
            return self.send_json({"error": "forbidden host"}, 403)
        port = self.server.server_address[1]
        origin = self.headers.get("Origin", "")
        if origin not in (f"http://127.0.0.1:{port}", f"http://localhost:{port}"):
            return self.send_json({"error": "forbidden origin"}, 403)
        if not secrets.compare_digest(self.headers.get("X-Forge-Token", ""), self.server.token):
            return self.send_json({"error": "bad token"}, 403)
        url = urlparse(self.path)
        if url.path == "/api/upload":
            return self.post_upload(parse_qs(url.query))
        route = {"/api/settings": self.post_setting, "/api/jobs": self.post_job, "/api/cancel": self.post_cancel,
                 "/api/answer": self.post_answer, "/api/browse": self.post_browse,
                 "/api/sources": self.post_sources, "/api/install": self.post_install}.get(url.path)
        if not route:
            return self.send_json({"error": "not found"}, 404)
        try:
            length = int(self.headers.get("Content-Length", "0"))
            route(json.loads(self.rfile.read(min(length, 65536)).decode("utf-8") or "{}"))
        except (forge.SettingsError, KeyError, ValueError) as e:
            self.send_json({"ok": False, "error": str(e)}, 400)

    def post_setting(self, body):
        key = body["key"]
        if key not in {s["key"] for s in forge.load_schema()["settings"]}:
            raise forge.SettingsError(f"unknown setting '{key}'")
        forge.write_user_setting(key, None if body.get("reset") else body["value"])
        self.send_json({"ok": True, **self.settings_payload()})

    def post_job(self, body):
        if not forge.session_waiting():
            return self.send_json({"ok": False, "error": "no_session"}, 409)
        forge.submit_job(forge.validate_job(body, forge.load_settings()))
        self.send_json({"ok": True})

    def post_cancel(self, body):
        if not forge.session_waiting():
            return self.send_json({"ok": False, "error": "no_session"}, 409)
        forge.cancel_job()
        self.send_json({"ok": True})

    def post_answer(self, body):
        run = self.find_run(body["run"])
        if not run:
            raise forge.SettingsError("unknown job")
        forge.answer_ask(run, body["id"], body["value"])
        self.send_json({"ok": True})

    def post_browse(self, body):
        self.send_json({"ok": True, **forge.list_folder(body.get("path")), "places": forge.places()})

    def post_install(self, body):
        """One entry of forge's fixed table, chosen by a click in the page."""
        self.send_json({"ok": True, "detail": forge.install_requirement(body.get("id"))})

    def post_sources(self, body):
        self.send_json({"ok": True, "pdfs": forge.expand_sources(body["paths"])})

    def post_upload(self, query):
        """Save one dropped PDF. The body is the raw file, not JSON."""
        self.close_connection = True
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= MAX_UPLOAD_BYTES:
                return self.send_json({"ok": False, "error": "too_large"}, 413)
            target = forge.upload_target(query.get("batch", [""])[0], query.get("name", [""])[0])
        except (forge.SettingsError, ValueError) as e:
            return self.send_json({"ok": False, "error": str(e)}, 400)
        tmp = target.with_name(f".{target.name}.part")
        try:
            with open(tmp, "wb") as f:
                while length > 0:
                    chunk = self.rfile.read(min(length, UPLOAD_CHUNK))
                    if not chunk:
                        raise OSError("upload stopped")
                    f.write(chunk)
                    length -= len(chunk)
            with open(tmp, "rb") as f:
                if f.read(5) != b"%PDF-":
                    raise forge.SettingsError(f"not a PDF: {target.name}")
            tmp.replace(target)
        except (forge.SettingsError, OSError) as e:
            return self.send_json({"ok": False, "error": str(e)}, 400)
        finally:
            tmp.unlink(missing_ok=True)
            try:
                target.parent.rmdir()  # only an empty batch folder goes
            except OSError:
                pass
        self.send_json({"ok": True, "path": str(target)})


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, addr):
        super().__init__(addr, Handler)
        self.token = secrets.token_urlsafe(24)
        self.last_seen = time.time()
        self.page_seen = 0

    def touch(self):
        self.last_seen = time.time()


def is_studio(port):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/ping", timeout=1) as r:
            d = json.load(r)
            return d.get("app") == "exam-forge" and d.get("build") == BUILD
    except (urllib.error.URLError, OSError, ValueError):
        return False


def find_studio(start):
    return next((p for p in range(start, start + PORT_RANGE) if is_studio(p)), 0)


def free_port(start):
    for port in range(start, start + PORT_RANGE):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    return 0


def main(port=8765, open_browser=True):
    running = find_studio(port)
    if running:
        url = f"http://127.0.0.1:{running}"
        print(f"Exam Forge Studio is already running: {url}", flush=True)
        if open_browser:
            forge.open_window(url)
        return
    server = Server(("127.0.0.1", free_port(port)))
    url = f"http://127.0.0.1:{server.server_address[1]}"
    print(f"Exam Forge Studio: {url}", flush=True)

    def watch_idle():
        while True:
            time.sleep(30)
            if time.time() - server.last_seen > IDLE_SECONDS and not forge.session_waiting() and not open_ask():
                server.shutdown()
                return

    threading.Thread(target=watch_idle, daemon=True).start()
    if open_browser:
        threading.Timer(0.3, forge.open_window, [url]).start()
    server.serve_forever()
