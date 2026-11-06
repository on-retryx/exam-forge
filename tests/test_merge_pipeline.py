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
        return book, forge.build_book_html(book, forge.S["answer_placement"])

    def test_notes_reach_the_book(self):
        listing = json.loads(self.ok("notes", self.pdf, "1-1", "--out", self.run_dir / "notes").stdout)
        note = listing["notes"][0]
        hand = question(2, quote="ATP is made here", kind="handwriting", note=note["id"])
        book, html = self.finish([question(1), hand])
        self.assertEqual(book["handwriting_questions"], 1)
        self.assertIn(forge.LABELS["en"]["handwritten_note"], html)
        self.assertNotIn(note["id"], html)

    def test_a_note_cannot_skip_the_review(self):
        hand = question(2, quote="ATP is made here", kind="handwriting")
        chunk = self.run_dir / "chunk-1.json"
        chunk.write_text(json.dumps({"source": "lesson.pdf", "chunk": 1, "format": "open", "page_start": 1, "page_end": 1,
                                     "questions": [hand]}), encoding="utf-8")
        r = self.cli("merge", self.run_dir, "--out", self.d / "run" / "book.json", "--title", "C", "--format", "open")
        self.assertNotEqual(r.returncode, 0)


if __name__ == "__main__":
    unittest.main()
