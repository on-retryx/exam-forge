"""Tests for forge.py (standard library only).

Run: python3 -m unittest discover -s tests
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import conftest  # noqa: F401

ROOT = Path(__file__).resolve().parent.parent
FORGE = ROOT / "skills" / "exam-forge" / "bin" / "forge.py"


def run(*args, env=None):
    e = dict(os.environ)
    e.pop("EXAM_FORGE_SETTINGS", None)
    if env:
        e.update(env)
    return subprocess.run([sys.executable, str(FORGE), *map(str, args)],
                          capture_output=True, text=True, env=e)


def question(i, difficulty="medium", type_="mcq", page=2, chunk=1, options=5, **extra):
    q = {
        "id": f"p{chunk}-q{i:02d}",
        "section": "Cells",
        "type": type_,
        "difficulty": difficulty,
        "question": f"What is function number {i} of the mitochondrion?",
        "answer": "A",
        "explanation": "Explanation.",
        "source": {"pdf": "lesson.pdf", "section": "Cells", "page": page},
        "quote": "Mitochondria produce energy.",
    }
    if type_ == "mcq":
        q["options"] = {h: f"option {h}{i}" for h in "ABCDE"[:options]}
    elif type_ == "true_false":
        q["answer"] = "True"
    elif type_ == "blank":
        q["question"] = f"Energy is produced in the ____ ({i})."
        q["answer"] = "mitochondrion"
    elif type_ == "open":
        q["answer"] = "Energy production."
    q.update(extra)
    return q


def chunk(questions, **extra):
    v = {"source": "lesson.pdf", "chunk": 1, "format": "mcq",
         "page_start": 1, "page_end": 20, "questions": questions}
    v.update(extra)
    return v


class Base(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.d = Path(self.td.name)
        self.env = {"HOME": str(self.d)}

    def tearDown(self):
        self.td.cleanup()

    def write(self, name, data):
        p = self.d / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return p

    def f(self, *args):
        return run(*args, env=self.env)


class Settings(Base):
    def test_defaults(self):
        s = json.loads(self.f("settings").stdout)
        self.assertEqual(s["mcq_options"], 5)
        self.assertEqual(s["answer_placement"], "book_end")
        self.assertEqual(s["merge_books"], "ask")
        self.assertEqual(s["section_titles"], "logical")
        self.assertTrue(s["studio_speech_bubbles"])

    def test_user_override(self):
        cfg = self.d / ".config" / "exam-forge"
        cfg.mkdir(parents=True)
        (cfg / "settings.json").write_text('{"mcq_options": 4, "models": {"editor": "opus"}}')
        s = json.loads(self.f("settings").stdout)
        self.assertEqual(s["mcq_options"], 4)
        self.assertEqual(json.loads(self.f("team").stdout)["editor"], "opus")

    def test_invalid_mix(self):
        p = self.write("a.json", {"difficulty_mix": {"easy": 0.5, "medium": 0.5, "hard": 0.5}})
        self.env["EXAM_FORGE_SETTINGS"] = str(p)
        self.assertEqual(self.f("settings").returncode, 2)

    def test_presets(self):
        team = json.loads(self.f("team").stdout)
        self.assertEqual((team["reviewer"], team["chief_editor"], team["screener"]), ("opus", "", "haiku"))
        self.f("config", "set", "quality_preset", "balanced")
        team = json.loads(self.f("team").stdout)
        self.assertEqual((team["reviewer"], team["chief_editor"]), ("sonnet", "opus"))
        self.f("config", "set", "screener_enabled", "false")
        self.assertEqual(json.loads(self.f("team").stdout)["screener"], "")


class Config(Base):
    def test_set_get_reset(self):
        self.assertEqual(self.f("config", "set", "answer_placement", "inline").returncode, 0)
        self.assertEqual(json.loads(self.f("config", "get", "answer_placement").stdout), "inline")
        self.f("config", "reset", "answer_placement")
        self.assertEqual(json.loads(self.f("config", "get", "answer_placement").stdout), "book_end")

    def test_rejects_bad_values(self):
        self.assertEqual(self.f("config", "set", "answer_placement", "nowhere").returncode, 2)
        self.assertEqual(self.f("config", "set", "difficulty_mix", "0.5,0.5,0.5").returncode, 2)
        self.assertEqual(self.f("config", "set", "no_such_key", "1").returncode, 2)
        self.assertEqual(self.f("config", "set", "mcq_options", "7").returncode, 2)

    def test_typed_values(self):
        self.f("config", "set", "difficulty_mix", "0.2,0.5,0.3")
        self.assertEqual(json.loads(self.f("config", "get", "difficulty_mix").stdout)["hard"], 0.3)
        self.f("config", "set", "short_pdf_question_options", "8,12")
        self.assertEqual(json.loads(self.f("config", "get", "short_pdf_question_options").stdout), [8, 12])
        self.f("config", "set", "mcq_options", "4")
        self.assertEqual(json.loads(self.f("config", "get", "mcq_options").stdout), 4)

    def test_list_json_has_schema(self):
        d = json.loads(self.f("config", "list", "--json").stdout)
        keys = {s["key"] for s in d["schema"]["settings"]}
        self.assertIn("section_titles", keys)
        self.assertIn("tr", d["schema"]["settings"][0]["label"])


class Plan(Base):
    def plan(self, pages):
        pdf = self.d / "lesson.pdf"
        pdf.write_bytes(b"%PDF-1.4\n")
        r = self.f("plan", pdf, "--pages", f"lesson.pdf={pages}", "--run", self.d / "run")
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def test_short_pdf_two_options(self):
        p = self.plan(12)["pdfs"][0]
        self.assertEqual(p["question_options"], [10, 15])
        self.assertEqual(p["split"]["10"], {"easy": 2, "medium": 5, "hard": 3})
        self.assertEqual(len(p["chunks"]), 1)
        self.assertEqual(p["chunks"][0]["min_questions"], 6)

    def test_long_pdf_splits_and_adds_up(self):
        p = self.plan(47)["pdfs"][0]
        self.assertEqual(p["question_options"], [47])
        self.assertEqual(len(p["chunks"]), 3)
        self.assertEqual(p["chunks"][-1]["page_end"], 47)
        self.assertEqual(sum(sum(c["split_options"]["47"].values()) for c in p["chunks"]), 47)

    def test_boundary_25_is_one_chunk(self):
        self.assertEqual(len(self.plan(25)["pdfs"][0]["chunks"]), 1)

    def test_writes_run_folder_and_event(self):
        r = self.plan(12)
        run_dir = Path(r["run"])
        self.assertTrue((run_dir / "plan.json").is_file())
        self.assertTrue((run_dir / "lesson").is_dir())
        ev = json.loads((run_dir / "events.jsonl").read_text().splitlines()[0])
        self.assertEqual(ev["type"], "plan")
        self.assertEqual(ev["pdfs"][0]["chunks"], 1)
        self.assertEqual(ev["team"]["screener"], "haiku")
        runs = json.loads((self.d / ".config/exam-forge/runs.json").read_text())
        self.assertEqual(runs[0], str(run_dir))


class Check(Base):
    def test_valid_chunk(self):
        p = self.write("p.json", chunk([question(1, "easy"), question(2, "medium"), question(3, "hard")]))
        r = self.f("check", p, "--format", "mcq", "--expected", "easy=1,medium=1,hard=1")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("CHECK OK", r.stdout)

    def test_english_reference_caught(self):
        for text in ["According to the slides, what is ATP?", "What does the value given for oceans mean?",
                     "As stated above, what is ATP?", "What does this chapter say about ATP?"]:
            p = self.write("p.json", chunk([question(1, question=text)]))
            r = self.f("check", p)
            self.assertEqual(r.returncode, 1, text)
            self.assertIn("reference", r.stdout)

    def test_turkish_reference_caught(self):
        for text in ["Slayta göre mitokondri nedir?", "Okyanuslar için verilen değer neyi ifade eder?"]:
            p = self.write("p.json", chunk([question(1, question=text)]))
            self.assertEqual(self.f("check", p).returncode, 1, text)

    def test_legitimate_stems_pass(self):
        for text in ["Which of the following is correct?", "Aşağıda verilenlerden hangisi doğrudur?"]:
            p = self.write("p.json", chunk([question(1, question=text)]))
            self.assertEqual(self.f("check", p).returncode, 0, text)

    def test_option_count_from_settings(self):
        p = self.write("p.json", chunk([question(1, options=4)]))
        self.assertEqual(self.f("check", p).returncode, 1)
        self.assertEqual(self.f("check", p, "--mcq-options", "4").returncode, 0)

    def test_type_outside_format(self):
        p = self.write("p.json", chunk([question(1, type_="open")]))
        self.assertEqual(self.f("check", p, "--format", "mcq").returncode, 1)

    def test_page_outside_range(self):
        p = self.write("p.json", chunk([question(1, page=40)]))
        self.assertEqual(self.f("check", p).returncode, 1)

    def test_blank_and_true_false(self):
        qs = [question(1, type_="blank"), question(2, type_="true_false"), question(3, type_="open")]
        p = self.write("p.json", chunk(qs, format="mixed"))
        self.assertEqual(self.f("check", p, "--format", "mixed").returncode, 0)
        qs[1]["answer"] = "Yes"
        p = self.write("p.json", chunk(qs, format="mixed"))
        self.assertEqual(self.f("check", p, "--format", "mixed").returncode, 1)

    def test_thin_page_reduction(self):
        levels = ["easy"] * 2 + ["medium"] * 4 + ["hard"] * 2
        qs = [question(i + 1, z) for i, z in enumerate(levels)]
        exp = "easy=2,medium=5,hard=3"
        p = self.write("p.json", chunk(qs))
        self.assertEqual(self.f("check", p, "--expected", exp, "--min", "6").returncode, 1)
        p = self.write("p.json", chunk(qs, reduction_reason="pp. 3-9 are title slides"))
        r = self.f("check", p, "--expected", exp, "--min", "6")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("WARNING", r.stdout)
        self.assertEqual(self.f("check", p, "--expected", exp, "--min", "9").returncode, 1)
        self.assertEqual(self.f("check", p, "--expected", exp).returncode, 1)


class Flow(Base):
    def setup_run(self, questions):
        (self.d / "run").mkdir()
        (self.d / "run" / "plan.json").write_text("{}")
        return self.write("run/lesson/chunk-1.json", chunk(questions))

    def events(self):
        p = self.d / "run" / "events.jsonl"
        return [json.loads(x) for x in p.read_text().splitlines()]

    def merge(self, *extra):
        return self.f("merge", self.d / "run/lesson", "--out", self.d / "run/book.json",
                      "--title", "B", "--format", "mcq", *extra)

    def test_screen_approve_replace_merge(self):
        p = self.setup_run([question(1, "easy"), question(2, "medium")])
        sc = self.write("run/lesson/screen-1-round1.json", {"results": [
            {"id": "p1-q01", "status": "PASS"},
            {"id": "p1-q02", "status": "FAIL", "reason": "giveaway", "feedback": "B is longest"}]})
        r = self.f("screen", p, sc)
        self.assertEqual(r.returncode, 1)
        self.assertEqual(json.loads(r.stdout)["remaining"][0]["id"], "p1-q02")
        self.assertFalse(json.loads(p.read_text())["questions"][0].get("verified"))

        rv = self.write("run/lesson/review-1-round1.json", {"results": [
            {"id": "p1-q01", "status": "PASS"},
            {"id": "p1-q02", "status": "FAIL", "reason": "multi_correct", "feedback": "B also correct"}]})
        self.assertEqual(self.f("approve", p, rv).returncode, 1)
        self.assertEqual(self.merge().returncode, 2, "unverified questions must block merge")

        bad = self.write("run/lesson/fix-bad.json", {"questions": [question(2, "hard")]})
        self.assertEqual(self.f("replace", p, bad).returncode, 2, "difficulty cannot change")
        locked = self.write("run/lesson/fix-lock.json", {"questions": [question(1, "easy")]})
        self.assertEqual(self.f("replace", p, locked).returncode, 2, "verified cannot change")

        new = self.write("run/lesson/fix-1-round1.json",
                         {"questions": [question(2, "medium", question="What is new here?")]})
        self.assertEqual(self.f("replace", p, new).returncode, 0)
        rv2 = self.write("run/lesson/review-1-round2.json", {"results": [{"id": "p1-q02", "status": "PASS"}]})
        self.assertEqual(self.f("approve", p, rv2).returncode, 0)
        self.assertEqual(json.loads(p.read_text())["questions"][1]["round"], 2)

        r = self.merge()
        self.assertEqual(r.returncode, 0, r.stderr)
        book = json.loads((self.d / "run/book.json").read_text())
        self.assertEqual(len(book["parts"][0]["questions"]), 2)
        kinds = [e["type"] for e in self.events()]
        for k in ("screen", "review", "replace", "merge"):
            self.assertIn(k, kinds)
        review = next(e for e in self.events() if e["type"] == "review")
        self.assertEqual(review["failed"][0]["reason"], "multi_correct")

    def test_audit_gate(self):
        qs = [question(i, z) for i, z in enumerate(["easy", "medium", "medium", "hard"], 1)]
        p = self.setup_run(qs)
        rv = self.write("run/lesson/review-1-round1.json",
                        {"results": [{"id": q["id"], "status": "PASS"} for q in qs]})
        self.assertEqual(self.f("approve", p, rv).returncode, 0)
        ids = self.f("audit-sample", p, "--rate", "0.5").stdout.strip().split(",")
        self.assertIn("p1-q04", ids, "hard questions are always audited")
        self.assertEqual(len(ids), 3)
        self.assertEqual(self.merge().returncode, 2, "pending audit must block merge")
        au = self.write("run/lesson/audit-1-round1.json", {"results": [
            {"id": ids[0], "status": "FAIL", "reason": "wrong_answer", "feedback": "x"}]
            + [{"id": x, "status": "PASS"} for x in ids[1:]]})
        self.assertEqual(self.f("approve", p, au, "--audit").returncode, 1)
        self.assertEqual(self.merge().returncode, 2, "an audit FAIL un-verifies the question")
        d = json.loads(p.read_text())
        self.assertFalse(next(q for q in d["questions"] if q["id"] == ids[0])["verified"])

    def test_source_pages_for_correction_round(self):
        p = self.setup_run([question(1, page=5), question(2, page=6), question(3, page=12), question(4, page=1)])
        r = self.f("source-pages", p, "--ids", "p1-q01,p1-q02,p1-q03,p1-q04")
        self.assertEqual(r.stdout.strip(), "1-2,4-7,11-13")

    def test_filename_titles(self):
        p = self.setup_run([question(1, "easy", verified=True)])
        self.assertEqual(self.merge("--titles", "filename").returncode, 0)
        self.assertEqual(json.loads((self.d / "run/book.json").read_text())["title"], "lesson")
        self.assertEqual(self.merge("--part", "lesson.pdf=Cell Biology").returncode, 0)
        book = json.loads((self.d / "run/book.json").read_text())
        self.assertEqual(book["parts"][0]["name"], "Cell Biology")
        self.assertEqual(book["title"], "B")


class Book(Base):
    def book(self, n_parts=2, language="en", qtype="mcq"):
        parts = []
        for pi in range(1, n_parts + 1):
            qs = [question(1, "easy", chunk=pi, verified=True, type_=qtype),
                  question(2, "hard", chunk=pi, verified=True, type_=qtype)]
            parts.append({"name": f"Part {pi} name", "pdf": f"lesson{pi}.pdf", "questions": qs})
        return {"title": "Demo", "format": "mcq" if qtype == "mcq" else "mixed", "language": language,
                "date": "2026-11-06", "sources": [p["pdf"] for p in parts], "parts": parts}

    def html(self, book, answers):
        src = self.write("book.json", book)
        out = self.d / "out.html"
        code = ("import sys,json;sys.path.insert(0,sys.argv[1]);import forge;"
                "forge.S=forge.load_settings();"
                "print(forge.build_book_html(json.load(open(sys.argv[2])),sys.argv[3])[0])")
        r = subprocess.run([sys.executable, "-c", code, str(FORGE.parent), str(src), answers],
                           capture_output=True, text=True, env={**os.environ, **self.env})
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    def test_book_end_key_comes_after_questions(self):
        h = self.html(self.book(), "book_end")
        self.assertEqual(h.count('class="key-title"'), 1)
        self.assertGreater(h.index('class="key-title"'), h.rindex('class="question"'))
        self.assertNotIn('class="inline-answer"', h)

    def test_inline_answers(self):
        h = self.html(self.book(), "inline")
        self.assertEqual(h.count('class="inline-answer"'), 4)
        self.assertNotIn('class="key-title"', h)
        self.assertNotIn('class="key"', h)

    def test_section_end_key(self):
        h = self.html(self.book(n_parts=1), "section_end")
        self.assertEqual(h.count('<section class="key">'), 1)
        self.assertLess(h.index('class="question"'), h.index('<section class="key">'))

    def test_part_covers_and_continuous_numbers(self):
        h = self.html(self.book(n_parts=2), "book_end")
        self.assertEqual(h.count('class="part-cover"'), 2)
        self.assertIn("Part 2 name", h)
        self.assertIn('<span class="no">4</span>', h)
        single = self.html(self.book(n_parts=1), "book_end")
        self.assertNotIn('class="part-cover"', single)

    def test_labels_follow_language(self):
        en = self.html(self.book(), "book_end")
        tr = self.html(self.book(language="tr"), "book_end")
        self.assertIn("Answer Key", en)
        self.assertIn("November 6, 2026", en)
        self.assertIn("Cevap Anahtarı", tr)
        self.assertIn("6 Kasım 2026", tr)
        self.assertNotIn("Answer Key", tr)

    def test_singular_labels(self):
        b = self.book(n_parts=1)
        b["parts"][0]["questions"] = b["parts"][0]["questions"][:1]
        h = self.html(b, "book_end")
        self.assertIn("<small>1 question</small>", h)
        self.assertIn('toc-range">Question 1<', h)

    def test_true_false_label_is_translated(self):
        h = self.html(self.book(n_parts=1, language="tr", qtype="true_false"), "inline")
        self.assertIn('<p class="correct">Doğru</p>', h)

    def test_render_rejects_unverified(self):
        b = self.book()
        b["parts"][0]["questions"][0]["verified"] = False
        p = self.write("book.json", b)
        self.assertEqual(self.f("render", p, self.d / "x.pdf").returncode, 2)

    def test_render_without_browser_writes_html(self):
        if sys.platform == "darwin" and any(Path(p).exists() for p in [
                "/Applications/Google Chrome.app", "/Applications/Chromium.app",
                "/Applications/Microsoft Edge.app"]):
            self.skipTest("a browser is installed; the HTML fallback cannot run here")
        self.env["PATH"] = "/nonexistent"
        p = self.write("book.json", self.book())
        self.assertEqual(self.f("render", p, self.d / "demo-questions.pdf").returncode, 3)
        self.assertTrue((self.d / "demo-questions.html").exists())


if __name__ == "__main__":
    unittest.main()
