#!/usr/bin/env python3
"""exam-forge helper (Python standard library only).

Commands:
  settings                              Print effective settings as JSON
  config list|get|set|reset             Read or change user settings
  team                                  Print the resolved team and models
  pages <pdf>                           Page count
  render-pages <pdf> <3-12> --out DIR   Rasterize pages to PNG (pymupdf or pdftoppm)
  plan <pdf>... [--run DIR]             Question counts, difficulty split, chunks
       [--pages file.pdf=N ...]         give the page count when it cannot be read
  check <chunk.json> [--format F] [--expected easy=2,medium=5,hard=3]
       [--mcq-options 4|5] [--min N]    --min allows a justified thin-page reduction
  screen <chunk.json> <screen.json>     Record screener results
  audit-sample <chunk.json> [--rate R]  Pick questions for the chief editor
  source-pages <chunk.json> --ids a,b   Pages (with one page of margin) for those ids
  approve <chunk.json> <review.json>    Mark reviewer PASS results as verified
       [--audit]                        apply chief editor results instead
  replace <chunk.json> <fix.json>       Swap questions that have the same id
  merge <pdf_dir>... --out book.json --title T --format F
       [--part file.pdf=Name ...] [--language en] [--titles logical|filename]
  render <book.json> <out.pdf> [--answers book_end|inline|section_end] [--html F]
  event <run> <type> [key=value ...]    Append an event by hand (hook fallback)
  cost <run>                            Tokens and estimated cost from transcripts
  studio [--port N] [--no-open]         Start the local office view
  intake open [--pdf P ...] [--notes T]  Open the job form in the Studio
  intake wait [--timeout S]             Wait for a job from the form (exit 4 timeout, 5 cancelled)
  ask <run> --id I --text T --option v=Label ...  Ask in the Studio; prints the value

Settings: defaults in ../settings.json, then ~/.config/exam-forge/settings.json,
then the file named by EXAM_FORGE_SETTINGS. Only the keys present are overridden.
"""
import argparse
import datetime as dt
import html
import json
import math
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import time
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = ROOT / "templates"


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
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except Exception as e:
        raise SettingsError(f"could not read {p}: {e}")


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


def write_user_setting(key, value):
    """Validate and persist one setting. value=None removes the override."""
    p = user_settings_path()
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
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(user, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


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
                p.unlink()
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
    "google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "microsoft-edge",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
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
    try:
        runs = json.loads(p.read_text(encoding="utf-8")) if p.is_file() else []
    except ValueError:
        runs = []
    runs = [str(run)] + [r for r in runs if r != str(run)]
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(runs[:50], ensure_ascii=False), encoding="utf-8")


def cmd_event(a):
    data = {}
    for kv in a.data or []:
        k, _, v = kv.partition("=")
        data[k] = int(v) if v.isdigit() else v
    emit(Path(a.run).expanduser(), a.type, **data)


# ---------- pages ----------
def find_browser():
    for c in [os.environ.get("EXAM_FORGE_BROWSER"), S.get("browser")] + BROWSER_CANDIDATES:
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
    try:
        import fitz  # optional pymupdf
        doc = fitz.open(str(pdf))
        last = min(last, doc.page_count)
        for i in range(first, last + 1):
            path = target / f"{pdf.stem}-p{i:03d}.png"
            doc[i - 1].get_pixmap(dpi=a.dpi).save(str(path))
            print(path)
        return
    except ImportError:
        pass
    if shutil.which("pdftoppm"):
        r = subprocess.run(["pdftoppm", "-png", "-r", str(a.dpi), "-f", str(first), "-l", str(last),
                            str(pdf), str(target / f"{pdf.stem}-p")], capture_output=True, text=True)
        if r.returncode != 0:
            fail(f"pdftoppm failed: {r.stderr[-400:]}")
        for p in sorted(target.glob(f"{pdf.stem}-p*.png")):
            print(p)
        return
    print("ERROR: no page image tool found. Install one: 'pip install pymupdf' or "
          "'brew install poppler' (Linux: 'apt-get install poppler-utils').", file=sys.stderr)
    sys.exit(4)


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


def cmd_plan(a):
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
    result = {
        "created": dt.datetime.now().isoformat(timespec="minutes"),
        "run": str(run),
        "output_dir": str(output),
        # Read with 'pages' needs pdftoppm; without it agents read the whole PDF
        # or rasterize with render-pages.
        "read_pages_supported": bool(shutil.which("pdftoppm")),
        "team": team,
        "pdfs": plans,
    }
    text = json.dumps(result, ensure_ascii=False, indent=2)
    (run / "plan.json").write_text(text, encoding="utf-8")
    register_run(run)
    emit(run, "plan", team=team, preset=S["quality_preset"],
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
    return e


def parse_split(text):
    return {k: int(v) for k, v in (x.split("=") for x in text.split(","))}


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
    errors = []
    ids = [q.get("id") for q in questions]
    if len(set(ids)) != len(ids):
        errors.append("repeated id")
    texts = [str(q.get("question", "")).strip().lower() for q in questions]
    if len(set(texts)) != len(texts):
        errors.append("identical question text repeated")
    for q in questions:
        errors += question_errors(q, fmt, page_range, a.mcq_options or S["mcq_options"])
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


def build_book_html(book, placement):
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
    css = (TEMPLATES / "book.css").read_text(encoding="utf-8")
    page = (TEMPLATES / "book.html").read_text(encoding="utf-8")
    fills = {
        "LANG": L["lang"], "CSS": css, "TITLE": esc(book["title"]), "BOOK": L["book"],
        "SOURCE_LABEL": L["source"], "SOURCE": esc(", ".join(book.get("sources", []))),
        "FORMAT": L.get(f'fmt_{book.get("format")}', esc(book.get("format", ""))),
        "DATE": format_date(book.get("date", dt.date.today().isoformat()), L),
        "TOTAL": count_label(len(questions), L, "questions"), "SECTIONS": count_label(sec_no - 1, L, "sections"),
        "MIX_LABEL": L["difficulty_mix"], "BAR": bar, "LEGEND": legend, "CONTENTS": L["contents"],
        "TOC": "".join(toc), "GUIDE": L[f"guide_{placement}"], "BODY": "".join(body),
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
    page, total, sections = build_book_html(book, placement)
    out = Path(a.out).expanduser().resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        stamp = dt.datetime.fromtimestamp(out.stat().st_mtime).strftime("%Y-%m-%d-%H%M")
        backup = out.with_name(f"{out.stem}-{stamp}.pdf")
        out.rename(backup)
        print(f"previous book kept: {backup.name}")
    run = find_run(a.json)
    with tempfile.TemporaryDirectory() as td:
        h = Path(td) / "book.html"
        h.write_text(page, encoding="utf-8")
        if a.html:
            shutil.copy(h, Path(a.html).expanduser())
        browser = find_browser()
        if not browser:
            fallback = out.with_suffix(".html")
            shutil.copy(h, fallback)
            emit(run, "render", questions=total, out=str(fallback), html_only=True)
            print(f"ERROR: Chrome, Chromium or Edge not found. Book written as HTML: {fallback}\n"
                  "Open it in a browser and print to PDF, or set EXAM_FORGE_BROWSER to a browser path.",
                  file=sys.stderr)
            sys.exit(3)
        r = subprocess.run([browser, "--headless=new", "--disable-gpu", "--no-pdf-header-footer",
                            f"--user-data-dir={td}/profile", "--no-first-run",
                            f"--print-to-pdf={out}", h.as_uri()],
                           capture_output=True, text=True, timeout=180)
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
    print(json.dumps(run_cost(Path(a.run).expanduser(), S["pricing"]), indent=2))


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


def intake_dir():
    return config_dir() / "intake"


def write_atomic(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


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


def session_waiting():
    return is_fresh(intake_dir() / "waiter.json")


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


def pdfs_under(folder):
    """PDFs in a folder and its subfolders, skipping hidden entries."""
    found = []
    for base, dirs, files in os.walk(folder):
        dirs[:] = sorted(d for d in dirs if not d.startswith("."))
        found += [Path(base) / f for f in sorted(files)
                  if f.lower().endswith(".pdf") and not f.startswith(".")]
        if len(found) > MAX_FOLDER_PDFS:
            raise SettingsError(f"more than {MAX_FOLDER_PDFS} PDFs in {folder.name}; choose a smaller folder")
    return found


def expand_sources(paths):
    """Files and folders the user chose, as a flat list of PDF paths."""
    out = []
    for raw in paths:
        p = Path(str(raw).strip()).expanduser().resolve()
        if p.is_dir():
            inside = pdfs_under(p)
            if not inside:
                raise SettingsError(f"no PDFs in {p.name}")
            out += [validate_pdf(str(f)) for f in inside]
        else:
            out.append(validate_pdf(str(p)))
    return list(dict.fromkeys(out))


def list_folder(raw):
    """Folders and PDFs for the Studio's file browser. Other files stay out of it."""
    p = Path(str(raw or "~").strip() or "~").expanduser().resolve()
    file = None
    if p.is_file():
        file, p = validate_pdf(str(p)), p.parent
    if not p.is_dir():
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


def places():
    home = Path.home()
    spots = [("home", home), ("desktop", home / "Desktop"), ("documents", home / "Documents"),
             ("downloads", home / "Downloads"), ("volumes", Path("/Volumes"))]
    return [{"key": k, "path": str(p)} for k, p in spots if p.is_dir()]


def upload_target(batch, name):
    """Where a dropped PDF goes. A browser hides its real path, so it needs a copy."""
    if not re.fullmatch(r"[0-9]{1,20}", batch or ""):
        raise SettingsError("bad upload batch")
    clean = re.sub(r'[\x00-\x1f/\\:*?"<>|]', "_", Path(str(name).replace("\\", "/")).name).strip()
    if clean.startswith(".") or not clean.lower().endswith(".pdf"):
        raise SettingsError(f"not a PDF: {clean}")
    folder = Path.home() / "Exam Forge" / batch
    folder.mkdir(parents=True, exist_ok=True)
    target, n = folder / clean, 1
    while target.exists():
        n += 1
        target = folder / f"{Path(clean).stem}-{n}.pdf"
    return target


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
    return {"pdfs": pdfs, "format": body["format"], "mcq_options": body["mcq_options"],
            "answer_placement": body["answer_placement"], "merge": merge,
            "notes": notes.strip(), "field": field.strip()}


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
        subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "studio", "--no-open"],
                         stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT,
                         start_new_session=True)
    for _ in range(50):
        time.sleep(0.1)
        m = re.search(r"http://127\.0\.0\.1:(\d+)", log.read_text(encoding="utf-8"))
        if m and server.is_studio(int(m.group(1))):
            return m.group(0)
    fail(f"Studio did not start; see {log}")


def intake_open(a):
    box = intake_dir()
    for name in ("job.json", "cancel.json"):
        remove_quiet(box / name)
    try:
        pdfs = expand_sources(a.pdf or [])
    except SettingsError as e:
        fail(str(e))
    write_atomic(box / "request.json", {"id": os.urandom(6).hex(), "t": now_iso(), "pdfs": pdfs,
                                        "notes": a.notes or ""})
    url = ensure_studio() + "/?tab=job"
    if S["studio_auto_open"] and not a.no_open:
        webbrowser.open(url)
    print(json.dumps({"url": url}))


def intake_wait(a):
    box = intake_dir()
    waiter, job_file, cancel = box / "waiter.json", box / "job.json", box / "cancel.json"
    deadline = time.time() + a.timeout
    try:
        while time.time() < deadline:
            write_atomic(waiter, {"pid": os.getpid()})
            if cancel.exists():
                remove_quiet(cancel)
                print('{"cancelled": true}')
                sys.exit(5)
            claimed = box / f".claimed.{os.getpid()}"
            try:
                os.replace(job_file, claimed)
            except OSError:
                time.sleep(ASK_POLL_SECONDS)
                continue
            print(claimed.read_text(encoding="utf-8"))
            remove_quiet(claimed)
            remove_quiet(box / "request.json")
            return
    finally:
        remove_quiet(waiter)
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
def main():
    global S
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
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
    p = sub.add_parser("plan"); p.add_argument("pdfs", nargs="+"); p.add_argument("--run")
    p.add_argument("--pages", action="append"); p.set_defaults(f=cmd_plan)
    p = sub.add_parser("check"); p.add_argument("json"); p.add_argument("--format", choices=list(FORMATS))
    p.add_argument("--expected", help="easy=2,medium=5,hard=3; separate alternatives with '|'")
    p.add_argument("--mcq-options", type=int, choices=[4, 5])
    p.add_argument("--min", type=int, help="thin-page floor from the plan (min_questions)")
    p.set_defaults(f=cmd_check)
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
    p.add_argument("--answers", choices=["book_end", "inline", "section_end"])
    p.add_argument("--html", help="also copy the intermediate HTML here"); p.set_defaults(f=cmd_render)
    p = sub.add_parser("event"); p.add_argument("run"); p.add_argument("type"); p.add_argument("data", nargs="*")
    p.set_defaults(f=cmd_event)
    p = sub.add_parser("cost"); p.add_argument("run"); p.set_defaults(f=cmd_cost)
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
