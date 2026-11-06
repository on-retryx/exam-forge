"""Questions built from figures: finding them in a PDF, checking them, printing them.

Run: python3 -m unittest tests/test_feature_figures.py (from the repository root, with tests on the path)
"""
import base64
import importlib
import json
import os
import re
import stat
import struct
import subprocess
import sys
import tempfile
import unittest
import zlib
from pathlib import Path

import conftest  # noqa: F401

ROOT = Path(__file__).resolve().parent.parent
SKILL = ROOT / "skills" / "exam-forge"
FORGE = SKILL / "bin" / "forge.py"

fitz = None
for _name in ("pymupdf", "fitz"):
    try:
        fitz = importlib.import_module(_name)
        break
    except Exception:
        pass
needs_pymupdf = unittest.skipUnless(fitz, "pymupdf is not installed")


def png_bytes(width=60, height=40, seed=0):
    """A real PNG, made by hand so these tests need no image library. `seed` changes its pixels."""
    rows = b"".join(b"\0" + bytes((x * 7 + y * 3 + seed) % 256 for x in range(width) for _ in range(3))
                    for y in range(height))

    def block(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    head = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + block(b"IHDR", head) + block(b"IDAT", zlib.compress(rows)) + block(b"IEND", b"")


def question(i=1, **extra):
    q = {
        "id": f"p1-q{i:02d}", "section": "Graphs", "type": "mcq", "difficulty": "easy",
        "question": f"What is the peak value of the curve number {i}?",
        "options": {k: f"{k} value {i}" for k in "ABCDE"},
        "answer": "A", "explanation": "It is labelled on the axis.",
        "source": {"pdf": "lesson.pdf", "section": "Graphs", "page": 3},
        "quote": "Peak: 40 mV",
    }
    q.update(extra)
    return q


def figure_question(i=1, file="figures/fig-p3-1.png", page=3, caption="Membrane potential over time", **extra):
    q = question(i, figure={"id": "fig-p3-1", "page": page, "file": file, "caption": caption},
                 question=f"What is the peak value in the graph shown, reading number {i}?")
    q["source"] = dict(q["source"], kind="figure")
    q.update(extra)
    return q


class Base(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.d = Path(self.td.name).resolve()
        self.env = {"HOME": str(self.d), "USERPROFILE": str(self.d)}
        self.addCleanup(self.td.cleanup)

    def f(self, *args, cwd=None, env=None):
        e = {k: v for k, v in os.environ.items() if k != "EXAM_FORGE_SETTINGS"}
        e.update(self.env)
        e.update(env or {})
        return subprocess.run([sys.executable, str(FORGE), *map(str, args)], capture_output=True, text=True,
                              env=e, cwd=cwd or self.d)

    def write(self, name, data):
        p = self.d / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return p

    def image(self, name="run/lesson/figures/fig-p3-1.png", data=None):
        p = self.d / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(png_bytes() if data is None else data)
        return p

    def chunk(self, questions, name="run/lesson/chunk-1.json", **extra):
        return self.write(name, {"source": "lesson.pdf", "chunk": 1, "format": "mcq", "page_start": 1,
                                 "page_end": 20, "questions": questions, **extra})


class FigureFinder(Base):
    """Fixture pages are drawn here, with the same library the command reads them with."""

    def pdf(self, *pages, size=(595, 842), name="lesson.pdf"):
        """Each page is a function that draws on one page."""
        doc = fitz.open()
        for draw in pages:
            page = doc.new_page(width=size[0], height=size[1])
            if draw:
                draw(page)
        p = self.d / name
        doc.save(str(p))
        doc.close()
        return p

    def figures(self, pdf, pages="1", out="run/lesson/figures"):
        r = self.f("figures", pdf, pages, "--out", self.d / out)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    @staticmethod
    def chart(page, x=100, y=150, labels=True):
        """Two axes, four bars, tick labels under the axis and a title under those."""
        shape = page.new_shape()
        shape.draw_line((x, y), (x, y + 200))
        shape.draw_line((x, y + 200), (x + 300, y + 200))
        shape.finish(color=(0, 0, 0), width=1)
        for i, h in enumerate((60, 120, 90, 170)):
            shape.draw_rect(fitz.Rect(x + 20 + i * 65, y + 200 - h, x + 60 + i * 65, y + 200))
            shape.finish(color=(0, 0, 0), fill=(.3, .5, .6))
        shape.commit()
        if labels:
            for i, t in enumerate(("Q1", "Q2", "Q3", "Q4")):
                page.insert_text((x + 30 + i * 65, y + 214), t, fontsize=9)
            page.insert_text((x + 110, y + 228), "Quarter", fontsize=9)

    @staticmethod
    def picture(page, rect=(100, 150, 400, 400), seed=0):
        page.insert_image(fitz.Rect(*rect), stream=png_bytes(seed=seed), keep_proportion=False)

    @staticmethod
    def triangle(page):
        shape = page.new_shape()
        shape.draw_polyline([(150, 400), (350, 400), (250, 250), (150, 400)])
        shape.finish(color=(0, 0, 0), width=1)
        shape.commit()
        page.insert_text((245, 243), "A", fontsize=10)

    def test_embedded_image_is_found_with_every_field(self):
        pdf = self.pdf(self.picture)
        [fig] = self.figures(pdf)
        self.assertEqual(fig["id"], "fig-p1-1")
        self.assertEqual(fig["page"], 1)
        self.assertEqual(len(fig["bbox"]), 4)
        x0, y0, x1, y1 = fig["bbox"]
        self.assertLessEqual(x0, 100)
        self.assertLessEqual(y0, 150)
        self.assertGreaterEqual(x1, 400)
        self.assertGreaterEqual(y1, 400)
        self.assertLess(x1, 420)
        self.assertLess(y1, 420)

    def test_crop_is_a_real_png_of_the_region(self):
        pdf = self.pdf(self.picture)
        [fig] = self.figures(pdf)
        png = self.d / "run/lesson" / fig["file"]
        self.assertTrue(png.read_bytes().startswith(b"\x89PNG\r\n\x1a\n"))
        pix = fitz.Pixmap(str(png))
        self.assertGreater(pix.width, 300)  # sharper than one pixel per point
        self.assertAlmostEqual(pix.width / pix.height, (fig["bbox"][2] - fig["bbox"][0]) / (fig["bbox"][3] - fig["bbox"][1]), 1)
        self.assertFalse(pix.is_unicolor)

    def test_file_is_relative_to_the_folder_holding_out_and_path_is_absolute(self):
        pdf = self.pdf(self.picture)
        [fig] = self.figures(pdf)
        self.assertEqual(fig["file"], "figures/fig-p1-1.png")
        self.assertTrue(Path(fig["path"]).is_absolute())
        self.assertTrue(Path(fig["path"]).is_file())

    def test_vector_chart_is_one_figure_not_one_per_line(self):
        pdf = self.pdf(self.chart)
        [fig] = self.figures(pdf)
        self.assertLessEqual(fig["bbox"][0], 100)
        self.assertGreaterEqual(fig["bbox"][2], 400)
        self.assertLessEqual(fig["bbox"][1], 150 + 30)
        self.assertGreaterEqual(fig["bbox"][3], 350)

    def test_labelled_shape_is_found(self):
        pdf = self.pdf(self.triangle)
        figs = self.figures(pdf)
        self.assertEqual(len(figs), 1)

    def test_labels_around_a_figure_are_inside_the_crop(self):
        pdf = self.pdf(self.chart)
        [fig] = self.figures(pdf)
        with fitz.open(str(pdf)) as doc:
            tick = doc[0].search_for("Q4")[0]
            title = doc[0].search_for("Quarter")[0]
        self.assertGreaterEqual(fig["bbox"][3], tick.y1)
        self.assertGreaterEqual(fig["bbox"][3], title.y1)

    def test_paragraph_next_to_a_figure_stays_out(self):
        def draw(page):
            self.chart(page, labels=False)
            page.insert_textbox(fitz.Rect(60, 362, 540, 460), "Sentence of running text about the topic. " * 12, fontsize=11)
        pdf = self.pdf(draw)
        [fig] = self.figures(pdf)
        self.assertLess(fig["bbox"][3], 372)

    def test_two_figures_on_a_page_are_numbered_top_to_bottom(self):
        def draw(page):
            self.picture(page, (100, 500, 300, 700), seed=1)
            self.chart(page, y=60, labels=False)
        pdf = self.pdf(draw)
        figs = self.figures(pdf)
        self.assertEqual([f["id"] for f in figs], ["fig-p1-1", "fig-p1-2"])
        self.assertLess(figs[0]["bbox"][1], figs[1]["bbox"][1])
        self.assertTrue(all(Path(f["path"]).is_file() for f in figs))

    def test_tiny_decoration_is_ignored(self):
        def draw(page):
            shape = page.new_shape()
            for i in range(6):  # bullets
                shape.draw_circle((80, 100 + i * 40), 3)
                shape.finish(fill=(0, 0, 0))
            shape.draw_line((60, 500), (540, 500))  # a rule
            shape.finish(color=(0, 0, 0))
            shape.commit()
            self.picture(page, (500, 780, 520, 800))  # an icon
        self.assertEqual(self.figures(self.pdf(draw)), [])

    def test_a_plain_box_is_not_a_figure(self):
        def draw(page):
            shape = page.new_shape()
            shape.draw_rect(fitz.Rect(80, 100, 500, 260))
            shape.finish(color=(0, 0, 0), fill=(.9, .9, .8))
            shape.commit()
            page.insert_text((100, 130), "Key point of the lecture", fontsize=12)
        self.assertEqual(self.figures(self.pdf(draw)), [])

    def test_logo_repeated_on_many_pages_is_ignored(self):
        def logo(page):
            self.picture(page, (440, 30, 560, 110), seed=9)
        pages = [logo] * 5 + [lambda p: (logo(p), self.picture(p, seed=2))]
        pdf = self.pdf(*pages)
        figs = self.figures(pdf, "1-6")
        self.assertEqual([f["id"] for f in figs], ["fig-p6-1"])

    def test_repeated_logo_is_found_outside_the_range_too(self):
        """A chunk must get the same answer as the whole book."""
        def logo(page):
            self.picture(page, (440, 30, 560, 110), seed=9)
        pdf = self.pdf(*[logo] * 5)
        self.assertEqual(self.figures(pdf, "2-2"), [])

    def test_repeated_vector_header_is_ignored(self):
        def header(page):
            shape = page.new_shape()
            for i in range(8):
                shape.draw_circle((420 + i * 14, 70), 30)
                shape.finish(color=(.2, .2, .5))
            shape.commit()
        self.assertEqual(self.figures(self.pdf(*[header] * 5), "1-5"), [])

    def test_a_figure_shown_twice_in_a_long_deck_is_kept(self):
        """Slides often build up over two pages. That is not a logo."""
        def shown(page):
            self.picture(page, seed=4)
        pdf = self.pdf(shown, shown, *[None] * 18)
        self.assertEqual(len(self.figures(pdf, "1-20")), 2)

    def test_page_size_background_and_border_are_ignored(self):
        def draw(page):
            page.insert_image(page.rect, stream=png_bytes(seed=5), keep_proportion=False)
            shape = page.new_shape()
            shape.draw_rect(page.rect + (10, 10, -10, -10))
            shape.finish(color=(0, 0, 0))
            shape.commit()
        self.assertEqual(self.figures(self.pdf(draw)), [])

    def test_background_does_not_hide_a_real_figure(self):
        def draw(page):
            page.insert_image(page.rect, stream=png_bytes(seed=5), keep_proportion=False)
            self.chart(page)
        figs = self.figures(self.pdf(draw))
        self.assertEqual(len(figs), 1)
        self.assertLess(figs[0]["bbox"][2], 500)

    def test_a_crop_with_nothing_on_it_is_dropped(self):
        def draw(page):
            shape = page.new_shape()
            for i in range(6):
                shape.draw_circle((200 + i * 20, 300), 30)
                shape.finish(color=(1, 1, 1), fill=(1, 1, 1))
            shape.commit()
        self.assertEqual(self.figures(self.pdf(draw)), [])

    def test_only_the_asked_range_is_read(self):
        pdf = self.pdf(*[lambda p, s=i: self.picture(p, seed=s) for i in range(5)])
        figs = self.figures(pdf, "2-3")
        self.assertEqual([f["page"] for f in figs], [2, 3])
        self.assertEqual(len(list((self.d / "run/lesson/figures").glob("*.png"))), 2)

    def test_single_page_and_a_range_past_the_end(self):
        pdf = self.pdf(*[lambda p, s=i: self.picture(p, seed=s) for i in range(3)])
        self.assertEqual([f["page"] for f in self.figures(pdf, "2")], [2])
        self.assertEqual([f["page"] for f in self.figures(pdf, "3-40")], [3])

    def test_same_result_when_run_again(self):
        pdf = self.pdf(self.chart)
        self.assertEqual(self.figures(pdf), self.figures(pdf))

    def test_rotated_page_crops_the_figure_not_a_blank_area(self):
        def draw(page):
            self.chart(page, x=80, y=80)
            page.set_rotation(90)
        pdf = self.pdf(draw)
        [fig] = self.figures(pdf)
        with fitz.open(str(pdf)) as doc:
            self.assertLessEqual(fig["bbox"][2], doc[0].rect.x1 + .01)
            self.assertLessEqual(fig["bbox"][3], doc[0].rect.y1 + .01)
        self.assertFalse(fitz.Pixmap(fig["path"]).is_unicolor)
        expected = fitz.Rect(80, 80, 380, 280) * fitz.Matrix(0, 1, -1, 0, 842, 0)
        self.assertTrue(fitz.Rect(fig["bbox"]).intersects(expected))

    def test_bad_ranges_stop_with_a_message(self):
        pdf = self.pdf(self.picture)
        for bad in ("x", "0-3", "5-2", "1-"):
            r = self.f("figures", pdf, bad, "--out", self.d / "o")
            self.assertEqual(r.returncode, 2, bad)
            self.assertIn("range", r.stderr)

    def test_missing_and_broken_pdf_stop_with_a_message(self):
        broken = self.d / "broken.pdf"
        broken.write_bytes(b"not a pdf at all")
        for pdf in (self.d / "nothing.pdf", broken):
            r = self.f("figures", pdf, "1", "--out", self.d / "o")
            self.assertEqual(r.returncode, 2)
            self.assertIn(pdf.name, r.stderr)
            self.assertNotIn("Traceback", r.stderr)


class NoPymupdf(Base):
    def test_exit_4_with_the_install_advice_of_render_pages(self):
        fake = self.d / "stub"
        fake.mkdir()
        for name in ("pymupdf", "fitz"):
            (fake / f"{name}.py").write_text("raise ImportError('not here')\n")
        (self.d / "a.pdf").write_bytes(b"%PDF-1.4\n")
        env = {"PYTHONPATH": str(fake), "PATH": "/nonexistent"}
        figures = self.f("figures", self.d / "a.pdf", "1", "--out", self.d / "o", env=env)
        pages = self.f("render-pages", self.d / "a.pdf", "1", "--out", self.d / "o", env=env)
        self.assertEqual(figures.returncode, 4)
        self.assertEqual(figures.stderr, pages.stderr)

    def test_the_helper_does_not_import_pymupdf_at_the_top(self):
        head = FORGE.read_text(encoding="utf-8").split("def ", 1)[0]
        self.assertNotRegex(head, r"(?m)^\s*(import|from)\s+(fitz|pymupdf)")


class Check(Base):
    def errors(self, q, image=True, **extra):
        """The stdout of `check` on one question, with its figure in place unless told otherwise."""
        if image:
            self.image()
        r = self.f("check", self.chunk([q], **extra), "--format", "mcq")
        return r.returncode, r.stdout + r.stderr

    def assertAccepted(self, q, **extra):
        code, out = self.errors(q, **extra)
        self.assertEqual(code, 0, out)

    def assertRejected(self, q, word, **extra):
        code, out = self.errors(q, **extra)
        self.assertNotEqual(code, 0, out)
        self.assertIn(word, out)

    def test_a_good_figure_question_passes(self):
        self.assertAccepted(figure_question())

    def test_a_question_without_a_figure_still_passes(self):
        self.assertAccepted(question(), image=False)

    def test_an_explicit_text_kind_passes(self):
        q = question()
        q["source"]["kind"] = "text"
        self.assertAccepted(q, image=False)

    def test_kind_figure_needs_a_figure(self):
        q = question()
        q["source"]["kind"] = "figure"
        self.assertRejected(q, "figure", image=False)

    def test_a_figure_needs_kind_figure(self):
        for kind in (None, "text", "handwriting"):
            q = figure_question()
            q["source"].pop("kind")
            if kind:
                q["source"]["kind"] = kind
            self.assertRejected(q, "kind")

    def test_handwriting_kind_is_allowed_without_a_figure(self):
        q = question()
        q["source"]["kind"] = "handwriting"
        self.assertAccepted(q, image=False)

    def test_an_unknown_kind_is_rejected(self):
        q = question()
        q["source"]["kind"] = "diagram"
        self.assertRejected(q, "kind", image=False)

    def test_the_figure_file_must_exist(self):
        self.assertRejected(figure_question(), "fig-p3-1.png", image=False)

    def test_the_file_is_found_from_the_question_file_not_the_working_folder(self):
        self.image()
        p = self.chunk([figure_question()])
        elsewhere = self.d / "elsewhere"
        elsewhere.mkdir()
        self.assertEqual(self.f("check", p, cwd=elsewhere).returncode, 0)

    def test_a_folder_or_a_non_png_is_not_a_figure(self):
        self.image(data=b"<html>not an image</html>")
        code, out = self.errors(figure_question(), image=False)
        self.assertNotEqual(code, 0)
        self.assertIn("PNG", out)
        code, out = self.errors(figure_question(file="figures"), image=False)
        self.assertNotEqual(code, 0)

    def test_a_truncated_or_empty_png_is_not_a_figure(self):
        for data in (b"", png_bytes()[:40]):
            self.image(data=data)
            code, out = self.errors(figure_question(), image=False)
            self.assertNotEqual(code, 0)
            self.assertIn("PNG", out)

    def test_the_page_must_be_in_the_chunk(self):
        self.assertRejected(figure_question(page=21), "page 21")
        self.assertRejected(figure_question(page=4), "outside", page_start=5, page_end=8)

    def test_the_figure_must_have_its_fields(self):
        for broken in ({"page": 3, "file": "figures/fig-p3-1.png"},
                       {"id": "fig-p3-1", "file": "figures/fig-p3-1.png"},
                       {"id": "fig-p3-1", "page": 3},
                       {"id": "fig-p3-1", "page": "3", "file": "figures/fig-p3-1.png"},
                       {"id": "fig-p3-1", "page": True, "file": "figures/fig-p3-1.png"},
                       {"id": "fig-p3-1", "page": 3, "file": "figures/fig-p3-1.png", "caption": 7},
                       "figures/fig-p3-1.png"):
            self.assertRejected(figure_question(figure=broken), "figure")

    def test_a_caption_is_optional(self):
        q = figure_question()
        del q["figure"]["caption"]
        self.assertAccepted(q)

    def test_a_caption_cannot_point_at_the_source(self):
        self.assertRejected(figure_question(caption="Curve from slide 12"), "reference")

    def test_a_figure_question_may_name_the_figure(self):
        for text in ("What is the peak value in the figure?", "According to the figure, which label marks the nucleus?",
                     "In the graph shown, when does the voltage peak?", "Which part is shown in the diagram?",
                     "What does the chart below show for 2020?", "Based on the image, what is at point A?",
                     "What happens after point B on the graph above?", "Which curve in this figure rises first?",
                     "Şekle göre A ile gösterilen yapı hangisidir?", "Grafikte en yüksek değer kaçtır?",
                     "Görselde B harfi neyi gösterir?", "Yukarıdaki şekilde gösterilen yapı nedir?",
                     "Aşağıdaki grafiğe göre en düşük değer hangisidir?", "Diyagramdaki ok neyi gösterir?",
                     "Yukarıda verilen grafikte en yüksek nokta hangisidir?", "Şekildeki üçgenin tepe noktası hangisidir?"):
            self.assertAccepted(figure_question(question=text))

    def test_a_figure_question_may_not_point_at_anything_else(self):
        for text in ("According to the notes, what does the figure show?", "What does the figure on page 12 show?",
                     "In the slides, what does the graph show?", "What does the graph in the PDF show?",
                     "What is the figure given for sodium in the graph?", "According to the text, what does the chart show?",
                     "Slayttaki grafikte en yüksek değer kaçtır?", "Metne göre şekildeki yapı nedir?",
                     "Grafikte sayfa 12 için hangi değer vardır?", "Sodyum için verilen değer şekildeki hangisidir?"):
            self.assertRejected(figure_question(question=text), "reference")

    def test_options_of_a_figure_question_are_held_to_the_same_rule(self):
        q = figure_question()
        q["options"]["B"] = "the value in the slides"
        self.assertRejected(q, "reference")

    def test_a_question_without_a_figure_may_not_name_one(self):
        for text in ("What is the peak value in the figure?", "According to the figure, what is the peak?",
                     "In the graph shown, when does the voltage peak?", "Which part is labelled in the diagram?",
                     "What does the chart below show for 2020?", "According to the graph, what happens?",
                     "Which structure is in this figure?", "What does the above figure show?",
                     "What happens on the graph above?", "Based on the image, what is at point A?",
                     "What does the image shown represent?",
                     "Şekle göre A yapısı hangisidir?", "Grafikte en yüksek değer kaçtır?",
                     "Görselde B harfi neyi gösterir?", "Yukarıdaki şekil neyi gösterir?",
                     "Aşağıdaki grafiğe göre değer nedir?", "Şekildeki yapı nedir?", "Diyagramda ok neyi gösterir?",
                     "Şekilde gösterilen yapı nedir?"):
            self.assertRejected(question(question=text), "reference", image=False)

    def test_ordinary_uses_of_these_words_stay_legal_in_every_question(self):
        for text in ("Which figure of speech compares two unlike things?", "What is the chart of accounts used for?",
                     "What is the graph of a linear function?", "What is the image of 2 under the function f?",
                     "How is the area under the curve calculated?", "Who is the central figure of the Reformation?",
                     "What is a bar chart used for?", "Hücre bu şekilde nasıl bölünür?",
                     "Enzimler hızlı bir şekilde nasıl çalışır?", "Proteinler aşağıdaki şekilde sınıflandırılır: hangisi doğrudur?",
                     "Doğrusal bir fonksiyonun grafiği nasıldır?"):
            self.assertAccepted(question(question=text), image=False)

    def test_turned_off_figure_questions_do_not_pass_the_check(self):
        self.image()
        self.env["EXAM_FORGE_SETTINGS"] = str(self.write("cfg.json", {"figure_questions": False}))
        code, out = self.errors(figure_question(), image=False)
        self.assertNotEqual(code, 0)
        self.assertIn("figure_questions", out)


class Pipeline(Base):
    def test_replace_keeps_the_figure_fields(self):
        self.image()
        p = self.chunk([question()])
        fix = self.write("run/lesson/fix-1-round2.json", {"questions": [figure_question()]})
        self.assertEqual(self.f("replace", p, fix).returncode, 0)
        q = json.loads(p.read_text(encoding="utf-8"))["questions"][0]
        self.assertEqual(q["figure"], figure_question()["figure"])
        self.assertEqual(q["source"]["kind"], "figure")
        self.assertEqual(self.f("check", p).returncode, 0)

    def test_replace_from_another_folder_still_finds_the_figure(self):
        self.image("run/fixes/figures/fig-p3-1.png")
        p = self.chunk([question()])
        fix = self.write("run/fixes/fix-1-round2.json", {"questions": [figure_question()]})
        self.assertEqual(self.f("replace", p, fix).returncode, 0)
        r = self.f("check", p)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_source_pages_keeps_the_figure_page_in_sight(self):
        q = figure_question(page=9)
        p = self.chunk([q])
        r = self.f("source-pages", p, "--ids", q["id"])
        self.assertEqual(r.stdout.strip(), "2-4,8-10")

    def test_source_pages_stays_inside_the_chunk(self):
        q = figure_question(page=8)
        p = self.chunk([q], page_start=2, page_end=8)
        self.assertEqual(self.f("source-pages", p, "--ids", q["id"]).stdout.strip(), "2-4,7-8")

    def book(self, *questions, folder="run/lesson"):
        """A book the normal way: chunk, then merge. Every question arrives verified."""
        for q in questions:
            q["verified"] = True
        self.chunk(list(questions), name=f"{folder}/chunk-1.json")
        out = self.d / "run" / "book-x.json"
        r = self.f("merge", self.d / folder, "--out", out, "--title", "Book", "--format", "mcq")
        self.assertEqual(r.returncode, 0, r.stderr)
        return out

    def stub_browser(self):
        browser = self.d / "browser"
        browser.write_text('#!/bin/sh\nfor a; do case "$a" in --print-to-pdf=*) : > "${a#--print-to-pdf=}";; esac; done\n')
        browser.chmod(browser.stat().st_mode | stat.S_IEXEC)
        self.env["EXAM_FORGE_BROWSER"] = str(browser)

    def render(self, book, *extra, cwd=None):
        self.stub_browser()
        out = self.d / "out" / "book.html"
        out.parent.mkdir(exist_ok=True)
        r = self.f("render", book, self.d / "out" / "book.pdf", "--html", out, *extra, cwd=cwd)
        self.assertEqual(r.returncode, 0, r.stderr)
        return out.read_text(encoding="utf-8")

    def test_merge_keeps_the_fields_and_points_at_the_file_from_the_book(self):
        self.image()
        book = self.book(figure_question())
        q = json.loads(book.read_text(encoding="utf-8"))["parts"][0]["questions"][0]
        self.assertEqual({k: q["figure"][k] for k in ("id", "page", "caption")},
                         {"id": "fig-p3-1", "page": 3, "caption": "Membrane potential over time"})
        self.assertEqual(q["source"]["kind"], "figure")
        self.assertTrue((book.parent / q["figure"]["file"]).is_file())

    def test_merge_of_two_folders_keeps_each_ones_figure(self):
        self.image("run/one/figures/fig-p3-1.png", png_bytes(seed=1))
        self.image("run/two/figures/fig-p3-1.png", png_bytes(seed=2))
        for name in ("one", "two"):
            q = figure_question()
            q["verified"] = True
            self.chunk([q], name=f"run/{name}/chunk-1.json", source=f"{name}.pdf")
        out = self.d / "run" / "book.json"
        self.assertEqual(self.f("merge", self.d / "run/one", self.d / "run/two", "--out", out, "--title", "B",
                                "--format", "mcq").returncode, 0)
        html = self.render(out)
        for seed in (1, 2):
            self.assertIn(base64.b64encode(png_bytes(seed=seed)).decode(), html)

    def test_render_embeds_the_image_so_the_book_stands_alone(self):
        data = self.image().read_bytes()
        html = self.render(self.book(figure_question()))
        self.assertIn(f'data:image/png;base64,{base64.b64encode(data).decode()}"', html)
        self.assertNotIn("fig-p3-1.png", html)

    def test_render_works_from_any_folder(self):
        self.image()
        elsewhere = self.d / "elsewhere"
        elsewhere.mkdir()
        self.assertIn("data:image/png", self.render(self.book(figure_question()), cwd=elsewhere))

    def test_render_of_a_book_moved_with_its_run_folder(self):
        self.image()
        book = self.book(figure_question())
        (self.d / "run").rename(self.d / "moved")
        self.assertIn("data:image/png", self.render(self.d / "moved" / book.name))

    def test_the_figure_sits_between_the_stem_and_the_options(self):
        self.image()
        html = self.render(self.book(figure_question()))
        article = re.search(r'<article class="question">.*?</article>', html, re.S).group(0)
        self.assertLess(article.index('class="stem"'), article.index("<figure"))
        self.assertLess(article.index("<figure"), article.index('class="options"'))
        self.assertIn("<figcaption>Membrane potential over time</figcaption>", article)

    def test_the_caption_is_escaped_and_the_alt_text_is_there(self):
        self.image()
        html = self.render(self.book(figure_question(caption='Ions <b>& "gates"')))
        self.assertIn("<figcaption>Ions &lt;b&gt;&amp; &quot;gates&quot;</figcaption>", html)
        self.assertIn('alt="Ions &lt;b&gt;&amp; &quot;gates&quot;"', html)

    def test_without_a_caption_there_is_no_empty_caption(self):
        self.image()
        q = figure_question()
        del q["figure"]["caption"]
        html = self.render(self.book(q))
        self.assertIn("<figure", html)
        self.assertNotIn("<figcaption", html)

    def test_every_answer_placement_prints_the_figure_once_with_its_question(self):
        self.image()
        book = self.book(figure_question())
        for placement in ("book_end", "section_end", "inline"):
            html = self.render(book, "--answers", placement)
            self.assertEqual(html.count("<figure"), 1, placement)
            article = re.search(r'<article class="question">.*?</article>', html, re.S).group(0)
            self.assertIn("<figure", article)

    def test_inline_answers_come_after_the_figure(self):
        self.image()
        html = self.render(self.book(figure_question()), "--answers", "inline")
        self.assertLess(html.index("<figure"), html.index('class="inline-answer"'))

    def test_a_book_without_figures_prints_none(self):
        self.assertNotIn("<figure", self.render(self.book(question())))

    def test_a_missing_file_fails_clearly_and_touches_nothing(self):
        png = self.image()
        book = self.book(figure_question())
        self.stub_browser()
        old = self.d / "out" / "book.pdf"
        old.parent.mkdir()
        old.write_bytes(b"the previous book")
        png.unlink()
        html = self.d / "out" / "book.html"
        r = self.f("render", book, old, "--html", html)
        self.assertEqual(r.returncode, 2)
        self.assertIn("fig-p3-1.png", r.stderr)
        self.assertIn("p1-q01", r.stderr)
        self.assertEqual(old.read_bytes(), b"the previous book")
        self.assertFalse(html.exists())
        self.assertEqual(sorted(p.name for p in old.parent.iterdir()), ["book.pdf"])

    def test_a_damaged_file_fails_too(self):
        png = self.image()
        book = self.book(figure_question())
        png.write_bytes(b"\x89PNG\r\n\x1a\nbroken")
        self.stub_browser()
        r = self.f("render", book, self.d / "out.pdf")
        self.assertEqual(r.returncode, 2)
        self.assertIn("PNG", r.stderr)

    def test_build_book_html_called_directly_cannot_print_a_broken_image(self):
        sys.path.insert(0, str(FORGE.parent))
        self.addCleanup(sys.path.remove, str(FORGE.parent))
        forge = importlib.import_module("forge")
        book = {"title": "B", "format": "mcq", "language": "en", "date": "2026-01-01", "sources": ["a.pdf"],
                "parts": [{"name": "A", "pdf": "a.pdf", "questions": [figure_question()]}]}
        with self.assertRaises(SystemExit):
            forge.build_book_html(book, "book_end", base=self.d)

    def test_the_stylesheet_keeps_figures_whole_and_small_enough_for_print(self):
        css = (SKILL / "templates" / "book.css").read_text(encoding="utf-8")
        rule = re.search(r"\.q-figure\s*\{[^}]*\}", css).group(0)
        self.assertRegex(rule, r"break-inside:\s*avoid")
        img = re.search(r"\.q-figure img\s*\{[^}]*\}", css).group(0)
        self.assertIn("max-width: 100%", img)
        self.assertRegex(img, r"max-height:\s*\d+mm")


class Setting(Base):
    def test_default_is_on(self):
        self.assertIs(json.loads(self.f("settings").stdout)["figure_questions"], True)

    def test_schema_entry_has_both_languages(self):
        schema = json.loads((SKILL / "settings.schema.json").read_text(encoding="utf-8"))
        [entry] = [s for s in schema["settings"] if s["key"] == "figure_questions"]
        self.assertEqual(entry["type"], "bool")
        self.assertEqual(entry["group"], "questions")
        for field in ("label", "help"):
            self.assertTrue(entry[field]["en"])
            self.assertTrue(entry[field]["tr"])

    def test_it_can_be_turned_off_and_only_takes_a_boolean(self):
        self.assertEqual(self.f("config", "set", "figure_questions", "false").returncode, 0)
        self.assertIs(json.loads(self.f("settings").stdout)["figure_questions"], False)
        self.assertEqual(self.f("config", "set", "figure_questions", "perhaps").returncode, 2)


class Docs(unittest.TestCase):
    @staticmethod
    def read(path):
        return (ROOT / path).read_text(encoding="utf-8")

    def test_the_helper_lists_the_command(self):
        self.assertIn("figures <pdf>", FORGE.read_text(encoding="utf-8").split('"""')[1])

    def test_skill_runs_figures_and_passes_the_setting(self):
        text = self.read("skills/exam-forge/SKILL.md")
        self.assertIn("s.figure_questions", text)
        self.assertRegex(text, r'\$PY "\$FORGE" figures "<pdf>" <start>-<end> --out "<run>/<stem>/figures"')

    def test_editor_is_told_how_to_use_figures_and_keeps_rule_2(self):
        text = self.read("agents/editor.md")
        self.assertIn('"figure": {"id"', text)
        self.assertIn('"kind": "figure"', text)
        self.assertIn("No references to the source.", text)
        self.assertRegex(text, r"(?i)never invent")

    def test_reviewer_opens_the_crop_and_has_a_reason_for_it(self):
        text = self.read("agents/reviewer.md")
        self.assertRegex(text, r"(?i)crop")
        for word in ("unreadable", "cut off", "not visible"):
            self.assertIn(word, text)
        self.assertRegex(text, r"\|figure\b")

    def test_chief_editor_opens_the_crop(self):
        self.assertRegex(self.read("agents/chief-editor.md"), r"(?i)crop")

    def test_screener_treats_a_figure_question_as_standalone(self):
        text = self.read("agents/screener.md")
        self.assertIn("figure", text)
        self.assertIn("standalone", text)

    def test_readme_covers_it_in_english_and_turkish(self):
        text = self.read("README.md")
        privacy = text.split("## Privacy and copyright")[1].split("\n## ")[0]
        self.assertRegex(privacy, r"(?i)figures")
        self.assertRegex(privacy, r"(?i)right to use")
        self.assertIn("`figure_questions`", text.split("## Settings")[1].split("\n## ")[0])
        self.assertRegex(text.split("## What you get")[1].split("\n## ")[0], r"(?i)figure")
        self.assertRegex(text.split("## Türkçe özet")[1], r"(?i)şekil|grafik")

    def test_changelog_has_it_under_unreleased(self):
        self.assertRegex(self.read("CHANGELOG.md").split("## [0.")[0], r"(?i)figure")


if __name__ == "__main__":
    unittest.main()
