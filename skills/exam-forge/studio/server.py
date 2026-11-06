"""Exam Forge Studio: a local dashboard and job form (standard library only).

Binds to 127.0.0.1 only. Checks the Host header on every request, and requires a
same-origin session token for every POST. The server never runs agents: a job from
the form goes to a Claude Code session that is waiting in `forge.py intake wait`.
The file browser shows only folders and PDFs, and only to the page holding the token.

The newest Studio writes its address to studio.json, and an older one that finds it there
stops by itself once nothing needs it. A Studio only ever stops itself, on its own checks.
"""
import hashlib
import http.client
import json
import os
import secrets
import selectors
import socket
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "bin"))
import app_icon  # noqa: E402
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
RETIRE_SECONDS = 60  # a replaced Studio goes this long after its last request, the time a window takes to open
JOB_SECONDS = 15 * 60  # a run this quiet is no longer active, as ACTIVE_MS in app.js
WATCH_SECONDS = 30
PAGE_SEEN_SECONDS = 6  # the page polls /api/intake every 2 seconds
HEARTBEAT_SECONDS = 20  # keeps the page's open connection from timing out
MAX_HELD_PAGES = 3  # Chrome allows 6 connections per host; more held pages would freeze every page. The rest count by polling
PORT_RANGE = 30
MAX_UPLOAD_BYTES = 200 * 1024 * 1024
UPLOAD_CHUNK = 1024 * 1024


def run_id(path):
    return hashlib.sha1(str(path).encode()).hexdigest()[:10]


def known_runs():
    p = forge.config_dir() / "runs.json"
    try:
        runs = json.loads(p.read_text(encoding="utf-8")) if p.is_file() else []
        return [Path(r) for r in runs if isinstance(r, str) and (Path(r) / "plan.json").is_file()]
    except (OSError, TypeError, ValueError):
        return []


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


def job_in_progress():
    if open_ask():
        return True
    for run in known_runs()[:5]:
        try:
            if time.time() - (run / "events.jsonl").stat().st_mtime < JOB_SECONDS:
                return True
        except OSError:
            continue
    return False


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

    def hold_page(self):
        """Held open by the page until its window closes. Timers of a hidden or minimized
        page can stop for minutes, so polling alone cannot prove that nobody is there."""
        if not self.server.holds.acquire(blocking=False):
            self.send_response(204)  # EventSource does not retry this; the page still polls /api/intake
            self.end_headers()
            return
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.server.pages.add(self)
            with selectors.DefaultSelector() as ready:
                ready.register(self.connection, selectors.EVENT_READ)
                while True:
                    if not ready.select(HEARTBEAT_SECONDS):
                        self.wfile.write(b": here\n\n")
                    elif not self.connection.recv(1):
                        return
        except OSError:
            pass
        finally:
            self.server.pages.discard(self)
            self.server.holds.release()
            self.server.touch()

    def do_GET(self):
        if not self.allowed_host():
            return self.send_json({"error": "forbidden host"}, 403)
        url = urlparse(self.path)
        q = parse_qs(url.query)
        wanted = q.get("run", [""])[0]
        # Every new Studio pings all the others: only a caller that can use this one keeps it alive.
        if url.path != "/api/ping" or q.get("build") == [BUILD]:
            self.server.touch()
        if url.path == "/icon.png":
            data = app_icon.png(128)
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "max-age=86400")
            self.end_headers()
            self.wfile.write(data)
        elif url.path in STATIC:
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
            self.send_json({"app": "exam-forge", "build": BUILD, "pid": os.getpid(),
                            "watching": self.server.page_open()})
        elif url.path == "/api/presence":
            if not secrets.compare_digest(q.get("token", [""])[0].encode(), self.server.token.encode()):
                return self.send_json({"error": "bad token"}, 403)
            self.hold_page()
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
            try:
                self.send_json(forge.doctor())
            except forge.SettingsError as e:
                self.send_json({"error": str(e)}, 500)
        elif url.path == "/api/intake":
            self.server.page_seen = time.monotonic()
            try:
                defaults = forge.default_team(forge.load_settings())
            except forge.SettingsError:
                defaults = None  # the page keeps its choices, and Settings and a job show the reason
            self.send_json({"session": forge.session_waiting(), "request": forge.intake_request(),
                            "ask": open_ask(), "defaults": defaults})
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
                 "/api/sources": self.post_sources, "/api/install": self.post_install,
                 "/api/estimate": self.post_estimate}.get(url.path)
        if not route:
            return self.send_json({"error": "not found"}, 404)
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length > 65536:  # never wait for a body that will not arrive
                raise forge.SettingsError("the request is too large")
            body = json.loads(self.rfile.read(max(0, length)).decode("utf-8") or "{}")
            if not isinstance(body, dict):
                raise forge.SettingsError("the request body must be a JSON object")
            route(body)
        except (forge.SettingsError, KeyError, ValueError, TypeError, AttributeError) as e:
            self.send_json({"ok": False, "error": str(e)}, 400)
        except OSError as e:
            self.send_json({"ok": False, "error": str(e)}, 500)

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

    def post_estimate(self, body):
        self.send_json(forge.plan_estimate(body, forge.load_settings()))

    def post_sources(self, body):
        skipped = []
        self.send_json({"ok": True, "pdfs": forge.expand_sources(body["paths"], skipped), "skipped": skipped})

    def post_upload(self, query):
        """Save one dropped PDF. The body is the raw file, not JSON."""
        self.close_connection = True
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= MAX_UPLOAD_BYTES:
                return self.send_json({"ok": False, "error": "too_large"}, 413)
            target = forge.upload_target(query.get("batch", [""])[0], query.get("name", [""])[0])
        except (forge.SettingsError, OSError, ValueError) as e:
            return self.send_json({"ok": False, "error": str(e)}, 400)
        tmp = target.with_name(f".{secrets.token_hex(8)}.part")
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
            forge.remove_quiet(target)  # the empty file that held its name
            return self.send_json({"ok": False, "error": str(e)}, 400)
        finally:
            forge.remove_quiet(tmp)
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
        self.last_seen = time.monotonic()  # on a Mac this clock stops while the computer sleeps
        self.page_seen = -PAGE_SEEN_SECONDS
        self.pages = set()
        self.holds = threading.Semaphore(MAX_HELD_PAGES)

    def touch(self):
        self.last_seen = time.monotonic()

    def page_open(self):
        return bool(self.pages) or time.monotonic() - self.page_seen < PAGE_SEEN_SECONDS

    def replaced(self):
        """True when studio.json names another Studio and that one answers."""
        mark = read_mark()
        return mark.get("pid") not in (None, os.getpid()) and ping(mark["port"]).get("pid") == mark["pid"]

    def should_stop(self):
        """Never with a page open or a job in progress. A waiting session is not a job: it
        talks through files, so while it waits the newest Studio is enough."""
        quiet = time.monotonic() - self.last_seen
        if quiet < RETIRE_SECONDS or self.page_open() or job_in_progress():
            return False
        return (quiet > IDLE_SECONDS and not forge.session_waiting()) or self.replaced()

    def watch(self):
        """Stops the Studio when it should. A check that fails is tried again on the next round."""
        while True:
            time.sleep(WATCH_SECONDS)
            try:
                stop = self.should_stop()
            except Exception:
                continue
            if stop:
                self.shutdown()
                return


def mark_file():
    return forge.config_dir() / "studio.json"


def read_mark():
    """studio.json, or {} unless it holds a whole port and pid."""
    mark = forge.read_quiet(mark_file())
    good = isinstance(mark, dict) and type(mark.get("port")) is int and type(mark.get("pid")) is int
    return mark if good and 0 < mark["port"] < 65536 else {}


def ping(port):
    try:
        with forge.LOCAL.open(f"http://127.0.0.1:{port}/api/ping?build={BUILD}", timeout=1) as r:
            d = json.load(r)
            return d if isinstance(d, dict) and d.get("app") == "exam-forge" else {}
    except (http.client.HTTPException, OSError, ValueError):
        return {}


def is_studio(port):
    return ping(port).get("build") == BUILD


def find_studio(start):
    """The newest Studio first, so that a running one is found without pinging the rest. The
    rest go together: a port that accepts and never answers costs a whole ping timeout, not a
    connect timeout, so one after the other a range of those took half a minute."""
    ports = range(start, start + PORT_RANGE)
    newest = read_mark().get("port")
    if newest in ports and is_studio(newest):
        return newest
    with ThreadPoolExecutor(PORT_RANGE) as pool:
        return next((p for p, found in zip(ports, list(pool.map(is_studio, ports))) if found), 0)


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
        if open_browser and not forge.page_open(url):
            forge.open_window(url)
        return
    free = free_port(port)
    if not free:  # a port outside the range would never be found again, and each start would add a Studio
        forge.fail(f"no free port from {port} to {port + PORT_RANGE - 1}; set studio_port to another one")
    server = Server(("127.0.0.1", free))
    url = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        forge.write_atomic(mark_file(), {"pid": os.getpid(), "port": server.server_address[1], "build": BUILD})
    except OSError:
        pass  # only a shortcut: find_studio also scans the range
    print(f"Exam Forge Studio: {url}", flush=True)
    threading.Thread(target=server.watch, daemon=True).start()
    if open_browser:
        threading.Timer(0.3, forge.open_window, [url]).start()
    server.serve_forever()
