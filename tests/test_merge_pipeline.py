"""One PDF through the whole command chain, as a job runs it. Each feature joins here as it merges."""
import importlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

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

BODY = "Mitochondria produce energy for the cell. Ribosomes build proteins from amino acids."


def question(i, page=1, quote="Mitochondria produce energy for the cell.", **source):
    return {"id": f"p1-q{i:02d}", "section": "Cells", "type": "open", "difficulty": "easy",
            "question": f"What does organelle {i} do?", "answer": "It produces energy.", "explanation": "Stated directly.",
            "quote": quote, "source": {"pdf": "lesson.pdf", "section": "Cells", "page": page, **source}}


@unittest.skipUnless(pymupdf, "needs pymupdf")
class Pipeline(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.d = Path(td.name)
        self.pdf = self.d / "lesson.pdf"
        self.run_dir = self.d / "run" / "lesson"
        self.run_dir.mkdir(parents=True)
        doc = pymupdf.open()
        self.page = doc.new_page()
        self.page.insert_text((72, 90), BODY, fontsize=11)
        self.decorate(self.page)
        doc.save(str(self.pdf))
        doc.close()

    def decorate(self, page):
        page.add_ink_annot([[(100, 200), (160, 220), (220, 205)]])
        page.draw_line((150, 320), (150, 560), width=1)
        page.draw_line((150, 560), (420, 560), width=1)
        for i, height in enumerate((60, 120, 200, 90)):
            page.draw_rect(pymupdf.Rect(170 + i * 60, 560 - height, 210 + i * 60, 560), fill=(0.3, 0.5, 0.9))

    def cli(self, *args):
        env = {**os.environ, "HOME": str(self.d)}
        env.pop("EXAM_FORGE_SETTINGS", None)
        return subprocess.run([sys.executable, str(FORGE), *map(str, args)], capture_output=True, text=True,
                              encoding="utf-8", env=env)

    def ok(self, *args):
        r = self.cli(*args)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return r

    def finish(self, questions):
        """check, approve, merge, and the book's text."""
        chunk = self.run_dir / "chunk-1.json"
        chunk.write_text(json.dumps({"source": "lesson.pdf", "chunk": 1, "format": "open", "page_start": 1, "page_end": 1,
                                     "questions": questions}), encoding="utf-8")
        self.ok("check", chunk, "--format", "open")
        review = self.run_dir / "review-1-round1.json"
        review.write_text(json.dumps({"results": [{"id": q["id"], "status": "PASS"} for q in questions]}), encoding="utf-8")
        self.ok("approve", chunk, review)
        out = self.d / "run" / "book.json"
        self.ok("merge", self.run_dir, "--out", out, "--title", "Cells", "--format", "open", "--language", "en")
        book = json.loads(out.read_text(encoding="utf-8"))
        forge.S = forge.load_settings()
        return book, forge.build_book_html(book, forge.S["answer_placement"], base=str(out.parent))[0]

    def test_notes_reach_the_book(self):
        listing = json.loads(self.ok("notes", self.pdf, "1-1", "--out", self.run_dir / "notes").stdout)
        note = listing["notes"][0]
        hand = question(2, quote="ATP is made here", kind="handwriting", note=note["id"])
        book, html = self.finish([question(1), hand])
        self.assertEqual(book["handwriting_questions"], 1)
        self.assertEqual(book["parts"][0]["questions"][-1]["source"].get("kind"), "handwriting")
        self.assertIn(forge.LABELS["en"]["handwritten_note"], html)
        self.assertNotIn(note["id"], html)

    def figure(self):
        listing = json.loads(self.ok("figures", self.pdf, "1-1", "--out", self.run_dir / "figures").stdout)
        fig = listing[0] if isinstance(listing, list) else listing["figures"][0]
        return {"id": fig["id"], "page": fig["page"], "file": fig["file"], "caption": "Energy by stage"}

    def test_figures_are_printed_inside_the_book(self):
        q = question(2, quote="Stage 2: 40 units", kind="figure")
        q["figure"] = self.figure()
        q["question"] = "Which stage does the graph shown peak at?"
        book, html = self.finish([question(1), q])
        self.assertIn("data:image/png;base64,", html)
        self.assertIn("Energy by stage", html)
        self.assertNotIn("figures/fig-p1", html)

    def test_figure_and_note_questions_share_one_book(self):
        fig = question(2, quote="Stage 2: 40 units", kind="figure")
        fig["figure"] = self.figure()
        fig["question"] = "Which stage does the graph shown peak at?"
        note = json.loads(self.ok("notes", self.pdf, "1-1", "--out", self.run_dir / "notes").stdout)["notes"][0]
        hand = question(3, quote="ATP is made here", kind="handwriting", note=note["id"])
        book, html = self.finish([question(1), fig, hand])
        self.assertEqual(book["handwriting_questions"], 1)
        self.assertIn("data:image/png;base64,", html)
        self.assertIn(forge.LABELS["en"]["handwritten_note"], html)

    def annotated_page(self, draw):
        doc = pymupdf.open()
        page = doc.new_page()
        page.insert_text((72, 90), BODY, fontsize=11)
        draw(page)
        path = self.d / "marked.pdf"
        doc.save(str(path))
        doc.close()
        return json.loads(self.ok("figures", path, "1-1", "--out", self.run_dir / "marked").stdout)

    def test_handwriting_is_not_a_figure(self):
        strokes = [[(100 + i * 12, 200 + (i % 3) * 25), (106 + i * 12, 240)] for i in range(25)]
        self.assertEqual(self.annotated_page(lambda p: p.add_ink_annot(strokes)), [])
        def many(page):
            for i in range(6):
                page.add_ink_annot([[(100, 200 + i * 30), (300, 205 + i * 30), (420, 200 + i * 30)]])
        self.assertEqual(self.annotated_page(many), [])

    def test_highlights_are_not_a_figure(self):
        def marked(page):
            for word in page.get_text("words")[:6]:
                page.add_highlight_annot(pymupdf.Rect(word[:4]))
        self.assertEqual(self.annotated_page(marked), [])

    def marked_pdf(self):
        doc = pymupdf.open(str(self.pdf))
        page = doc[0]
        page.add_highlight_annot(page.search_for("Mitochondria produce energy")[0])
        page.add_strikeout_annot(page.search_for("Ribosomes build proteins")[0])
        path = self.d / "lesson-marked.pdf"
        doc.save(str(path))
        doc.close()
        return path

    def test_all_three_features_share_one_book(self):
        pdf = self.marked_pdf()
        marks_file = self.run_dir / "marks-1.json"
        self.ok("marks", pdf, "1-1", "--out", marks_file)
        marks = json.loads(marks_file.read_text(encoding="utf-8"))["marks"]
        self.assertEqual(sorted(m["role"] for m in marks), ["emphasis", "excluded"])
        figures = json.loads(self.ok("figures", pdf, "1-1", "--out", self.run_dir / "figures").stdout)
        self.assertEqual(len(figures), 1)                  # neither the ink nor the marks are figures
        notes = json.loads(self.ok("notes", pdf, "1-1", "--out", self.run_dir / "notes").stdout)["notes"]
        self.assertEqual([n["kind"] for n in notes], ["ink"])  # nor are the marks notes
        marked = question(1, emphasis="highlight", mark=next(m["id"] for m in marks if m["role"] == "emphasis"))
        fig = question(2, quote="Stage 2: 40 units", kind="figure")
        fig["figure"] = {"id": figures[0]["id"], "page": 1, "file": figures[0]["file"], "caption": "Energy by stage"}
        fig["question"] = "Which stage does the graph shown peak at?"
        hand = question(3, quote="ATP is made here", kind="handwriting", note=notes[0]["id"])
        book, html = self.finish([marked, fig, hand])
        chunk = self.run_dir / "chunk-1.json"
        report = json.loads(self.ok("marks-coverage", chunk, marks_file).stdout)
        self.assertEqual(report["excluded_hits"], [])
        self.assertEqual(book["handwriting_questions"], 1)
        self.assertIn("data:image/png;base64,", html)
        self.assertIn(forge.LABELS["en"]["handwritten_note"], html)

    def test_struck_text_stays_out_and_highlighted_text_does_not(self):
        pdf = self.marked_pdf()
        marks_file = self.run_dir / "marks-1.json"
        self.ok("marks", pdf, "1-1", "--out", marks_file)
        chunk = self.run_dir / "chunk-1.json"
        struck = question(1, quote="Ribosomes build proteins from amino acids.")
        chunk.write_text(json.dumps({"source": "lesson.pdf", "chunk": 1, "format": "open", "page_start": 1, "page_end": 1,
                                     "questions": [struck]}), encoding="utf-8")
        self.assertNotEqual(self.cli("marks-coverage", chunk, marks_file).returncode, 0)
        kept = question(1)
        chunk.write_text(json.dumps({"source": "lesson.pdf", "chunk": 1, "format": "open", "page_start": 1, "page_end": 1,
                                     "questions": [kept]}), encoding="utf-8")
        self.ok("marks-coverage", chunk, marks_file)

    def test_a_question_cannot_mention_a_figure_it_does_not_have(self):
        q = question(1)
        q["question"] = "Which stage does the graph shown peak at?"
        chunk = self.run_dir / "chunk-1.json"
        chunk.write_text(json.dumps({"source": "lesson.pdf", "chunk": 1, "format": "open", "page_start": 1, "page_end": 1,
                                     "questions": [q]}), encoding="utf-8")
        self.assertNotEqual(self.cli("check", chunk, "--format", "open").returncode, 0)

    def test_a_note_cannot_skip_the_review(self):
        hand = question(2, quote="ATP is made here", kind="handwriting")
        chunk = self.run_dir / "chunk-1.json"
        chunk.write_text(json.dumps({"source": "lesson.pdf", "chunk": 1, "format": "open", "page_start": 1, "page_end": 1,
                                     "questions": [hand]}), encoding="utf-8")
        r = self.cli("merge", self.run_dir, "--out", self.d / "run" / "book.json", "--title", "C", "--format", "open")
        self.assertNotEqual(r.returncode, 0)


if __name__ == "__main__":
    unittest.main()
