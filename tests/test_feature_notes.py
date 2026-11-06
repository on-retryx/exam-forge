"""Handwritten notes as a source: `forge.py notes`, the source fields, the book and the setting."""
import argparse
import contextlib
import importlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import conftest  # noqa: F401

ROOT = Path(__file__).resolve().parent.parent
BIN = ROOT / "skills" / "exam-forge" / "bin"
FORGE = BIN / "forge.py"
sys.path.insert(0, str(BIN))
import forge  # noqa: E402

try:
    pymupdf = importlib.import_module("pymupdf")
except Exception:
    pymupdf = None

RED = (1, 0, 0)
STROKE = [[(100, 100), (150, 120), (200, 110)]]


def read(*parts):
    return ROOT.joinpath(*parts).read_text(encoding="utf-8")


def question(i=1, page=2, kind=None, note=None, quote="Mitochondria produce energy.", **extra):
    q = {
        "id": f"p1-q{i:02d}", "section": "Cells", "type": "open", "difficulty": "medium",
        "question": f"What does organelle number {i} do?", "answer": "Energy production.",
        "explanation": "Explanation.", "quote": quote,
        "source": {"pdf": "lesson.pdf", "section": "Cells", "page": page},
    }
    if kind:
        q["source"]["kind"] = kind
    if note is not None:
        q["source"]["note"] = note
    q.update(extra)
    return q


def chunk(questions, **extra):
    return {"source": "lesson.pdf", "chunk": 1, "format": "open", "page_start": 1, "page_end": 20,
            "questions": questions, **extra}


class Base(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.d = Path(self.td.name)
        self.env = {"HOME": str(self.d)}

    def tearDown(self):
        self.td.cleanup()

    def cli(self, *args, **env):
        e = {**os.environ, **self.env, **env}
        e.pop("EXAM_FORGE_SETTINGS", None)
        return subprocess.run([sys.executable, str(FORGE), *map(str, args)], capture_output=True, text=True,
                              encoding="utf-8", env=e)

    def write(self, name, data):
        p = self.d / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return p

    def setting_off(self):
        self.write(".config/exam-forge/settings.json", {"handwritten_notes": False})


def ink(page, points=None, color=RED):
    annot = page.add_ink_annot(points or STROKE)
    annot.set_colors(stroke=color)
    annot.set_border(width=3)
    annot.update()
    return annot


@unittest.skipUnless(pymupdf, "needs pymupdf")
class Notes(Base):
    def pdf(self, build, pages=3, name="lesson.pdf", **save):
        doc = pymupdf.open()
        for _ in range(pages):
            doc.new_page(width=600, height=800)
        build(doc)
        path = self.d / name
        doc.save(path, **save)
        doc.close()
        return path

    def notes(self, path, span="1-3", **env):
        r = self.cli("notes", path, span, "--out", self.d / "crops", **env)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def test_freetext_returns_its_text_directly(self):
        path = self.pdf(lambda d: d[0].add_freetext_annot((100, 300, 300, 350), "ATP comes from glycolysis"))
        n = self.notes(path)["notes"]
        self.assertEqual(len(n), 1)
        self.assertEqual(n[0]["id"], "note-p1-1")
        self.assertEqual((n[0]["page"], n[0]["kind"], n[0]["text"]), (1, "freetext", "ATP comes from glycolysis"))
        self.assertEqual(n[0]["bbox"], [100, 300, 300, 350])
        self.assertNotIn("file", n[0])

    def test_sticky_note_returns_its_text_directly(self):
        path = self.pdf(lambda d: d[1].add_text_annot((300, 300), "See the renal section"))
        n = self.notes(path)["notes"][0]
        self.assertEqual((n["id"], n["page"], n["kind"], n["text"]), ("note-p2-1", 2, "text", "See the renal section"))
        self.assertNotIn("file", n)

    def test_ink_returns_a_png_crop_for_a_model_to_read(self):
        path = self.pdf(lambda d: ink(d[2]))
        n = self.notes(path)["notes"][0]
        self.assertEqual((n["id"], n["page"], n["kind"]), ("note-p3-1", 3, "ink"))
        self.assertNotIn("text", n)
        self.assertEqual(len(n["bbox"]), 4)
        crop = Path(n["file"])
        self.assertTrue(crop.is_absolute())
        self.assertEqual(crop.parent, (self.d / "crops").resolve())
        self.assertEqual(crop.read_bytes()[:8], b"\x89PNG\r\n\x1a\n")

    def test_crop_has_a_margin_around_the_ink(self):
        path = self.pdf(lambda d: ink(d[0]))
        n = self.notes(path)["notes"][0]
        x0, y0, x1, y1 = n["bbox"]
        pix = pymupdf.Pixmap(n["file"])
        dpi = 200
        self.assertGreater(pix.width, (x1 - x0 + 10) * dpi / 72)
        self.assertGreater(pix.height, (y1 - y0 + 10) * dpi / 72)

    def test_crop_stops_at_the_page_edge(self):
        path = self.pdf(lambda d: ink(d[0], [[(1, 1), (30, 12)]]))
        n = self.notes(path)["notes"][0]
        pix = pymupdf.Pixmap(n["file"])
        self.assertLess(pix.width, (30 + 40) * 200 / 72)
        self.assertLess(pix.height, (12 + 40) * 200 / 72)

    def test_crop_dpi_can_be_changed(self):
        path = self.pdf(lambda d: ink(d[0]))
        one = pymupdf.Pixmap(self.notes(path)["notes"][0]["file"]).width
        r = self.cli("notes", path, "1", "--out", self.d / "low", "--dpi", 100)
        self.assertEqual(r.returncode, 0, r.stderr)
        low = pymupdf.Pixmap(json.loads(r.stdout)["notes"][0]["file"]).width
        self.assertLess(low, one * 0.6)

    def test_crop_on_a_rotated_page_shows_the_ink(self):
        for angle in (90, 180, 270):
            with self.subTest(angle=angle):
                def build(d):
                    ink(d[0])
                    d[0].set_rotation(angle)
                path = self.pdf(build, name=f"turned{angle}.pdf")
                n = self.notes(path)["notes"][0]
                pix = pymupdf.Pixmap(n["file"])
                s, step = pix.samples, pix.n
                red = sum(1 for i in range(0, len(s), step) if s[i] > 200 and s[i + 1] < 100)
                self.assertGreater(red, 100)

    def test_ids_number_the_notes_of_each_page_from_the_top(self):
        def build(d):
            d[0].add_freetext_annot((100, 500, 300, 550), "lower")
            d[0].add_text_annot((300, 100), "upper")
            ink(d[1])
        n = self.notes(self.pdf(build))["notes"]
        self.assertEqual([x["id"] for x in n], ["note-p1-1", "note-p1-2", "note-p2-1"])
        self.assertEqual(n[0]["text"], "upper")

    def test_an_id_does_not_change_with_the_range_asked_for(self):
        def build(d):
            d[1].add_text_annot((300, 100), "a")
            d[1].add_text_annot((300, 300), "b")
        path = self.pdf(build)
        self.assertEqual(self.notes(path, "1-3")["notes"], self.notes(path, "2")["notes"])

    def test_only_the_range_is_read(self):
        def build(d):
            d[0].add_text_annot((300, 100), "out")
            d[1].add_text_annot((300, 100), "in")
            d[2].add_text_annot((300, 100), "out")
        d = self.notes(self.pdf(build), "2-2")
        self.assertEqual([x["text"] for x in d["notes"]], ["in"])
        self.assertEqual(d["pages_with_notes"], [2])

    def test_pages_with_notes_lists_each_page_once_in_order(self):
        def build(d):
            d[2].add_text_annot((300, 100), "a")
            d[0].add_text_annot((300, 100), "b")
            d[0].add_text_annot((300, 200), "c")
        self.assertEqual(self.notes(self.pdf(build))["pages_with_notes"], [1, 3])

    def test_a_range_past_the_end_stops_at_the_last_page(self):
        d = self.notes(self.pdf(lambda d: d[2].add_text_annot((300, 100), "a")), "2-99")
        self.assertEqual(d["pages_with_notes"], [3])

    def test_a_pdf_without_notes_gives_an_empty_result(self):
        self.assertEqual(self.notes(self.pdf(lambda d: None)), {"notes": [], "pages_with_notes": []})

    def test_marks_are_left_to_the_marks_command(self):
        def build(d):
            d[0].insert_text((100, 100), "Printed line")
            d[0].add_highlight_annot(pymupdf.Rect(100, 90, 200, 105))
            d[0].add_underline_annot(pymupdf.Rect(100, 90, 200, 105))
            d[0].add_strikeout_annot(pymupdf.Rect(100, 90, 200, 105))
        self.assertEqual(self.notes(self.pdf(build))["notes"], [])

    def test_handwriting_drawn_into_the_page_has_no_annotation(self):
        path = self.pdf(lambda d: d[0].draw_line((100, 100), (200, 110)))
        self.assertEqual(self.notes(path)["notes"], [])

    def test_a_note_with_no_text_is_not_a_note(self):
        path = self.pdf(lambda d: d[0].add_text_annot((300, 100), "   "))
        self.assertEqual(self.notes(path), {"notes": [], "pages_with_notes": []})

    def test_a_hidden_annotation_is_not_a_note(self):
        def build(d):
            a = ink(d[0])
            a.set_flags(pymupdf.PDF_ANNOT_IS_HIDDEN)
            a.update()
        self.assertEqual(self.notes(self.pdf(build))["notes"], [])

    def test_strokes_saved_one_by_one_stay_one_note(self):
        def build(d):
            for x in range(100, 300, 20):
                ink(d[0], [[(x, 100), (x + 12, 115)]])
        n = self.notes(self.pdf(build))["notes"]
        self.assertEqual(len(n), 1)
        self.assertGreater(n[0]["bbox"][2] - n[0]["bbox"][0], 190)

    def test_ink_far_apart_stays_two_notes(self):
        def build(d):
            ink(d[0], [[(100, 100), (200, 110)]])
            ink(d[0], [[(100, 500), (200, 510)]])
        n = self.notes(self.pdf(build))["notes"]
        self.assertEqual([x["id"] for x in n], ["note-p1-1", "note-p1-2"])
        self.assertNotEqual(n[0]["file"], n[1]["file"])

    def test_turkish_text_survives_an_ansi_pipe(self):
        path = self.pdf(lambda d: d[0].add_text_annot((300, 100), "Şişman hücre için ğ"))
        n = self.notes(path, PYTHONIOENCODING="cp1252:strict")["notes"]
        self.assertEqual(n[0]["text"], "Şişman hücre için ğ")

    def test_a_bad_range_is_refused(self):
        path = self.pdf(lambda d: None)
        for span in ("x", "0", "0-2", "3-1", "9-12", "1-2-3"):
            with self.subTest(span=span):
                r = self.cli("notes", path, span, "--out", self.d / "crops")
                self.assertEqual(r.returncode, 2, r.stdout)
                self.assertTrue(r.stderr.startswith("ERROR:"), r.stderr)

    def test_a_broken_or_missing_file_is_refused(self):
        bad = self.d / "bad.pdf"
        bad.write_text("not a pdf")
        for path in (bad, self.d / "none.pdf"):
            with self.subTest(path=path.name):
                r = self.cli("notes", path, "1", "--out", self.d / "crops")
                self.assertEqual(r.returncode, 2)
                self.assertNotIn("Traceback", r.stderr)

    def test_a_locked_pdf_is_refused(self):
        path = self.pdf(lambda d: None, encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="u", owner_pw="o")
        r = self.cli("notes", path, "1", "--out", self.d / "crops")
        self.assertEqual(r.returncode, 2)
        self.assertNotIn("Traceback", r.stderr)


class WithoutPymupdf(Base):
    def stderr_of(self, fn, **args):
        err = io.StringIO()
        with mock.patch.object(forge, "load_fitz", return_value=None), \
                mock.patch.object(forge.shutil, "which", return_value=None), \
                contextlib.redirect_stderr(err), self.assertRaises(SystemExit) as stop:
            fn(argparse.Namespace(pdf=str(self.d / "a.pdf"), out=str(self.d / "o"), dpi=100, **args))
        self.assertEqual(stop.exception.code, 4)
        return err.getvalue()

    def test_exit_4_with_the_same_advice_as_render_pages(self):
        advice = self.stderr_of(forge.cmd_notes, range="1-2")
        self.assertIn("forge.py doctor --install pdf_tool", advice)
        self.assertEqual(advice, self.stderr_of(forge.cmd_render_pages, range="1-2"))


class Fields(Base):
    def errors(self, q):
        return forge.question_errors(q, "open", (1, 20), 5)

    def has(self, q, text):
        self.assertTrue([e for e in self.errors(q) if text in e], self.errors(q))

    def test_allowed_kinds(self):
        self.assertEqual(forge.SOURCE_KINDS, ["text", "figure", "handwriting"])

    def test_a_question_with_no_kind_is_still_fine(self):
        self.assertEqual(self.errors(question()), [])

    def test_each_allowed_kind_passes(self):
        for kind in forge.SOURCE_KINDS:
            self.assertEqual(self.errors(question(kind=kind)), [], kind)

    def test_a_handwriting_question_with_a_note_id_passes(self):
        self.assertEqual(self.errors(question(kind="handwriting", note="note-p2-1")), [])

    def test_a_handwriting_question_without_a_note_id_passes(self):
        # handwriting drawn into a scanned page has no annotation, so it has no id
        self.assertEqual(self.errors(question(kind="handwriting")), [])

    def test_an_unknown_kind_fails(self):
        for kind in ("note", "Handwriting", "", None, ["handwriting"]):
            with self.subTest(kind=kind):
                q = question()
                q["source"]["kind"] = kind
                self.has(q, "source.kind")

    def test_handwriting_needs_a_quote(self):
        for quote in ("", "   \n", None):
            with self.subTest(quote=quote):
                self.has(question(kind="handwriting", quote=quote), "transcription")

    def test_a_note_id_must_look_like_one(self):
        for note in ("", "p2-1", "note-2-1", "note-p2", "note-p2-1-3", "note-p0-1", "note-p2-0", "NOTE-P2-1", 7, ["note-p2-1"]):
            with self.subTest(note=note):
                self.has(question(kind="handwriting", note=note), "source.note")

    def test_a_note_id_must_match_the_page(self):
        self.has(question(page=3, kind="handwriting", note="note-p2-1"), "source.note")

    def test_a_note_id_belongs_on_a_handwriting_question(self):
        self.has(question(kind="text", note="note-p2-1"), "source.note")
        self.has(question(note="note-p2-1"), "source.note")

    def test_a_source_that_is_not_an_object_is_an_error_not_a_crash(self):
        q = question()
        q["source"] = "lesson.pdf"
        self.has(q, "source must be")

    def test_check_fails_on_a_bad_field(self):
        p = self.write("c.json", chunk([question(kind="pencil")]))
        r = self.cli("check", p, "--format", "open")
        self.assertEqual(r.returncode, 1)
        self.assertIn("source.kind", r.stdout)

    def test_source_references_to_a_note_are_caught(self):
        for text in ("According to the handwritten note, what is X?", "What does the margin note say about X?",
                     "El yazısı nota göre X nedir?", "Kenar notunda X için ne yazıyor?"):
            with self.subTest(text=text):
                self.has(question(question=text), "reference to the source")

    def test_the_word_handwritten_alone_is_not_a_reference(self):
        q = question(question="Why were medieval handwritten manuscripts copied by monks?")
        self.assertEqual(self.errors(q), [])

    def test_option_text_is_covered_too(self):
        q = question(type="mcq", answer="A", options={k: f"option {k}" for k in "ABCD"})
        q["options"]["B"] = "As the handwritten note says"
        self.assertTrue([e for e in forge.question_errors(q) if "reference to the source" in e])


class Pipeline(Base):
    def test_check_counts_handwriting_questions(self):
        qs = [question(1, kind="handwriting"), question(2, kind="handwriting", note="note-p2-1"), question(3)]
        r = self.cli("check", self.write("c.json", chunk(qs)), "--format", "open")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertEqual(json.loads(r.stdout.splitlines()[0])["handwriting"], 2)

    def test_check_reports_zero_when_there_are_none(self):
        r = self.cli("check", self.write("c.json", chunk([question()])), "--format", "open")
        self.assertEqual(json.loads(r.stdout.splitlines()[0])["handwriting"], 0)

    def test_replace_keeps_the_fields(self):
        p = self.write("c.json", chunk([question(1)]))
        fix = self.write("f.json", {"questions": [question(1, kind="handwriting", note="note-p2-1", quote="ATP!")]})
        r = self.cli("replace", p, fix)
        self.assertEqual(r.returncode, 0, r.stderr)
        q = json.loads(p.read_text(encoding="utf-8"))["questions"][0]
        self.assertEqual((q["source"]["kind"], q["source"]["note"], q["quote"]), ("handwriting", "note-p2-1", "ATP!"))

    def test_approve_keeps_the_fields(self):
        p = self.write("c.json", chunk([question(1, kind="handwriting", note="note-p2-1")]))
        r = self.cli("approve", p, self.write("r.json", {"results": [{"id": "p1-q01", "status": "PASS"}]}))
        self.assertEqual(r.returncode, 0, r.stdout)
        q = json.loads(p.read_text(encoding="utf-8"))["questions"][0]
        self.assertEqual(q["source"]["note"], "note-p2-1")
        self.assertTrue(q["verified"])

    def test_source_pages_handles_a_handwriting_question(self):
        p = self.write("c.json", chunk([question(1, page=5, kind="handwriting", note="note-p5-2"), question(2, page=9)]))
        r = self.cli("source-pages", p, "--ids", "p1-q01")
        self.assertEqual(r.stdout.strip(), "4-6")
        self.assertEqual(json.loads(p.read_text(encoding="utf-8"))["questions"][0]["source"]["note"], "note-p5-2")

    def merged(self, qs, *extra):
        for q in qs:
            q["verified"] = True
        self.write("lesson/chunk-1.json", chunk(qs))
        r = self.cli("merge", self.d / "lesson", "--out", self.d / "book.json", "--title", "T", "--format", "open", *extra)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r, json.loads((self.d / "book.json").read_text(encoding="utf-8"))

    def test_merge_keeps_the_fields_and_counts(self):
        r, book = self.merged([question(1, kind="handwriting", note="note-p2-1"), question(2, kind="handwriting"), question(3)])
        self.assertEqual(book["handwriting_questions"], 2)
        self.assertEqual(book["parts"][0]["questions"][0]["source"]["note"], "note-p2-1")
        self.assertIn("2 ", r.stdout)
        self.assertIn("handwritten", r.stdout)

    def test_merge_counts_zero(self):
        r, book = self.merged([question(1)])
        self.assertEqual(book["handwriting_questions"], 0)
        self.assertNotIn("handwritten", r.stdout)

    def test_merge_counts_across_parts(self):
        self.write("a/chunk-1.json", chunk([question(1, kind="handwriting", verified=True)], source="a.pdf"))
        self.write("b/chunk-1.json", chunk([question(2, kind="handwriting", verified=True)], source="b.pdf"))
        r = self.cli("merge", self.d / "a", self.d / "b", "--out", self.d / "book.json", "--title", "T", "--format", "open")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads((self.d / "book.json").read_text(encoding="utf-8"))["handwriting_questions"], 2)

    def test_old_book_questions_without_a_kind_still_merge(self):
        _, book = self.merged([question(1)])
        self.assertNotIn("kind", book["parts"][0]["questions"][0]["source"])


class Setting(Base):
    def test_default_is_on(self):
        self.assertIs(json.loads(self.cli("settings").stdout)["handwritten_notes"], True)

    def test_schema_entry_has_labels_in_both_languages(self):
        spec = next(s for s in forge.load_schema()["settings"] if s["key"] == "handwritten_notes")
        self.assertEqual(spec["type"], "bool")
        for field in ("label", "help"):
            self.assertTrue(spec[field]["en"])
            self.assertTrue(spec[field]["tr"])

    def test_it_can_be_turned_off(self):
        self.assertEqual(self.cli("config", "set", "handwritten_notes", "false").returncode, 0)
        self.assertEqual(self.cli("config", "get", "handwritten_notes").stdout.strip(), "false")

    def test_a_non_boolean_is_refused(self):
        self.setting_off()
        self.write(".config/exam-forge/settings.json", {"handwritten_notes": "maybe"})
        self.assertEqual(self.cli("settings").returncode, 2)

    def test_check_flags_a_note_question_when_off(self):
        self.setting_off()
        r = self.cli("check", self.write("c.json", chunk([question(1, kind="handwriting", note="note-p2-1")])), "--format", "open")
        self.assertEqual(r.returncode, 1)
        self.assertIn("p1-q01", r.stdout)
        self.assertIn("handwritten_notes", r.stdout)

    def test_check_passes_other_questions_when_off(self):
        self.setting_off()
        r = self.cli("check", self.write("c.json", chunk([question(1)])), "--format", "open")
        self.assertEqual(r.returncode, 0, r.stdout)

    def test_check_passes_a_note_question_when_on(self):
        r = self.cli("check", self.write("c.json", chunk([question(1, kind="handwriting")])), "--format", "open")
        self.assertEqual(r.returncode, 0, r.stdout)

    def test_a_job_keeps_the_value_it_started_with(self):
        pdf = self.d / "a.pdf"
        pdf.write_bytes(b"%PDF-1.4\n")
        r = self.cli("plan", pdf, "--pages", "a.pdf=10", "--run", self.d / "run")
        self.assertEqual(r.returncode, 0, r.stderr)
        plan = json.loads(r.stdout)
        self.assertIs(plan["handwritten_notes"], True)
        self.setting_off()  # changed during the job: settings apply to the next one
        p = self.write("run/a/chunk-1.json", chunk([question(1, kind="handwriting")]))
        self.assertEqual(self.cli("check", p, "--format", "open").returncode, 0)

    def test_a_job_started_with_it_off_stays_off(self):
        pdf = self.d / "a.pdf"
        pdf.write_bytes(b"%PDF-1.4\n")
        self.setting_off()
        r = self.cli("plan", pdf, "--pages", "a.pdf=10", "--run", self.d / "run")
        self.assertIs(json.loads(r.stdout)["handwritten_notes"], False)
        (self.d / ".config/exam-forge/settings.json").unlink()
        p = self.write("run/a/chunk-1.json", chunk([question(1, kind="handwriting")]))
        self.assertEqual(self.cli("check", p, "--format", "open").returncode, 1)


class Book(Base):
    def book(self, language="en"):
        return {"title": "T", "format": "open", "language": language, "date": "2026-11-07", "sources": ["lesson.pdf"],
                "parts": [{"name": "Cells", "pdf": "lesson.pdf",
                           "questions": [question(1, kind="handwriting", note="note-p2-1"), question(2, page=3)]}]}

    def test_labels(self):
        self.assertEqual(forge.LABELS["en"]["handwritten_note"], "handwritten note")
        self.assertEqual(forge.LABELS["tr"]["handwritten_note"], "el yazısı not")

    def test_source_line_cites_the_note(self):
        for lang in ("en", "tr"):
            L = forge.LABELS[lang]
            line = forge.source_line(question(kind="handwriting", note="note-p2-1"), L)
            self.assertIn(L["handwritten_note"], line)
            self.assertIn(f'{L["page"]} 2', line)

    def test_the_note_id_is_not_printed(self):
        self.assertNotIn("note-p2-1", forge.source_line(question(kind="handwriting", note="note-p2-1"), forge.LABELS["en"]))

    def test_a_note_without_an_id_is_cited_too(self):
        self.assertIn("handwritten note", forge.source_line(question(kind="handwriting"), forge.LABELS["en"]))

    def test_other_kinds_keep_the_line_as_it_was(self):
        L = forge.LABELS["en"]
        plain = forge.source_line(question(), L)
        self.assertEqual(plain, "Source: lesson.pdf · Section: Cells · p. 2")
        self.assertEqual(forge.source_line(question(kind="text"), L), plain)

    def test_every_answer_position_cites_it(self):
        for lang, word in (("en", "handwritten note"), ("tr", "el yazısı not")):
            for placement in ("book_end", "inline", "section_end"):
                with self.subTest(lang=lang, placement=placement):
                    page = forge.build_book_html(self.book(lang), placement)[0]
                    self.assertEqual(page.count(word), 1)

    def test_the_question_side_stays_free_of_the_note(self):
        q = question(kind="handwriting", note="note-p2-1")
        side = forge.question_html(1, q, forge.LABELS["en"], False)
        self.assertNotIn("handwritten", side)
        self.assertNotIn("note-p", side)
        stem = forge.question_html(1, q, forge.LABELS["en"], True).split('class="inline-answer"')[0]
        self.assertNotIn("handwritten", stem)


class Prompts(unittest.TestCase):
    def assertSays(self, text, *needles):
        for n in needles:
            self.assertIn(n, text)

    def test_editor(self):
        t = read("agents", "editor.md")
        self.assertSays(t, "scope instruction", "struck-through", "never guess", "`handwriting`", "source.note",
                        "handwritten_notes", "unreadable notes", "conflict", "not on the exam")

    def test_editor_still_treats_scope_notes_as_instructions(self):
        t = read("agents", "editor.md")
        self.assertIn('A handwritten or marked note such as "remove", "not on the exam", "out of scope", or a struck-through part, means that part is out of scope.', t)

    def test_reviewer(self):
        t = read("agents", "reviewer.md")
        self.assertSays(t, "crop", "transcription", "|handwriting", "scope instruction", "handwritten_notes", "conflict")

    def test_chief_editor(self):
        t = read("agents", "chief-editor.md")
        self.assertSays(t, "crop", "transcription", "|handwriting")

    def test_screener(self):
        t = read("agents", "screener.md")
        self.assertSays(t, "`handwriting`", "scope instruction")

    def test_orchestrator(self):
        t = read("skills", "exam-forge", "SKILL.md")
        self.assertSays(t, '"$FORGE" notes', "--out", "handwritten_notes", "handwriting_questions", "unreadable notes")

    def test_helper_usage_lists_the_command(self):
        self.assertIn("notes <pdf>", forge.__doc__)

    def test_readme_and_changelog(self):
        readme = read("README.md")
        self.assertSays(readme, "Handwritten notes", "`handwritten_notes`", "El yazısı")
        unreleased = read("CHANGELOG.md").split("## [0.3.1]")[0]
        self.assertIn("forge.py notes", unreleased)

    def test_studio_names_the_new_reason_in_both_languages(self):
        self.assertEqual(read("skills", "exam-forge", "studio", "i18n.js").count(" handwriting: "), 2)


if __name__ == "__main__":
    unittest.main()
