#!/usr/bin/env python3
"""exam-forge helper (Python standard library only).

Commands:
  settings                              Print effective settings as JSON
  config list|get|set|reset             Read or change user settings
  team                                  Print the resolved team and models
  pages <pdf>                           Page count
  render-pages <pdf> <3-12> --out DIR   Rasterize pages to PNG (pymupdf or pdftoppm)
  marks <pdf> <3-12> [--out F]          Highlights, underlines and strikethroughs (needs pymupdf)
  marks-coverage <chunk.json> <marks.json> [--fail-out F]  Marks with a question; fails on struck text
  plan <pdf>... [--run DIR]             Question counts, difficulty split, chunks
       [--mix 0.2,0.5,0.3]              difficulty shares for this job
       [--team editor=sonnet,...] [--effort editor=high,...] [--budget usd=5]
       [--pages file.pdf=N ...]         give the page count when it cannot be read
  check <chunk.json> [--format F] [--expected easy=2,medium=5,hard=3]
       [--mcq-options 4|5] [--min N]    --min allows a justified thin-page reduction
       [--marks marks.json]             also fail on a question that rests on struck text
  screen <chunk.json> <screen.json>     Record screener results
  audit-sample <chunk.json> [--rate R]  Pick questions for the chief editor
  source-pages <chunk.json> --ids a,b   Pages (with one page of margin) for those ids
  approve <chunk.json> <review.json>    Mark reviewer PASS results as verified
       [--audit]                        apply chief editor results instead
  replace <chunk.json> <fix.json>       Swap questions that have the same id
  merge <pdf_dir>... --out book.json --title T --format F
       [--part file.pdf=Name ...] [--language en] [--titles logical|filename]
  render <book.json> <out.pdf> [--answers book_end|inline|section_end] [--html F] [--no-contents]
  event <run> <type> [key=value ...]    Append an event by hand (hook fallback)
  cost <run>                            Tokens and estimated cost from transcripts
  cost <run> --record                   Also learn from this run for later estimates
  cost <run> --budget-check             Exit 6 once, when spending passes 120% of the target
  doctor [--json] [--install ID]        Check what a job needs; install one item
  models [--probe]                      Which model families this account can use
  studio [--port N] [--no-open]         Start the local office view
  studio-url                            Start the Studio if needed and print its address
  intake open [--pdf P ...] [--notes T]  Open the job form in the Studio
  intake wait [--timeout S]             Wait for a job from the form (exit 4 timeout, 5 cancelled)
  ask <run> --id I --text T --option v=Label ...  Ask in the Studio; prints the value

Settings: defaults in ../settings.json, then ~/.config/exam-forge/settings.json,
then the file named by EXAM_FORGE_SETTINGS. Only the keys present are overridden.
"""
import argparse
import datetime as dt
import glob
import html
import importlib
from itertools import product
import hashlib
import json
import math
import os
import plistlib
import random
import re
import shlex
import shutil
import string
import subprocess
import sys
import tempfile
import threading
import time
import unicodedata
import urllib.request
import webbrowser
from pathlib import Path

import app_icon  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = ROOT / "templates"
LOCAL = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # a proxy cannot reach this computer's 127.0.0.1
SETTINGS_LOCK = threading.Lock()  # the Studio writes settings from many threads


class SettingsError(Exception):
    pass


# ---------- settings ----------
def config_dir():
    return Path.home() / ".config" / "exam-forge"


def user_settings_path():
    return config_dir() / "settings.json"


def _deep_merge(base, top):
    for k, v in top.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v
    return base


def load_schema():
    return json.loads((ROOT / "settings.schema.json").read_text(encoding="utf-8"))


def dotted_get(d, key):
    for part in key.split("."):
        if not isinstance(d, dict) or part not in d:
            return None
        d = d[part]
    return d


def dotted_set(d, key, value):
    parts = key.split(".")
    for part in parts[:-1]:
        d = d.setdefault(part, {})
    d[parts[-1]] = value


def validate_value(spec, v):
    """Return an error string, or None when v fits spec."""
    t, key = spec["type"], spec["key"]
    if t == "enum":
        if v not in spec["values"]:
            return f"{key} must be one of {spec['values']}"
    elif t == "bool":
        if not isinstance(v, bool):
            return f"{key} must be true or false"
    elif t in ("int", "float"):
        if isinstance(v, bool) or not isinstance(v, (int, float)) or (t == "int" and int(v) != v):
            return f"{key} must be a number" if t == "float" else f"{key} must be a whole number"
        if v < spec.get("min", -math.inf) or v > spec.get("max", math.inf):
            return f"{key} must be between {spec.get('min')} and {spec.get('max')}"
    elif t == "string":
        if not isinstance(v, str):
            return f"{key} must be text"
    elif t == "int_list":
        if not isinstance(v, list) or not v or not all(isinstance(x, int) and x > 0 for x in v):
            return f"{key} must be a list of positive whole numbers"
    elif t == "mix":
        if not isinstance(v, dict) or set(v) != {"easy", "medium", "hard"}:
            return f"{key} must have easy, medium and hard"
        if not all(isinstance(x, (int, float)) and x >= 0 for x in v.values()) or abs(sum(v.values()) - 1) > 1e-6:
            return f"{key} shares must sum to 1"
    elif t == "json":
        if not isinstance(v, dict):
            return f"{key} must be a JSON object"
    return None


def validate_settings(s):
    errors = []
    for spec in load_schema()["settings"]:
        e = validate_value(spec, dotted_get(s, spec["key"]))
        if e:
            errors.append(e)
    return errors


def read_json_file(p):
    try:
        data = json.loads(Path(p).read_text(encoding="utf-8"))
    except Exception as e:
        raise SettingsError(f"could not read {p}: {e}")
    if not isinstance(data, dict):
        raise SettingsError(f"could not read {p}: it must hold a JSON object")
    return data


def load_settings(overlay=None):
    s = json.loads((ROOT / "settings.json").read_text(encoding="utf-8"))
    layers = [user_settings_path()]
    if os.environ.get("EXAM_FORGE_SETTINGS"):
        layers.append(Path(os.environ["EXAM_FORGE_SETTINGS"]).expanduser())
    for p in layers:
        if p.is_file():
            _deep_merge(s, read_json_file(p))
    if overlay:
        _deep_merge(s, overlay)
    errors = validate_settings(s)
    if errors:
        raise SettingsError("; ".join(errors))
    return s


PRESETS = {
    "quality": {"editor": "sonnet", "screener": "haiku", "reviewer": "opus", "chief_editor": ""},
    "balanced": {"editor": "sonnet", "screener": "haiku", "reviewer": "sonnet", "chief_editor": "opus"},
    "economy": {"editor": "sonnet", "screener": "haiku", "reviewer": "sonnet", "chief_editor": ""},
}


def resolve_team(s):
    team = dict(PRESETS[s["quality_preset"]])
    for role, model in s["models"].items():
        if model:
            team[role] = model
    if not s["screener_enabled"]:
        team["screener"] = ""
    return team


def lock_settings():
    """One writer across processes. O_EXCL behaves the same on every platform, and a lock
    left by a command that died goes once it is no longer fresh."""
    lock = config_dir() / "settings.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    for _ in range(LOCK_TRIES):
        try:
            os.close(os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
            return lock
        except FileExistsError:
            if not is_fresh(lock):
                remove_quiet(lock)
            time.sleep(0.02)
    raise SettingsError("another command is saving settings; try again")


def update_user_settings(p, key, value):
    user = read_json_file(p) if p.is_file() else {}
    if value is None:
        parts = key.split(".")
        d = user
        for part in parts[:-1]:
            d = d.get(part, {})
        d.pop(parts[-1], None)
        if len(parts) == 2 and not user.get(parts[0]):
            user.pop(parts[0], None)
    else:
        dotted_set(user, key, value)
    defaults = json.loads((ROOT / "settings.json").read_text(encoding="utf-8"))
    errors = validate_settings(_deep_merge(defaults, json.loads(json.dumps(user))))
    if errors:
        raise SettingsError("; ".join(errors))
    write_atomic(p.resolve(), user, indent=2)


def write_user_setting(key, value):
    """Validate and persist one setting. value=None removes the override."""
    p = user_settings_path()
    with SETTINGS_LOCK:
        try:
            lock = lock_settings()
            try:
                update_user_settings(p, key, value)
            finally:
                remove_quiet(lock)
        except OSError as e:
            raise SettingsError(f"cannot save settings in {p.parent}: {e.strerror}")


def parse_value(spec, raw):
    t = spec["type"]
    try:
        if t == "bool":
            if raw.lower() in ("true", "on", "yes", "1"):
                return True
            if raw.lower() in ("false", "off", "no", "0"):
                return False
            raise ValueError
        if t == "int":
            return int(raw)
        if t == "float":
            return float(raw)
        if t == "int_list":
            return [int(x) for x in raw.split(",")]
        if t == "mix":
            if raw.lstrip().startswith("{"):
                return json.loads(raw)
            a, b, c = (float(x) for x in raw.split(","))
            return {"easy": a, "medium": b, "hard": c}
        if t == "json":
            return json.loads(raw)
        if t == "enum" and all(isinstance(x, int) for x in spec["values"]):
            return int(raw)
    except ValueError:
        raise SettingsError(f"could not read '{raw}' for {spec['key']}")
    return raw


def cmd_config(a):
    specs = {x["key"]: x for x in load_schema()["settings"]}
    if a.action == "list":
        s = load_settings()
        if a.json:
            print(json.dumps({"schema": load_schema(), "values": s, "team": resolve_team(s)},
                             ensure_ascii=False, indent=2))
            return
        for key in specs:
            print(f"{key} = {json.dumps(dotted_get(s, key), ensure_ascii=False)}")
        return
    if a.action in ("get", "set") and not a.key:
        fail(f"config {a.action} needs a key")
    if a.key and a.key not in specs:
        fail(f"unknown setting '{a.key}'. Run: config list")
    if a.action == "get":
        print(json.dumps(dotted_get(load_settings(), a.key), ensure_ascii=False))
    elif a.action == "set":
        if a.value is None:
            fail("config set needs a value")
        write_user_setting(a.key, parse_value(specs[a.key], a.value))
        print(f"{a.key} = {json.dumps(dotted_get(load_settings(), a.key), ensure_ascii=False)}")
    elif a.action == "reset":
        if a.key:
            write_user_setting(a.key, None)
            print(f"{a.key} reset")
        else:
            p = user_settings_path()
            if p.is_file():
                try:
                    p.unlink()
                except OSError as e:
                    raise SettingsError(f"cannot save settings in {p.parent}: {e.strerror}")
            print("all settings reset")


# ---------- constants ----------
DIFFICULTIES = ["easy", "medium", "hard"]
FORMATS = {
    "mcq": {"mcq"},
    "open": {"open"},
    "mixed": {"open", "blank", "true_false"},
    "full-mix": {"open", "blank", "true_false", "mcq"},
}
TYPES = ["mcq", "open", "blank", "true_false"]
LETTERS = ["A", "B", "C", "D", "E"]
OPTION_SETS = {4: LETTERS[:4], 5: LETTERS}
SOURCE_REFERENCES = {
    "en": [
        r"\bpdf", r"\bslides?\b", r"\blecture notes?\b", r"\bhandout\b",
        r"according to the (text|passage|notes?|source|document|table|figure|lecture|author|chart)",
        r"\bin the (text|notes|document|slides?|lecture|passage|chapter|handout)\b",
        r"\bthis (section|chapter|document|text|passage|lecture)\b",
        r"\bpage \d", r"\bp\. ?\d",
        r"\b(given|stated|mentioned|shown|listed|described) (above|in the)\b",
        r"\b(value|figure|number|amount) (given|stated|listed) for\b",
        r"\bthe (above|preceding) (text|table|figure|section)\b",
    ],
    "tr": [
        r"slayt", r"sunum(da|a|un)", r"\bnotlar(da|a|ın)", r"ders not",
        r"metne göre", r"metinde", r"kaynağa göre", r"kaynakta",
        r"yukarıdaki (metin|kaynak|bölüm)", r"bu bölümde", r"bölümüne göre", r"dokümanda",
        r"belgede", r"sayfa \d", r"\bs\. ?\d", r"yazara göre", r"tabloya göre", r"şekle göre",
        r"görselde", r"için verilen", r"yukarıda verilen", r"için belirtilen", r"yukarıda belirtilen",
    ],
}
BROWSER_CANDIDATES = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
    "google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "microsoft-edge", "brave-browser",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe",
]
LABELS = {
    "en": {
        "lang": "en", "book": "Question Book", "difficulty_mix": "Difficulty mix", "source": "Source",
        "contents": "Contents", "section": "Section", "part": "Part", "questions": "questions",
        "sections": "sections", "questions_one": "question", "sections_one": "section", "question": "Question", "answer_key": "Answer Key", "page": "p.",
        "easy": "Easy", "medium": "Medium", "hard": "Hard", "true": "True", "false": "False",
        "mcq": "Multiple choice", "open": "Open-ended", "blank": "Fill in the blank",
        "true_false": "True / False", "fmt_mcq": "Multiple choice", "fmt_open": "Open-ended",
        "fmt_mixed": "Mixed", "fmt_full-mix": "Full mix",
        "guide_book_end": "Answers are at the end of the book, with a short explanation and the source of each one.",
        "guide_inline": "Each answer sits directly under its question, with a short explanation and its source.",
        "guide_section_end": "Each section ends with its answer key, with a short explanation and the source of each answer.",
        "months": ["January", "February", "March", "April", "May", "June", "July", "August",
                   "September", "October", "November", "December"],
        "date": "{month} {day}, {year}",
    },
    "tr": {
        "lang": "tr", "book": "Soru Kitabı", "difficulty_mix": "Zorluk dağılımı", "source": "Kaynak",
        "contents": "İçindekiler", "section": "Bölüm", "part": "Kısım", "questions": "soru",
        "sections": "bölüm", "question": "Soru", "answer_key": "Cevap Anahtarı", "page": "s.",
        "easy": "Kolay", "medium": "Orta", "hard": "Zor", "true": "Doğru", "false": "Yanlış",
        "mcq": "Çoktan seçmeli", "open": "Açık uçlu", "blank": "Boşluk doldurma",
        "true_false": "Doğru / Yanlış", "fmt_mcq": "Çoktan seçmeli", "fmt_open": "Açık uçlu",
        "fmt_mixed": "Karma", "fmt_full-mix": "Tam karma",
        "guide_book_end": "Cevaplar kitabın sonundadır; her cevapta kısa açıklama ve kaynak yer alır.",
        "guide_inline": "Her cevap kendi sorusunun hemen altındadır; kısa açıklama ve kaynakla birlikte.",
        "guide_section_end": "Her bölüm kendi cevap anahtarıyla biter; her cevapta kısa açıklama ve kaynak yer alır.",
        "months": ["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz", "Ağustos",
                   "Eylül", "Ekim", "Kasım", "Aralık"],
        "date": "{day} {month} {year}",
    },
}
S = None  # effective settings, set in main()
UTF8 = {"encoding": "utf-8", "errors": "replace"}  # for child output; the default is the Windows ANSI code page


def fail(msg, code=2):
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(code)


def read_json(p):
    try:
        return json.loads(Path(p).expanduser().read_text(encoding="utf-8"))
    except Exception as e:
        fail(f"could not read {p}: {e}")


def write_json(p, data):
    Path(p).expanduser().write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------- events ----------
def now_iso():
    return dt.datetime.now().isoformat(timespec="seconds")


def find_run(path):
    p = Path(path).expanduser().resolve()
    for d in [p] + list(p.parents)[:4]:
        if (d / "plan.json").is_file():
            return d
    return None


def emit(run, type_, **data):
    if not run:
        return
    rec = {"t": now_iso(), "type": type_, **data}
    try:
        with open(Path(run) / "events.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError:
        pass  # the Studio is optional; never block book production


def file_context(path):
    """(run, pdf stem, chunk number) for a working file."""
    p = Path(path).expanduser().resolve()
    m = re.search(r"(\d+)", p.stem)
    return find_run(p), p.parent.name, int(m.group(1)) if m else None


def register_run(run):
    p = config_dir() / "runs.json"
    runs = read_quiet(p)
    runs = [str(run)] + [r for r in runs if r != str(run)] if isinstance(runs, list) else [str(run)]
    try:
        write_atomic(p, runs[:50])
    except OSError:
        pass  # the list only feeds the Studio; never block book production


def cmd_event(a):
    data = {}
    for kv in a.data or []:
        k, _, v = kv.partition("=")
        data[k] = int(v) if v.isdigit() else v
    emit(Path(a.run).expanduser(), a.type, **data)


# ---------- requirements ----------
# One entry per thing a job needs. To require something new, add an entry here:
# the skill, `doctor` and the Studio's start screen all read this table.
MIN_PYTHON = (3, 9)  # the oldest version the tests have run on; the code itself needs 3.8 (Path.unlink(missing_ok))


def venv_dir():
    return config_dir() / "venv"


def load_fitz():
    """pymupdf from this Python or from our own venv. None when neither has it."""
    for site in glob.glob(str(venv_dir() / "lib" / "python*" / "site-packages")) + [str(venv_dir() / "Lib" / "site-packages")]:
        if site not in sys.path and Path(site).is_dir():
            sys.path.append(site)
    for name in ("pymupdf", "fitz"):  # newer releases deprecate the old name
        try:
            return importlib.import_module(name)
        except Exception:  # a venv built by another Python raises more than ImportError
            continue
    return None


def check_python():
    version = ".".join(map(str, sys.version_info[:3]))
    return (sys.version_info >= MIN_PYTHON, version)


def check_pdf_tool():
    if load_fitz():
        return True, "pymupdf"
    return bool(shutil.which("pdftoppm")), "pdftoppm" if shutil.which("pdftoppm") else ""


def check_browser():
    b = find_browser()
    return bool(b), Path(b).stem if b else ""


def check_claude():
    c = shutil.which("claude")
    return bool(c), c or ""


def venv_python():
    return venv_dir() / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def install_pymupdf():
    """A private venv: it works on an externally managed Python and leaves the system one alone."""
    return [[sys.executable, "-m", "venv", "--clear", str(venv_dir())],
            [str(venv_python()), "-m", "pip", "install", "--quiet", "--disable-pip-version-check", "pymupdf"]]


REQUIREMENTS = [
    {"id": "python", "required": True, "check": check_python, "hint": f"Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]} or newer: https://www.python.org/downloads/"},
    {"id": "pdf_tool", "required": True, "check": check_pdf_tool, "install": install_pymupdf,
     "hint": "Run: forge.py doctor --install pdf_tool"},
    {"id": "browser", "required": True, "check": check_browser,
     "install": {"darwin": [["brew", "install", "--cask", "google-chrome"]],
                 "win32": [["winget", "install", "-e", "--id", "Google.Chrome",
                            "--accept-source-agreements", "--accept-package-agreements"]]},
     "hint": "Install Chrome, Edge or Chromium, or set EXAM_FORGE_BROWSER."},
    {"id": "claude", "required": False, "check": check_claude, "hint": "Claude Code CLI on PATH; used to see which models you can use."},
]


def install_steps(item):
    plan = item.get("install")
    if callable(plan):
        return plan()
    return (plan or {}).get(sys.platform)


def doctor():
    items = []
    for item in REQUIREMENTS:
        ok, detail = item["check"]()
        items.append({"id": item["id"], "ok": ok, "required": item["required"], "detail": detail,
                      "can_install": bool(install_steps(item)), "hint": item["hint"]})
    return {"ok": all(i["ok"] for i in items if i["required"]), "items": items}


def install_requirement(name):
    """Run the fixed steps of one table entry. Callers must have the user's approval."""
    item = next((x for x in REQUIREMENTS if x["id"] == name), None)
    steps = install_steps(item) if item else None
    if not steps:
        raise SettingsError(f"cannot install '{name}' from here")
    for step in steps:
        try:
            r = subprocess.run(step, capture_output=True, timeout=600, stdin=subprocess.DEVNULL, **UTF8)
        except (OSError, subprocess.TimeoutExpired) as e:
            raise SettingsError(f"{step[0]} failed: {e}")
        if r.returncode != 0:
            raise SettingsError(f"{Path(step[0]).name} failed: {(r.stderr or r.stdout)[-400:].strip()}")
    ok, detail = item["check"]()
    if not ok:
        raise SettingsError(f"{name} is still missing after the install")
    return detail


def cmd_doctor(a):
    if a.install:
        try:
            print(f"{a.install}: {install_requirement(a.install)}")
        except SettingsError as e:
            fail(str(e))
        return
    d = doctor()
    if a.json:
        print(json.dumps(d, ensure_ascii=False, indent=2))
        return
    for i in d["items"]:
        print(f"{'ok' if i['ok'] else 'MISSING'}  {i['id']}  {i['detail'] or i['hint']}")
    sys.exit(0 if d["ok"] else 1)


MAC_APP = """
ObjC.import('Cocoa');
ObjC.import('WebKit');
var win, view, closer, links;
function run() {
  var app = Application.currentApplication();
  app.includeStandardAdditions = true;
  var url;
  try { url = app.doShellScript(COMMAND); } catch (e) { app.displayAlert('Exam Forge', { message: String(e) }); $.NSApp.terminate(null); return; }
  ObjC.registerSubclass({
    name: 'ForgeCloser', protocols: ['NSWindowDelegate'],
    methods: { 'windowWillClose:': { types: ['void', ['id']], implementation: function () { $.NSApp.terminate(null); } } },
  });
  ObjC.registerSubclass({
    name: 'ForgeLinks', protocols: ['WKUIDelegate'],
    methods: {
      'webView:createWebViewWithConfiguration:forNavigationAction:windowFeatures:': {
        types: ['id', ['id', 'id', 'id', 'id']],
        implementation: function (web, configuration, action) { $.NSWorkspace.sharedWorkspace.openURL(action.request.URL); return $(); },
      },
    },
  });
  closer = $.ForgeCloser.alloc.init;
  links = $.ForgeLinks.alloc.init;
  win = $.NSWindow.alloc.initWithContentRectStyleMaskBackingDefer($.NSMakeRect(0, 0, 1280, 860), 15, 2, false);
  win.title = 'Exam Forge';
  win.releasedWhenClosed = false;
  win.delegate = closer;
  view = $.WKWebView.alloc.initWithFrameConfiguration($.NSMakeRect(0, 0, 1280, 860), $.WKWebViewConfiguration.alloc.init);
  view.autoresizingMask = 18;
  view.UIDelegate = links;
  win.contentView = view;
  view.loadRequest($.NSURLRequest.requestWithURL($.NSURL.URLWithString(url)));
  win.center;
  win.makeKeyAndOrderFront(null);
  $.NSApp.activateIgnoringOtherApps(true);
}
"""


def mac_app():
    """A real Mac app holding a web view, so the Dock shows our icon.

    A browser started in app mode always keeps the browser's own icon. osacompile comes
    with macOS, so this needs no Xcode. Clicked in the Dock, the app starts the Studio itself.
    """
    bundle = Path.home() / "Applications" / "Exam Forge.app"
    command = f"{shlex.quote(sys.executable)} {shlex.quote(str(Path(__file__).resolve()))} studio-url"
    source = MAC_APP.replace("COMMAND", json.dumps(command))
    stamp = bundle / "Contents" / "Resources" / "exam-forge.js"
    source += f"// icon {hashlib.sha256(app_icon.png(128)).hexdigest()}\n"  # a new icon rebuilds the app too
    if stamp.is_file() and stamp.read_text(encoding="utf-8") == source:
        return bundle
    with tempfile.TemporaryDirectory() as td:
        script, iconset = Path(td) / "app.js", Path(td) / "icon.iconset"
        script.write_text(source, encoding="utf-8")
        iconset.mkdir()
        for size in (16, 32, 128, 256, 512):
            (iconset / f"icon_{size}x{size}.png").write_bytes(app_icon.png(size))
            (iconset / f"icon_{size}x{size}@2x.png").write_bytes(app_icon.png(size * 2))
        bundle.parent.mkdir(exist_ok=True)
        shutil.rmtree(bundle, ignore_errors=True)
        subprocess.run(["osacompile", "-l", "JavaScript", "-s", "-o", str(bundle), str(script)], check=True, capture_output=True, timeout=60)
        subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(bundle / "Contents" / "Resources" / "applet.icns")],
                       check=True, capture_output=True, timeout=30)
    # osacompile also ships a catalog holding the default script icon, and macOS prefers it to the .icns.
    (bundle / "Contents" / "Resources" / "Assets.car").unlink(missing_ok=True)
    info = bundle / "Contents" / "Info.plist"
    plist = plistlib.loads(info.read_bytes())
    plist.pop("CFBundleIconName", None)
    plist.update(CFBundleIdentifier="co.retryx.examforge", CFBundleName="Exam Forge",
                 NSAppTransportSecurity={"NSAllowsLocalNetworking": True, "NSAllowsArbitraryLoads": True})
    info.write_bytes(plistlib.dumps(plist))
    stamp.write_text(source, encoding="utf-8")
    subprocess.run(["codesign", "-f", "-s", "-", "--deep", str(bundle)], check=True, capture_output=True, timeout=60)
    return bundle


def window_flags():
    return [f"--user-data-dir={config_dir() / 'profile'}", "--no-first-run", "--no-default-browser-check",
            "--window-size=1280,860"]


def open_window(url):
    """Open the Studio in a window of its own. A plain browser tab when that is not possible."""
    if not (S or load_settings())["studio_window"]:
        webbrowser.open(url)
        return
    if sys.platform == "darwin":
        try:
            subprocess.Popen(["open", "-a", str(mac_app())], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True)
            return
        except (OSError, subprocess.SubprocessError):
            pass  # an old macOS or a blocked tool: the browser window below still works
    browser = find_browser(printing=False)
    if browser:
        try:
            subprocess.Popen([browser, f"--app={url}", *window_flags()],
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             start_new_session=True)
            return
        except (OSError, subprocess.SubprocessError):
            pass
    webbrowser.open(url)


# ---------- pages ----------
def find_browser(printing=True):
    """The browser the user named, else a known one. Headless Brave never wrote the PDF
    in testing, so it is only used for the Studio window unless named on purpose."""
    settings = S or load_settings()
    named = [os.environ.get("EXAM_FORGE_BROWSER"), settings.get("browser")]
    known = [c for c in BROWSER_CANDIDATES if not (printing and "brave" in c.lower())]
    for c in named + known:
        if not c:
            continue
        p = Path(c).expanduser()
        if p.is_file():
            return str(p)
        w = shutil.which(c)
        if w:
            return w
    return None


def page_count(pdf: Path):
    if sys.platform == "darwin":
        try:
            out = subprocess.run(["mdls", "-raw", "-name", "kMDItemNumberOfPages", str(pdf)],
                                 capture_output=True, text=True, timeout=15).stdout.strip()
            if out.isdigit() and int(out) > 0:
                return int(out)
        except Exception:
            pass
    fitz = load_fitz()  # the scan below cannot see a page tree inside a compressed object stream
    if fitz:
        try:
            with fitz.open(str(pdf)) as doc:
                return doc.page_count
        except Exception:
            pass
    try:
        data = pdf.read_bytes()
        counts = [int(m) for m in re.findall(rb"/Type\s*/Pages\b[^>]*?/Count\s+(\d+)", data)]
        counts += [int(m) for m in re.findall(rb"/Count\s+(\d+)[^>]*?/Type\s*/Pages\b", data)]
        if counts:
            return max(counts)
        n = len(re.findall(rb"/Type\s*/Page\b", data))
        if n:
            return n
    except Exception:
        pass
    return None


def cmd_pages(a):
    n = page_count(Path(a.pdf).expanduser())
    if n is None:
        fail("could not read the page count; pass --pages file.pdf=N to plan")
    print(n)


def cmd_render_pages(a):
    """Rasterize a page range, the fallback when the Read tool cannot draw PDF pages."""
    pdf = Path(a.pdf).expanduser().resolve()
    m = re.fullmatch(r"(\d+)(?:-(\d+))?", a.range)
    if not m:
        fail("range must look like '3' or '3-12'")
    first, last = int(m.group(1)), int(m.group(2) or m.group(1))
    target = Path(a.out).expanduser()
    target.mkdir(parents=True, exist_ok=True)
    fitz = load_fitz()
    if fitz:
        doc = fitz.open(str(pdf))
        last = min(last, doc.page_count)
        for i in range(first, last + 1):
            path = target / f"{pdf.stem}-p{i:03d}.png"
            doc[i - 1].get_pixmap(dpi=a.dpi).save(str(path))
            print(path)
        return
    if shutil.which("pdftoppm"):
        r = subprocess.run(["pdftoppm", "-png", "-r", str(a.dpi), "-f", str(first), "-l", str(last),
                            str(pdf), str(target / f"{pdf.stem}-p")], capture_output=True, **UTF8)
        if r.returncode != 0:
            fail(f"pdftoppm failed: {r.stderr[-400:]}")
        for p in sorted(target.glob(f"{pdf.stem}-p*.png")):
            print(p)
        return
    print("ERROR: no page image tool found. Run: forge.py doctor --install pdf_tool", file=sys.stderr)
    sys.exit(4)


# ---------- marks ----------
# Highlight and underline mean important. Only a line through the text means out of scope.
ANNOTATION_MARKS = {"Highlight": "highlight", "Underline": "underline", "Squiggly": "underline",
                    "StrikeOut": "strikethrough"}
MARK_ROLES = {"highlight": "emphasis", "underline": "emphasis", "strikethrough": "excluded"}
# A drawn line, measured up from the baseline as a share of the font size: from UNDER_DEPTH below it to
# UNDER_TOP is an underline, from there to STRIKE_TOP runs through the letters, and above that it is neither.
UNDER_DEPTH, UNDER_TOP, STRIKE_TOP = 0.45, 0.12, 0.62
THIN_LINE, TALL_BAR = 0.35, 1.9  # bar thickness over font size: a pen line, and the tallest highlight
MIN_FIT = 0.6  # share of a bar's length that must lie on letters
PAGE_WIDE, PAGE_RULE, PAGE_MARGIN = 0.85, 0.4, 0.08  # of the page: ignored always, in a margin, the margin's depth
THIN_POST, THIN_RULE, ROW_GAP = 2.5, 4, 4  # points, points, font sizes between joined rows
IDENTITY = (1, 0, 0, 1, 0, 0)


def turn(m, x, y):
    return m[0] * x + m[2] * y + m[4], m[1] * x + m[3] * y + m[5]


def turn_box(m, box):
    (x0, y0), (x1, y1) = turn(m, box[0], box[1]), turn(m, box[2], box[3])
    return min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)


def page_lines(page, m):
    """Text lines with their real baseline, which is what a line's position is measured against."""
    lines = []
    for block in page.get_text("rawdict")["blocks"]:
        for line in block.get("lines", []):
            spans = [s for s in line["spans"] if s["chars"]]
            if not spans:
                continue
            main = max(spans, key=lambda s: len(s["chars"]))  # a superscript does not move the line
            lines.append({"upright": m[0] * line["dir"][0] + m[2] * line["dir"][1] > 0.99,
                          "baseline": turn(m, *main["origin"])[1], "size": main["size"],
                          "chars": [(turn_box(m, c["bbox"]), c["c"]) for s in spans for c in s["chars"]]})
    return lines


def tidy(text):
    return " ".join(unicodedata.normalize("NFKC", text).split())  # a PDF's "fi" is often one ligature character


def annotation_quads(annot, m):
    points = annot.vertices or []
    quads = [points[i:i + 4] for i in range(0, len(points) - 3, 4)]
    return [turn_box(m, (min(p[0] for p in q), min(p[1] for p in q), max(p[0] for p in q), max(p[1] for p in q)))
            for q in quads] or [turn_box(m, tuple(annot.rect))]


def annotation_marks(page, lines, m):
    """Text comes from the quads: a rectangle around a two-line mark also holds what was not marked."""
    marks = []
    for annot in page.annots() or []:
        kind = ANNOTATION_MARKS.get(annot.type[1])
        if not kind:
            continue
        quads = annotation_quads(annot, m)
        parts = ["".join(c for box, c in line["chars"]
                         if q[0] <= (box[0] + box[2]) / 2 <= q[2] and q[1] <= (box[1] + box[3]) / 2 <= q[3])
                 for q in quads for line in lines]
        marks.append({"kind": kind, "origin": "annotation", "text": tidy(" ".join(parts)),
                      "bbox": [min(q[0] for q in quads), min(q[1] for q in quads),
                               max(q[2] for q in quads), max(q[3] for q in quads)]})
    return marks


def drop_annotations(page):
    """The drawing list includes what annotations draw, and those were read already."""
    annot = page.first_annot
    while annot:
        annot = page.delete_annot(annot)


def is_marker(rgb, opacity):
    """Highlighter ink: coloured, and see-through or pale. Grey is table shading, dark is a label."""
    light = 0.3 * rgb[0] + 0.59 * rgb[1] + 0.11 * rgb[2]
    return max(rgb) - min(rgb) >= 0.08 and (opacity < 0.95 or (max(rgb) >= 0.8 and light >= 0.6))


def page_shapes(page, m):
    """Horizontal bars that may be marks, and the vertical rules that give a table away."""
    bars, posts = [], []

    def add(x0, y0, x1, y1, colour, opacity):
        rgb = tuple(colour) * 3 if len(colour) == 1 else tuple(colour)[:3]
        if x1 - x0 >= 2 and opacity > 0.02 and min(rgb) < 0.97:  # white on white is not a mark
            bars.append({"x0": x0, "y0": y0, "x1": x1, "y1": y1, "rgb": rgb, "opacity": opacity})

    def boxes(d):
        items = d["items"]
        if all(i[0] in ("re", "qu") for i in items):
            return [turn_box(m, tuple(i[1] if i[0] == "re" else i[1].rect)) for i in items]
        return [turn_box(m, tuple(d["rect"]))]  # a rounded or hand-built shape counts as its outline

    for d in page.get_drawings():
        if d["type"] in ("f", "fs") and d.get("fill"):
            opacity = 1 if d.get("fill_opacity") is None else d["fill_opacity"]
            for x0, y0, x1, y1 in boxes(d):
                if x1 - x0 <= THIN_POST and y1 - y0 > x1 - x0:
                    posts.append((x0, y0, y1))
                else:
                    add(x0, y0, x1, y1, d["fill"], opacity)
        if d["type"] in ("s", "fs") and d.get("color"):
            width = d.get("width") or 1
            opacity = 1 if d.get("stroke_opacity") is None else d["stroke_opacity"]
            for i in d["items"]:
                if i[0] == "l":
                    (ax, ay), (bx, by) = turn(m, *i[1]), turn(m, *i[2])
                    if abs(ay - by) <= 2 < abs(ax - bx):
                        mid = (ay + by) / 2
                        add(min(ax, bx), mid - width / 2, max(ax, bx), mid + width / 2, d["color"], opacity)
                    elif abs(ax - bx) <= 2 < abs(ay - by):
                        posts.append((ax, min(ay, by), max(ay, by)))
                elif i[0] in ("re", "qu"):
                    x0, y0, x1, y1 = turn_box(m, tuple(i[1] if i[0] == "re" else i[1].rect))
                    if y1 - y0 <= THIN_POST:
                        add(x0, y0, x1, y1, d["color"], opacity)
                    else:  # a frame: its sides show it is a cell, so its bottom edge is no underline
                        posts += [(x0, y0, y1), (x1, y0, y1)]
    return bars, posts


def bar_kind(bar, line):
    """The core test: how high a bar sits above the baseline, as a share of the font size."""
    thickness, size = bar["y1"] - bar["y0"], line["size"]
    marker = is_marker(bar["rgb"], bar["opacity"])
    if thickness <= THIN_LINE * size:
        rise = (line["baseline"] - (bar["y0"] + bar["y1"]) / 2) / size
        if rise < -UNDER_DEPTH or rise > STRIKE_TOP:
            return None
        if rise <= UNDER_TOP:
            return "underline"
        return "highlight" if marker else "strikethrough"  # marker ink never cancels anything
    if marker and thickness <= TALL_BAR * size and bar["y0"] <= line["baseline"] - 0.25 * size <= bar["y1"]:
        return "highlight"
    return None


def read_bar(bar, lines):
    hits = []
    for line in lines:
        kind = line["upright"] and bar_kind(bar, line)
        chars = [box for box, _ in line["chars"] if bar["x0"] <= (box[0] + box[2]) / 2 <= bar["x1"]] if kind else None
        if chars:
            hits.append((sum(box[2] - box[0] for box in chars), kind, line, chars))
    if not hits:
        return None
    _, kind, line, _ = max(hits, key=lambda h: h[0])
    row = [h for h in hits if h[1] == kind and abs(h[2]["baseline"] - line["baseline"]) <= 1]
    if sum(h[0] for h in row) < MIN_FIT * (bar["x1"] - bar["x0"]):
        return None  # far longer than its words: a rule, not a mark
    boxes = [box for h in row for box in h[3]]
    return {"kind": kind, "origin": "drawn", "baseline": line["baseline"], "size": line["size"],
            "x0": min(b[0] for b in boxes), "x1": max(b[2] for b in boxes),
            "top": min(b[1] for b in boxes), "bottom": max(b[3] for b in boxes)}


def join_on_rows(found):
    """Tools often draw one line per word."""
    joined = []
    for m in sorted(found, key=lambda m: (m["kind"], round(m["baseline"]), m["x0"])):
        last = joined[-1] if joined else None
        if last and last["kind"] == m["kind"] and abs(last["baseline"] - m["baseline"]) <= 1 \
                and m["x0"] - last["x1"] <= m["size"]:
            last.update(x1=max(last["x1"], m["x1"]), top=min(last["top"], m["top"]),
                        bottom=max(last["bottom"], m["bottom"]))
        else:
            joined.append(dict(m))
    return joined


def text_of(m, lines):
    return "".join(c for line in lines if abs(line["baseline"] - m["baseline"]) <= 1 for box, c in line["chars"]
                   if m["x0"] <= (box[0] + box[2]) / 2 <= m["x1"])


def join_across_rows(found, lines):
    """One sentence marked over two lines is one mark, so one question can cover it."""
    rows = {}
    for line in lines:
        if line["upright"]:
            boxes = [box for box, _ in line["chars"]]
            row = rows.setdefault(round(line["baseline"]), [math.inf, -math.inf])
            row[0], row[1] = min(row[0], *(b[0] for b in boxes)), max(row[1], *(b[2] for b in boxes))
    order = sorted(rows)
    chains = []
    for m in sorted(found, key=lambda m: (m["baseline"], m["x0"])):
        y = round(m["baseline"])
        near = order.index(y) if y in order else -1
        above = order[near - 1] if near > 0 else None
        for chain in chains:
            tail = chain[-1]
            if above is not None and tail["kind"] == m["kind"] and round(tail["baseline"]) == above \
                    and y - above <= ROW_GAP * m["size"] and rows[above][1] - tail["x1"] <= m["size"] \
                    and m["x0"] - rows[y][0] <= m["size"]:
                chain.append(m)
                break
        else:
            chains.append([m])
    return [{"kind": c[0]["kind"], "origin": "drawn", "text": tidy(" ".join(text_of(m, lines) for m in c)),
             "bbox": [min(m["x0"] for m in c), min(m["top"] for m in c),
                      max(m["x1"] for m in c), max(m["bottom"] for m in c)]} for c in chains]


def drawn_marks(page, lines, m, width, height):
    bars, posts = page_shapes(page, m)
    links = [turn_box(m, tuple(link["from"])) for link in page.get_links()]
    found = []
    for bar in bars:
        length, mid = bar["x1"] - bar["x0"], (bar["y0"] + bar["y1"]) / 2
        if length >= PAGE_WIDE * width:
            continue
        if bar["y1"] - bar["y0"] <= THIN_RULE:
            if length >= PAGE_RULE * width and not PAGE_MARGIN * height <= mid <= (1 - PAGE_MARGIN) * height:
                continue  # a header or footer rule
            if any(bar["x0"] - 2 <= x <= bar["x1"] + 2 and top - 1.5 <= mid <= bottom + 1.5 for x, top, bottom in posts):
                continue  # meets a vertical rule: a table
        mark = read_bar(bar, lines)
        if not mark:
            continue
        center = (mark["x0"] + mark["x1"]) / 2
        if mark["kind"] == "underline" and any(r[0] <= center <= r[2] and r[1] <= mark["baseline"] <= r[3] for r in links):
            continue  # how a hyperlink looks, nobody's emphasis
        found.append(mark)
    return join_across_rows(join_on_rows(found), lines)


def upright_chars(lines):
    return sum(len(line["chars"]) for line in lines if line["upright"])


def reading_frame(page):
    """A page turned by /Rotate reports text, annotations and drawings in ways that differ between pymupdf
    releases. So read it all unrotated, here in memory only, then turn it the one way the text reads upright.
    Returns the matrix and the page's size in that frame."""
    rotated, m, size = page.rotation, tuple(page.rotation_matrix), (page.rect.width, page.rect.height)
    page.set_rotation(0)
    if rotated and upright_chars(page_lines(page, IDENTITY)) <= upright_chars(page_lines(page, m)):
        return (m, *size)
    return (IDENTITY, page.rect.width, page.rect.height)


def page_marks(page, number):
    m, width, height = reading_frame(page)
    found = annotation_marks(page, page_lines(page, m), m)
    drop_annotations(page)
    found += drawn_marks(page, page_lines(page, m), m, width, height)
    found.sort(key=lambda m: (round((m["bbox"][1] + m["bbox"][3]) / 16), m["bbox"][0]))
    return [{"id": f"mark-p{number}-{n}", "page": number, "kind": f["kind"], "role": MARK_ROLES[f["kind"]],
             "origin": f["origin"], "text": f["text"], "bbox": [round(x, 1) for x in f["bbox"]]}
            for n, f in enumerate(found, 1)]


def is_scanned(page):
    return not page.get_text("text").strip() and bool(page.get_images())


def mark_counts(marks):
    counts = {"total": len(marks)}
    for field, names in (("role", ("emphasis", "excluded")), ("kind", tuple(MARK_ROLES)),
                         ("origin", ("annotation", "drawn"))):
        for name in names:
            counts[name] = sum(1 for m in marks if m[field] == name)
    return counts


def cmd_marks(a):
    """Reader's marks in a page range. Pages without a text layer are named for a model to look at."""
    fitz = load_fitz()
    if not fitz:
        print("ERROR: reading marks needs pymupdf. Run: forge.py doctor --install pdf_tool", file=sys.stderr)
        sys.exit(4)
    m = re.fullmatch(r"(\d+)(?:-(\d+))?", a.range)
    if not m or int(m.group(1)) < 1 or int(m.group(2) or m.group(1)) < int(m.group(1)):
        fail("range must look like '3' or '3-12'")
    first, last = int(m.group(1)), int(m.group(2) or m.group(1))
    pdf = Path(a.pdf).expanduser()
    try:
        doc = fitz.open(str(pdf))
    except Exception as e:
        fail(f"cannot open {pdf}: {e}")
    if doc.needs_pass:
        fail(f"{pdf} is protected by a password")
    marks, scanned = [], []
    for number in range(first, min(last, doc.page_count) + 1):
        page = doc[number - 1]
        if is_scanned(page):
            scanned.append(number)
        marks += page_marks(page, number)
    result = {"pdf": pdf.name, "marks": marks, "counts": mark_counts(marks), "scanned_pages": scanned}
    if a.out:
        out = Path(a.out).expanduser()
        out.parent.mkdir(parents=True, exist_ok=True)
        write_json(out, result)
        print(json.dumps({"out": str(out), "counts": result["counts"], "scanned_pages": scanned}, ensure_ascii=False))
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))


# ---------- plan ----------
def split_by_difficulty(total):
    mix = S["difficulty_mix"]
    d = {z: math.floor(total * mix[z]) for z in DIFFICULTIES}
    d["medium"] += total - sum(d.values())
    return d


def divide(n, weights):
    """Divide n by weights with the largest remainder method."""
    t = sum(weights)
    raw = [n * w / t for w in weights]
    base = [math.floor(x) for x in raw]
    order = sorted(range(len(raw)), key=lambda i: raw[i] - base[i], reverse=True)
    for i in order[:n - sum(base)]:
        base[i] += 1
    return base


def chunk_ranges(pages):
    if pages <= S["split_threshold"]:
        return [(1, pages)]
    n = math.ceil(pages / S["chunk_pages"])
    out, start = [], 1
    for size in divide(pages, [1] * n):
        out.append((start, start + size - 1))
        start += size
    return out


def plan_pdf(pdf: Path, pages: int):
    short = pages < S["short_pdf_pages"]
    options = (list(S["short_pdf_question_options"]) if short
               else [max(1, round(pages * S["questions_per_page"]))])
    ranges = chunk_ranges(pages)
    weights = [b - a + 1 for a, b in ranges]
    chunks = [{"chunk": i, "page_start": a, "page_end": b, "split_options": {}}
              for i, (a, b) in enumerate(ranges, 1)]
    for total in options:
        d = split_by_difficulty(total)
        per = {z: divide(d[z], weights) for z in DIFFICULTIES}
        for i, c in enumerate(chunks):
            c["split_options"][str(total)] = {z: per[z][i] for z in DIFFICULTIES}
    for c in chunks:
        smallest = min(sum(d.values()) for d in c["split_options"].values())
        c["min_questions"] = max(1, math.ceil(smallest * S["thin_page_floor"]))
    return {
        "pdf": str(pdf), "name": pdf.name, "stem": pdf.stem, "pages": pages,
        "question_options": options,
        "decision": (f"editor picks {' or '.join(map(str, options))}" if short else "fixed"),
        "split": {str(t): split_by_difficulty(t) for t in options},
        "chunks": chunks,
    }


def use_mix(mix, key="--mix"):
    """Use a difficulty mix for this job instead of the setting."""
    spec = next(x for x in load_schema()["settings"] if x["key"] == "difficulty_mix")
    error = validate_value(spec, mix)
    if error:
        raise SettingsError(error.replace("difficulty_mix", key))
    S["difficulty_mix"] = mix


def parse_pairs(raw, flag):
    try:
        return dict(x.split("=", 1) for x in raw.split(","))
    except ValueError:
        fail(f"{flag} must look like editor=sonnet,reviewer=opus")


def cmd_plan(a):
    if a.mix:
        try:
            use_mix(parse_value(next(x for x in load_schema()["settings"] if x["key"] == "difficulty_mix"), a.mix))
        except (SettingsError, ValueError) as e:
            fail(str(e))
    manual = {}
    for s in a.pages or []:
        k, _, v = s.rpartition("=")
        manual[Path(k).name] = int(v)
    plans = []
    for p in a.pdfs:
        pdf = Path(p).expanduser().resolve()
        if not pdf.exists():
            fail(f"file not found: {pdf}")
        n = manual.get(pdf.name) or page_count(pdf)
        if not n:
            fail(f"{pdf.name}: could not read the page count; pass --pages '{pdf.name}=N'")
        plans.append(plan_pdf(pdf, n))
    stems = [p["stem"] for p in plans]
    if len(set(stems)) != len(stems):
        fail("two PDFs share a name; their output would collide")
    first = Path(plans[0]["pdf"])
    output = Path(S["output_dir"]).expanduser() if S["output_dir"] else first.parent
    run = Path(a.run).expanduser().resolve() if a.run else (
        output / ".exam-forge" / dt.datetime.now().strftime("%Y%m%d-%H%M%S"))
    for stem in stems:
        (run / stem).mkdir(parents=True, exist_ok=True)
    team = resolve_team(S)
    extra = {}
    try:
        if a.team:
            team = clean_team(parse_pairs(a.team, "--team"))
        if a.effort:
            extra["effort"] = clean_effort(parse_pairs(a.effort, "--effort"))
        if a.budget:
            unit, _, amount = a.budget.partition("=")
            extra["budget"] = clean_budget({"unit": unit, "amount": float(amount)})
    except (SettingsError, ValueError) as e:
        fail(str(e))
    result = {
        "created": dt.datetime.now().isoformat(timespec="minutes"),
        "run": str(run),
        "output_dir": str(output),
        # Read with 'pages' needs pdftoppm; without it agents read the whole PDF
        # or rasterize with render-pages.
        "read_pages_supported": bool(shutil.which("pdftoppm")),
        "team": team,
        "difficulty_mix": S["difficulty_mix"],
        **extra,
        "pdfs": plans,
    }
    text = json.dumps(result, ensure_ascii=False, indent=2)
    (run / "plan.json").write_text(text, encoding="utf-8")
    register_run(run)
    emit(run, "plan", team=team, preset=S["quality_preset"], budget=extra.get("budget"),
         pdfs=[{"stem": p["stem"], "pages": p["pages"], "chunks": len(p["chunks"]),
                "questions": max(p["question_options"])} for p in plans])
    print(text)


# ---------- check ----------
def source_reference(text):
    for lang in SOURCE_REFERENCES.values():
        for rx in lang:
            m = re.search(rx, text, re.IGNORECASE)
            if m:
                return m.group(0)
    return None


def question_errors(q, fmt=None, page_range=None, mcq_options=None):
    e = []
    qid = q.get("id", "?")
    for field in ["id", "section", "type", "difficulty", "question", "answer", "explanation", "source", "quote"]:
        if not q.get(field):
            e.append(f"{qid}: '{field}' is missing")
    t = q.get("type")
    if t not in TYPES:
        e.append(f"{qid}: invalid type '{t}'")
    if fmt and t not in FORMATS[fmt]:
        e.append(f"{qid}: type '{t}' is not allowed in format '{fmt}'")
    if q.get("difficulty") not in DIFFICULTIES:
        e.append(f"{qid}: invalid difficulty '{q.get('difficulty')}'")
    if t == "mcq":
        opts = q.get("options") or {}
        allowed = [OPTION_SETS[mcq_options]] if mcq_options else list(OPTION_SETS.values())
        if sorted(opts.keys()) not in allowed or not all(str(v).strip() for v in opts.values()):
            e.append(f"{qid}: needs exactly {mcq_options or '4 or 5'} filled options "
                     f"({'/'.join(''.join(x) for x in allowed)})")
        if q.get("answer") not in opts:
            e.append(f"{qid}: answer must be one of the options")
        if len({str(v).strip().lower() for v in opts.values()}) != len(opts):
            e.append(f"{qid}: two options have the same text")
    elif q.get("options"):
        e.append(f"{qid}: only mcq questions have options")
    if t == "true_false" and q.get("answer") not in ("True", "False"):
        e.append(f"{qid}: true_false answer must be 'True' or 'False'")
    if t == "blank" and "____" not in str(q.get("question", "")):
        e.append(f"{qid}: a blank question must contain '____'")
    text = " ".join([str(q.get("question", ""))] + [str(v) for v in (q.get("options") or {}).values()])
    ref = source_reference(text)
    if ref:
        e.append(f"{qid}: reference to the source: '{ref}'")
    src = q.get("source") or {}
    if not isinstance(src, dict) or not src.get("pdf") or not src.get("section") or not isinstance(src.get("page"), int):
        e.append(f"{qid}: source must be {{pdf, section, page(int)}}")
    elif page_range and not (page_range[0] <= src["page"] <= page_range[1]):
        e.append(f"{qid}: source page {src['page']} is outside chunk range {page_range}")
    if isinstance(src, dict):
        if "emphasis" in src and src["emphasis"] not in ("highlight", "underline"):
            e.append(f"{qid}: source.emphasis must be 'highlight' or 'underline'")
        if "mark" in src and not re.fullmatch(r"mark-p\d+-\d+", str(src["mark"])):
            e.append(f"{qid}: source.mark must look like 'mark-p12-3'")
    return e


def parse_split(text):
    return {k: int(v) for k, v in (x.split("=") for x in text.split(","))}


# ---------- marks-coverage ----------
def compare_words(text):
    """Words of a quote or a mark, free of case, ligatures, line-end hyphens and dotted letters."""
    text = unicodedata.normalize("NFKD", str(text))
    text = "".join(c for c in text if not unicodedata.combining(c)).casefold().replace("ı", "i")
    return re.findall(r"\w+", re.sub(r"(\w)-\s+(\w)", r"\1\2", text))


def shared_run(a, b):
    """Length of the longest run of words the two lists have in common."""
    best, row = 0, [0] * (len(b) + 1)
    for x in a:
        row = [0] + [row[j] + 1 if x == y else 0 for j, y in enumerate(b)]
        best = max(best, *row)
    return best


def rests_on(q, m):
    """Does this question stand on this mark? By its claim, or by a quote sharing the marked words."""
    src = q.get("source") if isinstance(q.get("source"), dict) else {}
    if src.get("mark") == m["id"]:
        return True
    page, quote, marked = src.get("page"), compare_words(q.get("quote", "")), compare_words(m.get("text", ""))
    if not isinstance(page, int) or abs(page - m["page"]) > 1 or not quote or not marked:
        return False  # the reviewer accepts a quote within one page of source.page, so this does too
    return shared_run(quote, marked) >= min(3, len(quote), len(marked))


def read_marks(path):
    marks = read_json(path)
    marks = marks.get("marks") if isinstance(marks, dict) else None
    if not isinstance(marks, list) or not all(isinstance(m, dict) and {"id", "page", "role"} <= set(m) for m in marks):
        fail(f"{path} is not a marks file; make one with: forge.py marks <pdf> <range> --out <file>")
    return marks


def marks_report(questions, marks, page_range=None):
    known = {m["id"] for m in marks}
    if page_range:  # one marks file may serve a whole PDF
        marks = [m for m in marks if page_range[0] <= m["page"] <= page_range[1]]
    emphasis = [m for m in marks if m["role"] == "emphasis"]
    excluded = [m for m in marks if m["role"] == "excluded"]
    covered, emphasized, hits, unknown = set(), 0, [], []
    for q in questions:
        src = q.get("source") if isinstance(q.get("source"), dict) else {}
        touched = [m["id"] for m in emphasis if rests_on(q, m)]
        covered.update(touched)
        emphasized += bool(touched or src.get("emphasis"))
        hits += [{"question": q.get("id"), "mark": m["id"], "page": m["page"], "text": m.get("text", "")}
                 for m in excluded if rests_on(q, m)]
        if src.get("mark") and src["mark"] not in known:
            unknown.append({"question": q.get("id"), "mark": src["mark"]})
    missing = [{k: m.get(k) for k in ("id", "page", "kind", "text")} for m in emphasis if m["id"] not in covered]
    share = round(emphasized / len(questions), 4) if questions else 0
    limit, priority = S["marked_text_share"], S["marked_text_priority"]
    return {"emphasis_marks": len(emphasis), "covered": len(covered), "missing": missing,
            "questions": len(questions), "emphasized_questions": emphasized, "emphasized_share": share,
            "share_limit": limit, "priority": priority, "needs_more": bool(priority and missing and share < limit),
            "excluded_marks": len(excluded), "excluded_hits": hits, "unknown_marks": unknown}


def marks_problems(report):
    return [f"{h['question']}: rests on struck text, which is out of scope ({h['mark']}, page {h['page']}): "
            f"'{h['text']}'" for h in report["excluded_hits"]] \
        + [f"{u['question']}: source.mark {u['mark']} is not in the marks file" for u in report["unknown_marks"]]


def cmd_marks_coverage(a):
    data = read_json(a.chunk)
    questions = data.get("questions") if isinstance(data, dict) else None
    if not isinstance(questions, list):
        fail("'questions' is missing")
    page_range = (data["page_start"], data["page_end"]) if data.get("page_start") and data.get("page_end") else None
    report = marks_report(questions, read_marks(a.marks), page_range)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    problems = marks_problems(report)
    if not problems:
        return
    print("MARKS FAILED:")
    for x in problems:
        print(" -", x)
    if a.fail_out:  # in the shape `approve` reads, because a verified question cannot be replaced
        results = {}
        for u in report["unknown_marks"]:
            results[u["question"]] = {"id": u["question"], "status": "FAIL", "reason": "location",
                                      "feedback": f"source.mark {u['mark']} is not in the marks file."}
        for h in report["excluded_hits"]:
            results[h["question"]] = {"id": h["question"], "status": "FAIL", "reason": "scope",
                                      "feedback": f"Rests on struck text, page {h['page']} ({h['mark']}): "
                                                  f"'{h['text']}'. Write from text that is not struck."}
        write_json(a.fail_out, {"file": Path(a.chunk).name, "results": list(results.values())})
    sys.exit(1)


def cmd_check(a):
    data = read_json(a.json)
    questions = data.get("questions")
    if not isinstance(questions, list) or not questions:
        fail("'questions' is empty or missing")
    fmt = a.format or data.get("format")
    if fmt and fmt not in FORMATS:
        fail(f"invalid format '{fmt}'")
    page_range = None
    if data.get("page_start") and data.get("page_end"):
        page_range = (data["page_start"], data["page_end"])
    run = find_run(a.json)
    if run:  # a job can set its own mix; the thin-page rule below must follow it
        mix = json.loads((run / "plan.json").read_text(encoding="utf-8")).get("difficulty_mix")
        if mix:
            use_mix(mix)
    errors = []
    ids = [q.get("id") for q in questions]
    if len(set(ids)) != len(ids):
        errors.append("repeated id")
    texts = [str(q.get("question", "")).strip().lower() for q in questions]
    if len(set(texts)) != len(texts):
        errors.append("identical question text repeated")
    for q in questions:
        errors += question_errors(q, fmt, page_range, a.mcq_options or S["mcq_options"])
    if a.marks:
        errors += marks_problems(marks_report(questions, read_marks(a.marks), page_range))
    count = {z: sum(1 for q in questions if q.get("difficulty") == z) for z in DIFFICULTIES}
    reduced = False
    if a.expected:
        options = [parse_split(b) for b in a.expected.split("|")]
        match = any(all(count[z] == o.get(z, 0) for z in DIFFICULTIES) for o in options)
        if not match and a.min:
            total = len(questions)
            top = max(sum(o.values()) for o in options)
            reason = str(data.get("reduction_reason", "")).strip()
            target = split_by_difficulty(total)
            if a.min <= total < top and reason and all(abs(count[z] - target[z]) <= 1 for z in DIFFICULTIES):
                match = reduced = True
            elif total < a.min:
                errors.append(f"{total} questions is below the thin-page floor of {a.min}")
            elif not reason and total < top:
                errors.append("fewer questions than expected but no 'reduction_reason'")
            elif total < top:
                errors.append(f"reduced to {total}: difficulty must be {target} (±1), found {count}")
        if not match:
            errors.append(f"difficulty counts {count} do not match expected {a.expected}")
        elif reduced:
            print(f"WARNING: thin-page reduction accepted ({len(questions)} questions); "
                  "the reviewer must check the reason")
    if fmt in ("mixed", "full-mix"):
        missing = FORMATS[fmt] - {q.get("type") for q in questions}
        if missing and len(questions) >= 8:
            errors.append(f"format '{fmt}' has none of: {sorted(missing)}")
    print(json.dumps({"questions": len(questions), "difficulty": count,
                      "type": {t: sum(1 for q in questions if q.get("type") == t) for t in TYPES}},
                     ensure_ascii=False))
    run, pdf, chunk = file_context(a.json)
    emit(run, "check", pdf=pdf, chunk=chunk, ok=not errors, questions=len(questions),
         errors=len(errors), file=Path(a.json).name)
    if errors:
        print("CHECK FAILED:")
        for x in errors:
            print(" -", x)
        sys.exit(1)
    print("CHECK OK")


# ---------- screen / audit / approve / replace ----------
def results_of(path):
    return {r["id"]: r for r in read_json(path).get("results", [])}


def apply_results(data, results, on_pass, on_fail, pending):
    passed, remaining = [], []
    for q in data["questions"]:
        r = results.get(q["id"])
        if r is None:
            if pending(q):
                remaining.append({"id": q["id"], "reason": "not_evaluated",
                                  "feedback": "this question was not evaluated"})
            continue
        if r.get("status") == "PASS":
            on_pass(q)
            q.pop("feedback", None)
            passed.append(q["id"])
        else:
            on_fail(q)
            q["feedback"] = r.get("feedback", "")
            remaining.append({"id": q["id"], "reason": r.get("reason", ""), "feedback": q["feedback"]})
    return passed, remaining


def finish_step(a, data, kind, passed, remaining):
    write_json(a.chunk, data)
    run, pdf, chunk = file_context(a.chunk)
    emit(run, kind, pdf=pdf, chunk=chunk, passed=len(passed),
         failed=[{"id": r["id"], "reason": r["reason"]} for r in remaining])
    print(json.dumps({"passed": len(passed), "remaining": remaining}, ensure_ascii=False, indent=2))
    sys.exit(0 if not remaining else 1)


def cmd_screen(a):
    data = read_json(a.chunk)
    passed, remaining = apply_results(
        data, results_of(a.result),
        on_pass=lambda q: q.__setitem__("screened", True),
        on_fail=lambda q: q.__setitem__("screened", False),
        pending=lambda q: not q.get("screened") and not q.get("verified"))
    finish_step(a, data, "screen", passed, remaining)


def cmd_approve(a):
    data = read_json(a.chunk)
    results = results_of(a.result)
    if a.audit:
        def on_pass(q):
            q["audited"] = True

        def on_fail(q):
            q["verified"] = False
            q["audited"] = False

        pending = lambda q: q.get("audit_required") and not q.get("audited")
        kind = "audit"
    else:
        def on_pass(q):
            q["verified"] = True

        def on_fail(q):
            q["verified"] = False

        pending = lambda q: not q.get("verified")
        kind = "review"
    passed, remaining = apply_results(data, results, on_pass, on_fail, pending)
    finish_step(a, data, kind, passed, remaining)


def cmd_audit_sample(a):
    """All hard questions, all rewritten ones, and a repeatable sample of the rest."""
    data = read_json(a.chunk)
    rate = S["chief_sample_rate"] if a.rate is None else a.rate
    must, rest = [], []
    for q in data["questions"]:
        (must if q.get("difficulty") == "hard" or q.get("round", 1) > 1 else rest).append(q)
    rng = random.Random(Path(a.chunk).name)
    sample = rng.sample(rest, min(len(rest), math.ceil(len(rest) * rate)))
    for q in must + sample:
        q["audit_required"] = True
    write_json(a.chunk, data)
    run, pdf, chunk = file_context(a.chunk)
    ids = [q["id"] for q in must + sample]
    emit(run, "audit_sample", pdf=pdf, chunk=chunk, questions=len(ids))
    print(",".join(ids))


def cmd_source_pages(a):
    """Compact page list for a correction round, so reviewers skip unrelated pages."""
    data = read_json(a.chunk)
    wanted = set(a.ids.split(","))
    lo, hi = data.get("page_start", 1), data.get("page_end", 10 ** 6)
    pages = set()
    for q in data["questions"]:
        if q["id"] in wanted:
            p = q["source"]["page"]
            pages.update(x for x in (p - 1, p, p + 1) if lo <= x <= hi)
    spans = []
    for p in sorted(pages):
        if spans and p == spans[-1][1] + 1:
            spans[-1][1] = p
        else:
            spans.append([p, p])
    print(",".join(f"{x}-{y}" if x != y else str(x) for x, y in spans))


def cmd_replace(a):
    data = read_json(a.chunk)
    new = {q["id"]: q for q in read_json(a.fix).get("questions", [])}
    idx = {q["id"]: i for i, q in enumerate(data["questions"])}
    for qid, q in new.items():
        if qid not in idx:
            fail(f"{qid} is not in the chunk; a fix must reuse the existing id")
        old = data["questions"][idx[qid]]
        if old.get("verified"):
            fail(f"{qid} is already verified and cannot change")
        if q.get("difficulty") != old.get("difficulty"):
            fail(f"{qid}: the new question must keep difficulty {old.get('difficulty')}")
        q["verified"] = False
        q["round"] = old.get("round", 1) + 1
        if old.get("audit_required"):
            q["audit_required"] = True
        data["questions"][idx[qid]] = q
    write_json(a.chunk, data)
    run, pdf, chunk = file_context(a.chunk)
    emit(run, "replace", pdf=pdf, chunk=chunk, ids=list(new))
    print(f"{len(new)} questions replaced")


# ---------- merge ----------
def cmd_merge(a):
    names = {}
    for s in a.part or []:
        k, _, v = s.rpartition("=")
        names[Path(k).name] = v
    titles = a.titles or S["section_titles"]
    parts, unverified, unaudited = [], [], []
    for d in a.dirs:
        d = Path(d).expanduser()
        files = sorted((p for p in d.glob("chunk-*.json") if re.fullmatch(r"chunk-\d+", p.stem)),
                       key=lambda p: int(p.stem.split("-")[1]))
        if not files:
            fail(f"no chunk-N.json in {d}")
        questions, pdf = [], None
        for p in files:
            data = read_json(p)
            pdf = pdf or data.get("source")
            for q in data["questions"]:
                if not q.get("verified"):
                    unverified.append(q["id"])
                elif q.get("audit_required") and not q.get("audited"):
                    unaudited.append(q["id"])
                questions.append(q)
        pdf = pdf or d.name
        stem = Path(pdf).stem
        name = stem if titles == "filename" else names.get(Path(pdf).name, stem)
        parts.append({"name": name, "pdf": pdf, "questions": questions})
    if unverified:
        fail(f"unverified questions cannot enter a book: {unverified}")
    if unaudited:
        fail(f"questions still waiting for the chief editor: {unaudited}")
    title = parts[0]["name"] if titles == "filename" and len(parts) == 1 else a.title
    book = {
        "title": title, "format": a.format, "language": a.language,
        "date": dt.date.today().isoformat(), "sources": [p["pdf"] for p in parts], "parts": parts,
    }
    write_json(a.out, book)
    total = sum(len(p["questions"]) for p in parts)
    emit(find_run(a.dirs[0]), "merge", questions=total, parts=len(parts), title=title)
    print(f"{total} verified questions -> {a.out}")


# ---------- render ----------
def group_sections(questions):
    """Sections by first source page; easy to hard inside each."""
    groups = {}
    for q in questions:
        groups.setdefault(q["section"].strip(), []).append(q)
    order = sorted(groups.items(), key=lambda kv: min(x["source"]["page"] for x in kv[1]))
    out = []
    for name, qs in order:
        qs.sort(key=lambda x: (DIFFICULTIES.index(x["difficulty"]), x["source"]["page"]))
        out.append((name, qs))
    return out


def esc(x):
    return html.escape(str(x))


def answer_text(q, L):
    a = q["answer"]
    if q["type"] == "mcq":
        return f'{a}) {q["options"][a]}'
    if q["type"] == "true_false":
        return L["true"] if a == "True" else L["false"]
    return a


def source_line(q, L):
    s = q["source"]
    return (f'{L["source"]}: {esc(s["pdf"])} · {L["section"]}: {esc(s["section"])} · '
            f'{L["page"]} {esc(s["page"])}')


def question_html(no, q, L, inline):
    t = q["type"]
    parts = [f'<article class="question"><header><span class="no">{no}</span>'
             f'<span class="badge {q["difficulty"]}">{L[q["difficulty"]]}</span>'
             f'<span class="type">{L[t]}</span></header>']
    body = esc(q["question"]).replace("____", '<span class="blank"></span>')
    parts.append(f'<p class="stem">{body}</p>')
    if t == "mcq":
        parts.append('<ol class="options">' + "".join(
            f'<li><b>{k})</b> {esc(q["options"][k])}</li>' for k in LETTERS if k in q["options"]) + "</ol>")
    elif t == "true_false" and not inline:
        parts.append(f'<p class="tf"><span>☐ {L["true"]}</span><span>☐ {L["false"]}</span></p>')
    elif t == "open" and not inline:
        parts.append('<div class="writing"></div>')
    if inline:
        parts.append(f'<div class="inline-answer"><p class="correct">{esc(answer_text(q, L))}</p>'
                     f'<p class="explanation">{esc(q["explanation"])}</p>'
                     f'<p class="citation">{source_line(q, L)}</p></div>')
    parts.append("</article>")
    return "".join(parts)


def answer_html(no, q, L):
    return (f'<div class="answer"><span class="no">{no}</span><div>'
            f'<p class="correct">{esc(answer_text(q, L))}</p>'
            f'<p class="explanation">{esc(q["explanation"])}</p>'
            f'<p class="citation">{source_line(q, L)}</p></div></div>')


def count_label(n, L, key):
    return f'{n} {L.get(key + "_one", L[key]) if n == 1 else L[key]}'


def range_label(a, b, L):
    return f'{L["question"]} {a}–{b}' if b > a else f'{L["question"]} {a}'


def format_date(iso, L):
    d = dt.date.fromisoformat(iso)
    return L["date"].format(day=d.day, month=L["months"][d.month - 1], year=d.year)


def build_book_html(book, placement, contents=True):
    L = LABELS.get(book.get("language"), LABELS["en"])
    parts = book["parts"]
    multi = len(parts) > 1
    body, toc, end_keys = [], [], []
    no = sec_no = 1
    for pi, part in enumerate(parts, 1):
        sections = group_sections(part["questions"])
        if multi:
            first_no = no
            count = len(part["questions"])
            body.append(f'<section class="part-cover"><span>{L["part"]} {pi:02d}</span>'
                        f'<h2>{esc(part["name"])}</h2><p>{count_label(count, L, "questions")} · '
                        f'{count_label(len(sections), L, "sections")}</p>'
                        f'<small>{L["source"]}: {esc(part["pdf"])}</small></section>')
            toc.append(f'<li class="toc-part"><span class="toc-no">{pi:02d}</span>'
                       f'<span class="toc-name">{esc(part["name"])}</span>'
                       f'<span class="toc-range">{range_label(first_no, first_no + count - 1, L)}</span></li>')
        for name, qs in sections:
            start = no
            cells, answers = [], []
            for q in qs:
                cells.append(question_html(no, q, L, placement == "inline"))
                answers.append(answer_html(no, q, L))
                no += 1
            toc.append(f'<li class="{"toc-sub" if multi else ""}"><span class="toc-no">{sec_no:02d}</span>'
                       f'<span class="toc-name">{esc(name)}</span>'
                       f'<span class="toc-range">{range_label(start, no - 1, L)}</span></li>')
            body.append(f'<section class="section"><div class="section-band"><span>{L["section"]} {sec_no:02d}</span>'
                        f'<h2>{esc(name)}</h2><small>{count_label(len(qs), L, "questions")}</small></div>'
                        f'<div class="questions">{"".join(cells)}</div></section>')
            if placement == "section_end":
                body.append(f'<section class="key"><div class="key-band"><span>{L["section"]} {sec_no:02d}</span>'
                            f'<h3>{L["answer_key"]} · {esc(name)}</h3></div>{"".join(answers)}</section>')
            elif placement == "book_end":
                end_keys.append(f'<div class="key-group"><div class="key-band"><span>{L["section"]} {sec_no:02d}</span>'
                                f'<h3>{esc(name)}</h3></div>{"".join(answers)}</div>')
            sec_no += 1
    if placement == "book_end":
        body.append(f'<section class="key"><h2 class="key-title">{L["answer_key"]}</h2>{"".join(end_keys)}</section>')
        toc.append(f'<li class="toc-part"><span class="toc-no">✓</span>'
                   f'<span class="toc-name">{L["answer_key"]}</span><span class="toc-range"></span></li>')

    questions = [q for p in parts for q in p["questions"]]
    count = {z: sum(1 for q in questions if q["difficulty"] == z) for z in DIFFICULTIES}
    bar = "".join(f'<span class="{z}" style="flex:{max(count[z], 0.001)}"></span>' for z in DIFFICULTIES)
    legend = "".join(f'<li><i class="{z}"></i>{L[z]} <b>{count[z]}</b></li>' for z in DIFFICULTIES)
    guide = f'<p class="guide">{L[f"guide_{placement}"]}</p>'
    css = (TEMPLATES / "book.css").read_text(encoding="utf-8")
    page = (TEMPLATES / "book.html").read_text(encoding="utf-8")
    fills = {
        "LANG": L["lang"], "CSS": css, "TITLE": esc(book["title"]), "BOOK": L["book"],
        "SOURCE_LABEL": L["source"], "SOURCE": esc(", ".join(book.get("sources", []))),
        "FORMAT": L.get(f'fmt_{book.get("format")}', esc(book.get("format", ""))),
        "DATE": format_date(book.get("date", dt.date.today().isoformat()), L),
        "TOTAL": count_label(len(questions), L, "questions"), "SECTIONS": count_label(sec_no - 1, L, "sections"),
        "MIX_LABEL": L["difficulty_mix"], "BAR": bar, "LEGEND": legend, "BODY": "".join(body),
        "CONTENTS_PAGE": (f'<section class="contents"><h2>{L["contents"]}</h2><ol>{"".join(toc)}</ol>{guide}</section>'
                          if contents else ""),
        "COVER_GUIDE": "" if contents else guide,
    }
    for k, v in fills.items():
        page = page.replace("{{" + k + "}}", v)
    return page, len(questions), sec_no - 1


def cmd_render(a):
    book = read_json(a.json)
    questions = [q for p in book.get("parts", []) for q in p["questions"]]
    bad = [q["id"] for q in questions if not q.get("verified")]
    if bad:
        fail(f"unverified questions cannot enter a book: {bad}")
    errors = []
    for q in questions:
        errors += question_errors(q, book.get("format"))
    if errors:
        fail("pre-render check: " + "; ".join(errors[:10]))
    placement = a.answers or S["answer_placement"]
    contents = not a.no_contents and S["book_contents"] != "never"
    page, total, sections = build_book_html(book, placement, contents)
    out = Path(a.out).expanduser().resolve()
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        if out.exists():
            stamp = dt.datetime.fromtimestamp(out.stat().st_mtime).strftime("%Y-%m-%d-%H%M")
            stem = out.stem.encode("utf-8")[:200].decode("utf-8", "ignore")  # the date must still fit a file name
            backup, n = out.with_name(f"{stem}-{stamp}.pdf"), 1
            while backup.exists():  # never over an older backup
                n += 1
                backup = out.with_name(f"{stem}-{stamp}-{n}.pdf")
            os.replace(out, backup)  # rename() fails on Windows when the target exists
            print(f"previous book kept: {backup.name}")
    except OSError as e:
        hint = (f"If {out.name} is in use, close it in your PDF viewer and render again." if sys.platform == "win32"
                else "Check the permissions of that folder.")
        fail(f"cannot write the book in {out.parent}: {e.strerror}. {hint}")
    run = find_run(a.json)
    h = None
    td = tempfile.mkdtemp(prefix="exam-forge-")
    try:
        # Next to the book, not in the temp folder: a snap Chromium has a private /tmp and would print an error page.
        # A short fixed name: one built from the book's name can pass the file system's limit when the book does not.
        fd, name = tempfile.mkstemp(dir=out.parent, prefix=".exam-forge-", suffix=".html")
        h = Path(name)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(page)
        if a.html:
            Path(a.html).expanduser().write_text(page, encoding="utf-8")
        browser = find_browser()
        if not browser:
            fallback = out.with_suffix(".html")
            fallback.write_text(page, encoding="utf-8")
            emit(run, "render", questions=total, out=str(fallback), html_only=True)
            print(f"ERROR: Chrome, Chromium or Edge not found. Book written as HTML: {fallback}\n"
                  "Open it in a browser and print to PDF, or set EXAM_FORGE_BROWSER to a browser path.",
                  file=sys.stderr)
            sys.exit(3)
        try:
            r = subprocess.run([browser, "--headless=new", "--disable-gpu", "--no-pdf-header-footer",
                                "--use-mock-keychain", "--password-store=basic",
                                f"--user-data-dir={Path(td) / 'profile'}", "--no-first-run",
                                f"--print-to-pdf={out}", h.as_uri()],
                               capture_output=True, timeout=180, **UTF8)
        except subprocess.TimeoutExpired:
            fallback = out.with_suffix(".html")
            fallback.write_text(page, encoding="utf-8")
            emit(run, "render", questions=total, out=str(fallback), html_only=True)
            print(f"ERROR: {Path(browser).name} did not finish printing. Book written as HTML: {fallback}",
                  file=sys.stderr)
            sys.exit(3)
    except OSError as e:
        if not h:  # the page to print could not be created: the folder is the news, not the temp name
            fail(f"cannot write the book in {out.parent}: {e.strerror}. Check the permissions of that folder.")
        fail(f"cannot write the book: {e}")
    finally:
        if h:
            remove_quiet(h)
        shutil.rmtree(td, ignore_errors=True)  # Chrome's helpers can hold the profile open a moment on Windows
    if not out.exists():
        fail(f"PDF was not produced: {r.stderr[-800:]}")
    emit(run, "render", questions=total, out=str(out))
    print(f"{total} questions, {sections} sections -> {out}")


# ---------- cost ----------
def model_family(model):
    for name in ("haiku", "sonnet", "opus"):
        if name in (model or ""):
            return name
    return None


def run_cost(run, pricing):
    """Sum usage from the subagent transcripts this run started."""
    events = Path(run) / "events.jsonl"
    if not events.is_file():
        return None
    starts = []
    for line in events.read_text(encoding="utf-8").splitlines():
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if e.get("type") == "agent_start" and e.get("transcript") and e.get("tool_use_id"):
            starts.append(e)
    by_role, seen_files = {}, set()
    total = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0, "usd": 0.0}
    for e in starts:
        folder = Path(e["transcript"]).with_suffix("") / "subagents"
        if not folder.is_dir():
            continue
        for meta in folder.glob("*.meta.json"):
            try:
                info = json.loads(meta.read_text(encoding="utf-8"))
            except ValueError:
                continue
            if info.get("toolUseId") != e["tool_use_id"]:
                continue
            log = meta.with_name(meta.name.replace(".meta.json", ".jsonl"))
            if log in seen_files or not log.is_file():
                continue
            seen_files.add(log)
            seen_ids = set()
            role = by_role.setdefault(e.get("role", "other"), {"input": 0, "output": 0, "usd": 0.0})
            for line in log.read_text(encoding="utf-8").splitlines():
                try:
                    m = json.loads(line).get("message") or {}
                except ValueError:
                    continue
                u = m.get("usage")
                if not u or m.get("id") in seen_ids:
                    continue
                seen_ids.add(m.get("id"))
                price = pricing.get(model_family(m.get("model")) or "", {})
                nums = {"input": u.get("input_tokens", 0), "output": u.get("output_tokens", 0),
                        "cache_read": u.get("cache_read_input_tokens", 0),
                        "cache_write": u.get("cache_creation_input_tokens", 0)}
                usd = sum(nums[k] * price.get(k, 0) for k in nums) / 1e6
                for k in nums:
                    total[k] += nums[k]
                total["usd"] += usd
                role["input"] += nums["input"] + nums["cache_read"] + nums["cache_write"]
                role["output"] += nums["output"]
                role["usd"] += usd
    total["usd"] = round(total["usd"], 4)
    for r in by_role.values():
        r["usd"] = round(r["usd"], 4)
    return {"agents": len(seen_files), "total": total, "by_role": by_role,
            "note": "Subagents only; the orchestrator session is not included."}


def cmd_cost(a):
    run = Path(a.run).expanduser()
    cost = run_cost(run, S["pricing"])
    if a.budget_check:
        sys.exit(6 if budget_passed(run, cost) else 0)
    if a.record:
        record_calibration(run, cost)
    print(json.dumps(cost, indent=2))


# ---------- estimate ----------
# Tokens per agent role, measured on a real 30-page, 40-question job at effort "high"
# and including its rewrite rounds. Input scales with pages + questions, output with
# questions. Every run recorded with `cost --record` corrects them for this user.
COST_MODEL = {"editor": {"input": 17000, "output": 2200}, "screener": {"input": 8000, "output": 1400},
              "reviewer": {"input": 17000, "output": 1850}}
CACHE_READ_SHARE = 0.72  # of input tokens in that job; the rest were cache writes
EFFORTS = ["low", "medium", "high", "xhigh", "max"]
SCREENER_EFFORTS = ["low", "medium"]  # a proofreading pass gains nothing from more
EFFORT_FACTOR = {"low": 0.6, "medium": 0.8, "high": 1.0, "xhigh": 1.3, "max": 1.7}  # assumed, not measured
ESTIMATE_MARGIN = 0.35
ROLES = ["editor", "screener", "reviewer", "chief_editor"]
ROLE_RANK = {"editor": 3, "screener": 0.5, "reviewer": 4, "chief_editor": 2}
MODEL_RANK = {"": 0, "haiku": 1, "sonnet": 3, "opus": 5}
EFFORT_RANK = dict(zip(EFFORTS, [0, 1, 1.7, 2.1, 2.3]))  # each step adds less
OPUS_ROLES = ("editor", "reviewer", "chief_editor")
BUDGET_ALARM = 1.2


def calibration_path():
    return config_dir() / "calibration.json"


def calibration():
    try:
        return json.loads(calibration_path().read_text(encoding="utf-8")).get("factor", {})
    except (OSError, ValueError, AttributeError):
        return {}


def role_tokens(role, pages, questions, sample_rate, factor=None):
    """Input and output tokens one role is expected to use on a job, at effort high."""
    base = COST_MODEL["reviewer" if role == "chief_editor" else role]
    share = sample_rate if role == "chief_editor" else 1
    scale = share * (factor if factor is not None else 1)
    return base["input"] * (pages + questions) * scale, base["output"] * questions * scale


def role_cost(role, model, effort, tokens, pricing):
    """(tokens, usd) for one role, or zeros when the role is off."""
    if not model:
        return 0, 0.0
    price = pricing.get(model, {})
    tokens_in, tokens_out = tokens[role]
    in_rate = CACHE_READ_SHARE * price.get("cache_read", 0) + (1 - CACHE_READ_SHARE) * price.get("cache_write", 0)
    spread = EFFORT_FACTOR[effort]
    return ((tokens_in + tokens_out) * spread,
            (tokens_in * in_rate + tokens_out * price.get("output", 0)) * spread / 1e6)


def job_size(pdfs):
    """Pages and questions of a job, from the same rules `plan` uses."""
    pages = questions = 0
    for p in pdfs:
        pdf = Path(p).expanduser()
        n = page_count(pdf)
        if not n:
            raise SettingsError(f"could not read the page count of {pdf.name}")
        pages += n
        questions += max(plan_pdf(pdf, n)["question_options"])
    return pages, questions


def estimate(tokens, team, effort, pricing):
    roles = {r: role_cost(r, team.get(r, ""), effort.get(r, "high"), tokens, pricing) for r in ROLES}
    used = sum(t for t, _ in roles.values())
    usd = sum(u for _, u in roles.values())
    return {"tokens": round(used), "usd": round(usd, 2), "usd_low": round(usd * (1 - ESTIMATE_MARGIN), 2),
            "usd_high": round(usd * (1 + ESTIMATE_MARGIN), 2),
            "by_role": {r: {"tokens": round(t), "usd": round(u, 2)} for r, (t, u) in roles.items()}}


def quality(team, effort):
    return sum(ROLE_RANK[r] * (MODEL_RANK[team[r]] + (EFFORT_RANK[effort[r]] if team[r] else 0)) for r in ROLES)


def solve_budget(tokens, unit, amount, pricing, opus):
    """The best team within the target. Reviewers stay on, at Sonnet or better."""
    strong = ["sonnet", "opus"] if opus else ["sonnet"]
    pick = {"editor": strong, "screener": ["", "haiku"], "reviewer": strong, "chief_editor": ["", *strong]}
    levels = {r: SCREENER_EFFORTS if r == "screener" else EFFORTS for r in ROLES}
    costs = {r: {(m, e): role_cost(r, m, e, tokens, pricing) for m in pick[r] for e in levels[r]} for r in ROLES}
    metric = 0 if unit == "tokens" else 1
    best = cheapest = None
    for models in product(*(pick[r] for r in ROLES)):
        team = dict(zip(ROLES, models))
        for chosen in product(*(levels[r] for r in ROLES)):
            effort = dict(zip(ROLES, chosen))
            spent = sum(costs[r][(team[r], effort[r])][metric] for r in ROLES)
            rank = quality(team, effort)
            if cheapest is None or spent < cheapest[0]:
                cheapest = (spent, team, effort)
            if spent <= amount and (best is None or (rank, spent) > best[0]):
                best = ((rank, spent), team, effort)
    if best:
        return {"team": best[1], "effort": best[2], "reachable": True, "minimum": cheapest[0]}
    return {"team": cheapest[1], "effort": cheapest[2], "reachable": False, "minimum": cheapest[0]}


def plan_estimate(body, settings):
    """What the job form shows. With a budget, also the team that fits it."""
    global S
    S = settings
    pdfs = body.get("pdfs")
    if not isinstance(pdfs, list) or not pdfs:
        raise SettingsError("choose at least one PDF")
    pages, questions = job_size(pdfs)
    factors = calibration()
    tokens = {r: role_tokens(r, pages, questions, settings["chief_sample_rate"], factors.get(r)) for r in ROLES}
    pricing = settings["pricing"]
    team, effort = clean_team(body.get("team")), clean_effort(body.get("effort"))
    out = {"ok": True, "pages": pages, "questions": questions}
    budget = clean_budget(body.get("budget"))
    if budget:
        fit = solve_budget(tokens, budget["unit"], budget["amount"], pricing, model_access().get("opus") is True)
        team, effort = fit["team"], fit["effort"]
        out.update(team=team, effort=effort, reachable=fit["reachable"], minimum=fit["minimum"])
    out["estimate"] = estimate(tokens, team, effort, pricing)
    return out


def clean_team(team):
    if not isinstance(team, dict) or set(team) != set(ROLES):
        raise SettingsError("team must name every role")
    if any(team[r] not in MODEL_RANK for r in ROLES):
        raise SettingsError("unknown model in team")
    if not team["editor"] or not team["reviewer"]:
        raise SettingsError("the editor and reviewer cannot be off")
    return team


def clean_effort(effort):
    if not isinstance(effort, dict) or set(effort) != set(ROLES) or any(effort[r] not in EFFORTS for r in ROLES):
        raise SettingsError(f"effort must give every role one of {EFFORTS}")
    return effort


def clean_budget(budget):
    if budget is None:
        return None
    amount = budget.get("amount") if isinstance(budget, dict) else None
    if not isinstance(budget, dict) or budget.get("unit") not in ("usd", "tokens") \
            or isinstance(amount, bool) or not isinstance(amount, (int, float)) or amount <= 0:
        raise SettingsError("budget needs a unit (usd or tokens) and a positive amount")
    return {"unit": budget["unit"], "amount": amount}


def default_team(settings):
    """Where the job form starts. The chief editor gets the best model this account has."""
    access = model_access().get("opus")
    team = dict(PRESETS[settings["quality_preset"]])
    team["chief_editor"] = "opus"
    for role, model in settings["models"].items():
        if model:
            team[role] = model
    if not settings["screener_enabled"]:
        team["screener"] = ""
    if access is not True:
        team = {r: "sonnet" if m == "opus" else m for r, m in team.items()}
    return {"team": team, "effort": {r: "low" if r == "screener" else "high" for r in ROLES}, "opus": access}


def models_path():
    return config_dir() / "models.json"


def model_access():
    try:
        data = json.loads(models_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def probe_opus():
    """Ask Claude Code for one word from Opus. True, False, or None when unclear."""
    if os.environ.get("EXAM_FORGE_NO_PROBE"):
        return None
    claude = shutil.which("claude")
    if not claude:
        return None
    try:
        r = subprocess.run([claude, "-p", "Reply with: ok", "--model", "opus", "--output-format", "json",
                            "--no-session-persistence", "--disable-slash-commands", "--strict-mcp-config", "--tools", ""],
                           capture_output=True, timeout=90, stdin=subprocess.DEVNULL, cwd=tempfile.gettempdir(), **UTF8)
    except (OSError, subprocess.TimeoutExpired):
        return None
    try:
        answer = json.loads(r.stdout)
    except ValueError:
        answer = {}
    if r.returncode == 0 and answer.get("is_error") is False:
        return True
    text = f"{answer.get('result', '')} {r.stderr}".lower()
    denied = ("not available", "not_found", "not found", "access", "upgrade", "does not support", "isn't available")
    return False if "model" in text and any(w in text for w in denied) else None


def cmd_models(a):
    if a.probe:
        result = probe_opus()
        models_path().parent.mkdir(parents=True, exist_ok=True)
        write_atomic(models_path(), {"opus": result, "t": now_iso()})
    print(json.dumps(model_access()))


def probe_in_background():
    """The answer rarely changes: look again after 30 days, or after 1 when it was unclear."""
    if os.environ.get("EXAM_FORGE_NO_PROBE"):  # tests: the real CLI must never start from here
        return
    known = model_access()
    try:
        age = (dt.datetime.now() - dt.datetime.fromisoformat(known["t"])).days
    except (KeyError, TypeError, ValueError):
        age = 999
    if age < (30 if known.get("opus") is not None else 1):
        return
    subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "models", "--probe"], stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)


def read_plan(run):
    try:
        return json.loads((Path(run) / "plan.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def budget_passed(run, cost):
    """True once, when a job with a target has used 120% of it."""
    budget, mark = read_plan(run).get("budget"), Path(run) / "budget-alarm"
    if not budget or not cost or mark.exists():
        return False
    total = cost["total"]
    spent = total["usd"] if budget["unit"] == "usd" else sum(total[k] for k in ("input", "output", "cache_read", "cache_write"))
    if spent < budget["amount"] * BUDGET_ALARM:
        return False
    mark.touch()
    return True


def record_calibration(run, cost):
    """Move each role's correction a third of the way to what this run really used."""
    mark, plan = Path(run) / "calibrated", read_plan(run)
    if mark.exists() or not cost or not plan.get("pdfs"):
        return
    pages = sum(p["pages"] for p in plan["pdfs"])
    questions = sum(max(p["question_options"]) for p in plan["pdfs"])
    factors, effort = calibration(), plan.get("effort", {})
    for role in ROLES:
        seen = cost["by_role"].get(role.replace("_", "-"))
        tokens = role_tokens(role, pages, questions, S["chief_sample_rate"])
        expected = sum(tokens) * EFFORT_FACTOR[effort.get(role, "high")]
        if seen and expected:
            ratio = min(3.0, max(0.3, (seen["input"] + seen["output"]) / expected))
            factors[role] = round(factors.get(role, 1.0) * 2 / 3 + ratio / 3, 3)
    calibration_path().parent.mkdir(parents=True, exist_ok=True)
    write_atomic(calibration_path(), {"factor": factors, "t": now_iso()})
    mark.touch()


def cmd_studio(a):
    sys.path.insert(0, str(ROOT / "studio"))
    import server
    server.main(port=a.port or S["studio_port"], open_browser=not a.no_open)


# ---------- job form ----------
# The Studio cannot run agents. A Claude Code session waits here, the browser hands
# it a job through these files, and everything after that runs as usual.
FRESH_SECONDS = 10
MAX_FOLDER_PDFS = 200
ASK_POLL_SECONDS = 1
REPLACE_TRIES = 20
LOCK_TRIES = 600  # at 20 ms, longer than a dead command's lock stays fresh
STUDIO_START_TICKS = 100  # of 0.1 s
MAX_UPLOAD_NAME = 240  # bytes; file systems stop at 255, and a repeated name gets a suffix


def intake_dir():
    return config_dir() / "intake"


def write_atomic(path, data, indent=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.urandom(4).hex()}.tmp")
    try:
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=indent) + "\n", encoding="utf-8")
        try:
            os.chmod(tmp, path.stat().st_mode & 0o7777)  # a file the user made private stays private
        except OSError:
            pass  # a new file
        for attempt in range(REPLACE_TRIES):
            try:
                os.replace(tmp, path)
                return
            except PermissionError:  # Windows: the Studio or an antivirus has the file open for a moment
                if attempt == REPLACE_TRIES - 1:
                    raise
                time.sleep(0.05)
    finally:
        remove_quiet(tmp)


def is_fresh(path):
    try:
        return time.time() - path.stat().st_mtime < FRESH_SECONDS
    except OSError:
        return False


def read_quiet(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def remove_quiet(path):
    try:
        path.unlink()
    except OSError:
        pass


def pid_alive(pid):
    if pid >= 2 ** 31:  # no such process, and os.kill would raise OverflowError
        return False
    if sys.platform == "win32":  # os.kill(pid, 0) would send Ctrl+C there
        import ctypes
        kernel = ctypes.windll.kernel32
        handle = kernel.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        code = ctypes.c_ulong()
        alive = handle and kernel.GetExitCodeProcess(handle, ctypes.byref(code)) and code.value == 259  # STILL_ACTIVE
        if handle:
            kernel.CloseHandle(handle)
        return bool(alive)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        pass  # someone else's process, so it exists
    return True


def session_waiting():
    waiter = intake_dir() / "waiter.json"
    if not is_fresh(waiter):
        return False
    mark = read_quiet(waiter)
    pid = mark.get("pid") if isinstance(mark, dict) else None
    return pid_alive(pid) if type(pid) is int and pid > 0 else True


def clear_leftovers():
    """A job or a cancel with no live wait was left by one that died, and must never run.
    A live wait is only between polls: what is here is its own, and it will claim it."""
    waiter = intake_dir() / "waiter.json"
    mark = read_quiet(waiter)
    pid = mark.get("pid") if isinstance(mark, dict) else None
    if is_fresh(waiter) and type(pid) is int and pid > 0 and pid != os.getpid() and pid_alive(pid):
        return
    for name in ("job.json", "cancel.json"):
        remove_quiet(intake_dir() / name)


def claim(path):
    """Move path out of the way. Only one of many waits can succeed. Its new name, or None."""
    claimed = path.with_name(f".claimed.{os.getpid()}")
    try:
        os.replace(path, claimed)
        return claimed
    except OSError:
        return None


def intake_request():
    return read_quiet(intake_dir() / "request.json")


def validate_pdf(raw):
    """Absolute path of a real PDF file, or a SettingsError naming what is wrong."""
    if not isinstance(raw, str) or not raw.strip():
        raise SettingsError("empty PDF path")
    pdf = Path(raw.strip()).expanduser().resolve()
    if not pdf.is_file():
        raise SettingsError(f"file not found: {pdf}")
    with open(pdf, "rb") as f:
        if f.read(5) != b"%PDF-":
            raise SettingsError(f"not a PDF: {pdf.name}")
    return str(pdf)


def pdfs_under(folder, denied):
    """PDFs in a folder and its subfolders, skipping hidden entries. Folders it may not open go into denied."""
    found = []
    for base, dirs, files in os.walk(folder, onerror=denied.append):
        dirs[:] = sorted(d for d in dirs if not d.startswith("."))
        found += [Path(base) / f for f in sorted(files)
                  if f.lower().endswith(".pdf") and not f.startswith(".")]
        if len(found) > MAX_FOLDER_PDFS:
            raise SettingsError(f"more than {MAX_FOLDER_PDFS} PDFs in {folder.name or folder}; choose a smaller folder")
    return found


def expand_sources(paths, skipped=None):
    """Files and folders the user chose, as a flat list of PDF paths. What cannot be used
    inside a folder is named in skipped, so one bad file does not lose the whole folder."""
    skipped = [] if skipped is None else skipped
    if not isinstance(paths, list) or not all(isinstance(p, (str, os.PathLike)) and str(p).strip() for p in paths):
        raise SettingsError("paths must be a list of file or folder paths")
    out = []
    for raw in paths:
        p = Path(str(raw).strip()).expanduser().resolve()
        try:
            if not p.is_dir():
                out.append(validate_pdf(str(p)))
                continue
            denied, good, bad = [], [], []
            for f in pdfs_under(p, denied):
                try:
                    good.append(validate_pdf(str(f)))
                except (SettingsError, OSError):
                    bad.append(f.name)
            skipped += bad + [Path(e.filename).name for e in denied]
            if not good and denied:
                raise SettingsError(f"cannot open {denied[0].filename}: {denied[0].strerror}")
            if not good and bad:
                more = f" and {len(bad) - 5} more" if len(bad) > 5 else ""
                raise SettingsError(f"no PDFs in {p.name or p}; these are not real PDFs: {', '.join(bad[:5])}{more}")
            if not good:
                raise SettingsError(f"no PDFs in {p.name or p}")
            out += good
        except OSError as e:
            raise SettingsError(f"cannot read {p}: {e.strerror}")
    return list(dict.fromkeys(out))


def list_folder(raw):
    """Folders and PDFs for the Studio's file browser. Other files stay out of it."""
    try:
        p = Path(str(raw or "~").strip() or "~").expanduser().resolve()
        file = None
        if p.is_file():
            file, p = validate_pdf(str(p)), p.parent
        found = p.is_dir()
    except (OSError, RuntimeError) as e:  # a drive with no disk in it, a locked folder, an unknown ~user
        raise SettingsError(f"cannot open {raw}: {e}")
    if not found:
        raise SettingsError(f"folder not found: {p}")
    try:
        names = [e for e in os.scandir(p) if not e.name.startswith(".")]
    except OSError as e:
        raise SettingsError(f"cannot open {p}: {e.strerror}")
    entries = []
    for e in names:
        try:
            is_dir = e.is_dir()
        except OSError:
            continue
        if is_dir or e.name.lower().endswith(".pdf"):
            entries.append({"name": e.name, "path": e.path, "dir": is_dir})
    entries.sort(key=lambda e: (not e["dir"], e["name"].lower()))
    return {"path": str(p), "parent": str(p.parent) if p.parent != p else None, "entries": entries, "file": file}


def windows_drives():
    """Roots such as D:\\. This reads a bit mask, so a sleeping or empty drive is not touched."""
    try:
        import ctypes
        mask = ctypes.windll.kernel32.GetLogicalDrives()
    except (ImportError, AttributeError, OSError):
        return []
    return [f"{letter}:\\" for i, letter in enumerate(string.ascii_uppercase) if mask >> i & 1]


def places():
    home = Path.home()
    spots = [("home", home), ("desktop", home / "Desktop"), ("documents", home / "Documents"),
             ("downloads", home / "Downloads")]
    if sys.platform == "darwin":
        spots.append(("volumes", Path("/Volumes")))
    found = [{"key": k, "path": str(p)} for k, p in spots if p.is_dir()]
    if sys.platform == "win32":  # a drive root has no parent, so the Up button never reaches another drive
        found += [{"key": "drive", "label": d[:2], "path": d} for d in windows_drives()]
    return found


def upload_target(batch, name):
    """Where a dropped PDF goes. A browser hides its real path, so it needs a copy."""
    if not re.fullmatch(r"[0-9]{1,20}", batch or ""):
        raise SettingsError("bad upload batch")
    clean = re.sub(r'[\x00-\x1f/\\:*?"<>|]', "_", Path(str(name).replace("\\", "/")).name).strip()
    if clean.startswith(".") or not clean.lower().endswith(".pdf"):
        raise SettingsError(f"not a PDF: {clean}")
    if len(os.fsencode(clean)) > MAX_UPLOAD_NAME:
        raise SettingsError("the file name is too long")
    folder = Path.home() / "Exam Forge" / batch
    folder.mkdir(parents=True, exist_ok=True)
    target, n = folder / clean, 1
    while True:
        try:  # taken here and now, so parallel drops of one name cannot end up in one file
            os.close(os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
            return target
        except FileExistsError:
            n += 1
            target = folder / f"{Path(clean).stem}-{n}.pdf"


def validate_job(body, settings):
    """Clean a job from the form. Only known fields survive."""
    if not isinstance(body, dict):
        raise SettingsError("job must be an object")
    raw = body.get("pdfs")
    if not isinstance(raw, list) or not raw:
        raise SettingsError("choose at least one PDF")
    pdfs = [validate_pdf(p) for p in raw]
    if len(set(pdfs)) != len(pdfs):
        raise SettingsError("the same PDF was added twice")
    stems = [Path(p).stem for p in pdfs]
    if len(set(stems)) != len(stems):
        raise SettingsError("two PDFs share a name; their output would collide")
    if body.get("format") not in FORMATS:
        raise SettingsError(f"unknown format: {body.get('format')}")
    if body.get("mcq_options") not in (4, 5):
        raise SettingsError("options per question must be 4 or 5")
    if body.get("answer_placement") not in ("book_end", "inline", "section_end"):
        raise SettingsError(f"unknown answer placement: {body.get('answer_placement')}")
    merge = None
    if len(pdfs) > 1:
        fixed = {"always": True, "never": False}.get(settings["merge_books"])
        merge = bool(body.get("merge")) if fixed is None else fixed
    notes, field = body.get("notes", ""), body.get("field", "")
    if not isinstance(notes, str) or len(notes) > 4000:
        raise SettingsError("notes must be text of at most 4000 characters")
    if not isinstance(field, str) or len(field) > 100:
        raise SettingsError("field must be text of at most 100 characters")
    mix = body.get("difficulty_mix", settings["difficulty_mix"])
    error = validate_value(next(x for x in load_schema()["settings"] if x["key"] == "difficulty_mix"), mix)
    if error:
        raise SettingsError(error)
    contents = {"always": True, "never": False}.get(settings["book_contents"], body.get("contents", True) is not False)
    base = default_team(settings)
    team = clean_team(body.get("team", base["team"]))
    effort = clean_effort(body.get("effort", base["effort"]))
    return {"pdfs": pdfs, "format": body["format"], "mcq_options": body["mcq_options"],
            "answer_placement": body["answer_placement"], "merge": merge,
            "notes": notes.strip(), "field": field.strip(), "difficulty_mix": mix, "contents": contents,
            "team": team, "effort": effort, "budget": clean_budget(body.get("budget"))}


def submit_job(job):
    write_atomic(intake_dir() / "job.json", job)


def cancel_job():
    write_atomic(intake_dir() / "cancel.json", {"t": now_iso()})


def ensure_studio():
    """URL of a running Studio, starting one in the background when needed."""
    sys.path.insert(0, str(ROOT / "studio"))
    import server
    port = server.find_studio(S["studio_port"])
    if port:
        return f"http://127.0.0.1:{port}"
    log = config_dir() / "studio.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    with open(log, "w", encoding="utf-8") as out:
        proc = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "studio", "--no-open"],
                                stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT,
                                start_new_session=True)
    for _ in range(STUDIO_START_TICKS):
        time.sleep(0.1)
        ended = proc.poll() is not None  # asked before the log is read, so its last line is not missed
        m = re.search(r"http://127\.0\.0\.1:(\d+)", log.read_text(**UTF8))
        if m and server.is_studio(int(m.group(1))):
            return m.group(0)
        if ended:
            break
    if proc.poll() is None:  # else a Studio could appear after the failure was reported
        proc.kill()
        proc.wait()
    lines = log.read_text(**UTF8).split("\n")
    last = next((x.strip() for x in reversed(lines) if x.strip()), "it did not answer")
    fail(f"Studio did not start: {re.sub('^ERROR: ', '', last)} (log: {log})")


def page_open(base):
    """True when a Studio page is already polling; it shows the new request by itself."""
    try:
        with LOCAL.open(base + "/api/ping", timeout=2) as r:
            return bool(json.load(r).get("watching"))
    except (OSError, ValueError):
        return False


def intake_open(a):
    box = intake_dir()
    clear_leftovers()
    skipped = []
    try:
        pdfs = expand_sources(a.pdf or [], skipped)
    except SettingsError as e:
        fail(str(e))
    write_atomic(box / "request.json", {"id": os.urandom(6).hex(), "t": now_iso(), "pdfs": pdfs,
                                        "notes": a.notes or ""})
    probe_in_background()
    base = ensure_studio()
    url = base + "/?tab=job"
    if S["studio_auto_open"] and not a.no_open and not page_open(base):
        open_window(url)
    print(json.dumps({"url": url, "skipped": skipped}))


def intake_wait(a):
    box = intake_dir()
    waiter, job_file, cancel = box / "waiter.json", box / "job.json", box / "cancel.json"
    clear_leftovers()
    deadline = time.time() + a.timeout

    def take_job():
        claimed = claim(job_file)
        if not claimed:
            return False
        print(claimed.read_text(encoding="utf-8"))
        remove_quiet(claimed)
        remove_quiet(box / "request.json")
        return True

    try:
        while time.time() < deadline:
            write_atomic(waiter, {"pid": os.getpid()})
            claimed = claim(cancel)
            if claimed:
                remove_quiet(claimed)
                print('{"cancelled": true}')
                sys.exit(5)
            if take_job():
                return
            time.sleep(ASK_POLL_SECONDS)
    finally:
        mark = read_quiet(waiter)
        if isinstance(mark, dict) and mark.get("pid") == os.getpid():  # with two waits, the mark may be the other's
            remove_quiet(waiter)
    if take_job():  # accepted just as the time ran out: the next wait would throw it away
        return
    print('{"timeout": true}')
    sys.exit(4)


def cmd_intake(a):
    (intake_open if a.action == "open" else intake_wait)(a)


def pending_ask(run):
    """The open question of a run, if the session is still waiting for its answer."""
    p = Path(run) / "ask.json"
    return read_quiet(p) if is_fresh(p) else None


def answer_ask(run, ask_id, value):
    ask = pending_ask(run)
    if not ask or ask["id"] != ask_id:
        raise SettingsError("this question is no longer open")
    if value not in [o["value"] for o in ask["options"]]:
        raise SettingsError(f"unknown answer: {value}")
    write_atomic(Path(run) / "answer.json", {"id": ask_id, "value": value})


def cmd_ask(a):
    """Ask the person watching the Studio a question and print the chosen value."""
    run = Path(a.run).expanduser().resolve()
    if not (run / "plan.json").is_file():
        fail(f"not a run folder: {run}")
    options = []
    for raw in a.option:
        value, sep, label = raw.partition("=")
        if not (sep and value and label):
            fail(f"option must look like value=Label: {raw}")
        options.append({"value": value, "label": label})
    ensure_studio()  # it may have closed during a long, quiet stretch of the job
    ask_file, answer_file = run / "ask.json", run / "answer.json"
    remove_quiet(answer_file)
    write_atomic(ask_file, {"id": a.id, "text": a.text, "options": options})
    emit(run, "ask", text=a.text)
    deadline = time.time() + a.timeout
    try:
        while time.time() < deadline:
            os.utime(ask_file)
            answer = read_quiet(answer_file)
            if answer and answer.get("id") == a.id:
                emit(run, "answer", value=answer["value"])
                print(answer["value"])
                return
            time.sleep(ASK_POLL_SECONDS)
    finally:
        remove_quiet(ask_file)
        remove_quiet(answer_file)
    print("timeout", file=sys.stderr)
    sys.exit(4)


# ---------- cli ----------
def utf8_output():
    """Claude Code reads UTF-8, but a Windows pipe defaults to the ANSI code page. There, print()
    garbles Turkish paths and crashes on any character the code page lacks."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
        except (AttributeError, ValueError):
            pass  # replaced by something that is not a text stream


def main():
    global S
    utf8_output()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("doctor"); p.add_argument("--json", action="store_true"); p.add_argument("--install")
    p.set_defaults(f=cmd_doctor)
    p = sub.add_parser("studio-url"); p.set_defaults(f=lambda a: print(ensure_studio() + "/?tab=job"))
    p = sub.add_parser("settings")
    p.set_defaults(f=lambda a: print(json.dumps(S, ensure_ascii=False, indent=2)))
    p = sub.add_parser("config")
    p.add_argument("action", choices=["list", "get", "set", "reset"])
    p.add_argument("key", nargs="?")
    p.add_argument("value", nargs="?")
    p.add_argument("--json", action="store_true")
    p.set_defaults(f=cmd_config)
    p = sub.add_parser("team")
    p.set_defaults(f=lambda a: print(json.dumps(resolve_team(S), indent=2)))
    p = sub.add_parser("pages"); p.add_argument("pdf"); p.set_defaults(f=cmd_pages)
    p = sub.add_parser("render-pages"); p.add_argument("pdf"); p.add_argument("range")
    p.add_argument("--out", required=True); p.add_argument("--dpi", type=int, default=110)
    p.set_defaults(f=cmd_render_pages)
    p = sub.add_parser("marks"); p.add_argument("pdf"); p.add_argument("range"); p.add_argument("--out")
    p.set_defaults(f=cmd_marks)
    p = sub.add_parser("plan"); p.add_argument("pdfs", nargs="+"); p.add_argument("--run")
    p.add_argument("--mix"); p.add_argument("--team"); p.add_argument("--effort"); p.add_argument("--budget")
    p.add_argument("--pages", action="append"); p.set_defaults(f=cmd_plan)
    p = sub.add_parser("check"); p.add_argument("json"); p.add_argument("--format", choices=list(FORMATS))
    p.add_argument("--expected", help="easy=2,medium=5,hard=3; separate alternatives with '|'")
    p.add_argument("--mcq-options", type=int, choices=[4, 5])
    p.add_argument("--min", type=int, help="thin-page floor from the plan (min_questions)")
    p.add_argument("--marks", help="marks file: also fail on a question that rests on struck text")
    p.set_defaults(f=cmd_check)
    p = sub.add_parser("marks-coverage"); p.add_argument("chunk"); p.add_argument("marks")
    p.add_argument("--fail-out", help="write failures here in the form approve reads")
    p.set_defaults(f=cmd_marks_coverage)
    p = sub.add_parser("screen"); p.add_argument("chunk"); p.add_argument("result"); p.set_defaults(f=cmd_screen)
    p = sub.add_parser("approve"); p.add_argument("chunk"); p.add_argument("result")
    p.add_argument("--audit", action="store_true"); p.set_defaults(f=cmd_approve)
    p = sub.add_parser("audit-sample"); p.add_argument("chunk"); p.add_argument("--rate", type=float)
    p.set_defaults(f=cmd_audit_sample)
    p = sub.add_parser("source-pages"); p.add_argument("chunk"); p.add_argument("--ids", required=True)
    p.set_defaults(f=cmd_source_pages)
    p = sub.add_parser("replace"); p.add_argument("chunk"); p.add_argument("fix"); p.set_defaults(f=cmd_replace)
    p = sub.add_parser("merge"); p.add_argument("dirs", nargs="+"); p.add_argument("--out", required=True)
    p.add_argument("--title", required=True); p.add_argument("--format", required=True, choices=list(FORMATS))
    p.add_argument("--part", action="append", help="file.pdf=Logical name")
    p.add_argument("--language", default="en", help="label language of the book (en or tr)")
    p.add_argument("--titles", choices=["logical", "filename"]); p.set_defaults(f=cmd_merge)
    p = sub.add_parser("render"); p.add_argument("json"); p.add_argument("out")
    p.add_argument("--no-contents", action="store_true")
    p.add_argument("--answers", choices=["book_end", "inline", "section_end"])
    p.add_argument("--html", help="also copy the intermediate HTML here"); p.set_defaults(f=cmd_render)
    p = sub.add_parser("event"); p.add_argument("run"); p.add_argument("type"); p.add_argument("data", nargs="*")
    p.set_defaults(f=cmd_event)
    p = sub.add_parser("cost"); p.add_argument("run"); p.add_argument("--record", action="store_true")
    p.add_argument("--budget-check", action="store_true"); p.set_defaults(f=cmd_cost)
    p = sub.add_parser("models"); p.add_argument("--probe", action="store_true"); p.set_defaults(f=cmd_models)
    p = sub.add_parser("studio"); p.add_argument("--port", type=int); p.add_argument("--no-open", action="store_true")
    p.set_defaults(f=cmd_studio)
    p = sub.add_parser("intake"); p.add_argument("action", choices=["open", "wait"])
    p.add_argument("--pdf", action="append"); p.add_argument("--notes")
    p.add_argument("--no-open", action="store_true"); p.add_argument("--timeout", type=int, default=7200)
    p.set_defaults(f=cmd_intake)
    p = sub.add_parser("ask"); p.add_argument("run"); p.add_argument("--id", required=True)
    p.add_argument("--text", required=True); p.add_argument("--option", action="append", required=True)
    p.add_argument("--timeout", type=int, default=7200); p.set_defaults(f=cmd_ask)
    a = ap.parse_args()
    try:
        S = load_settings()
        a.f(a)
    except SettingsError as e:
        fail(str(e))


if __name__ == "__main__":
    main()
