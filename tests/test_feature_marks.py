"""Marked text: highlight and underline mean important, only a line through the text means out of scope.

Fixture PDFs are built on the fly with pymupdf. Drawn marks are placed from real word positions.
Run: python3 -m unittest tests/test_feature_marks.py
"""
import argparse
import contextlib
import io
import json
import os
import re
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

fitz = forge.load_fitz()
needs_pymupdf = unittest.skipUnless(fitz, "pymupdf is not installed")

BODY = [
    "Mitochondria produce energy for the cell through respiration.",
    "Ribosomes assemble proteins from amino acids in the cytoplasm.",
    "The nucleus stores genetic material inside a double membrane.",
    "Lysosomes digest worn organelles with strong acidic enzymes.",
]
FIRST_Y, LEADING = 150, 40


def cli(*args, env=None):
    e = dict(os.environ)
    e.pop("EXAM_FORGE_SETTINGS", None)
    e.update(env or {})
    return subprocess.run([sys.executable, str(FORGE), *map(str, args)], capture_output=True, text=True,
                          encoding="utf-8", env=e)


def write_text(page, lines=BODY, size=12, x=72, y=FIRST_Y, leading=LEADING):
    for i, line in enumerate(lines):
        page.insert_text((x, y + i * leading), line, fontsize=size)


def word(page, text, nth=0):
    found = [w for w in page.get_text("words") if w[4].strip(".,") == text]
    return fitz.Rect(found[nth][:4])


def baseline(page, text):
    """The real baseline of the line holding this word."""
    for block in page.get_text("rawdict")["blocks"]:
        for line in block.get("lines", []):
            for span in line["spans"]:
                if text in "".join(c["c"] for c in span["chars"]):
                    return span["origin"][1], span["size"]
    raise AssertionError(text)


def underline_at(page, text, drop=0.12, **kw):
    r, (y, size) = word(page, text), baseline(page, text)
    page.draw_line((r.x0, y + drop * size), (r.x1, y + drop * size), **{"color": (0, 0, 0), "width": 0.8, **kw})


def strike_at(page, text, **kw):
    r, (y, size) = word(page, text), baseline(page, text)
    mid = y - 0.26 * size  # the middle of the x-height
    page.draw_line((r.x0, mid), (r.x1, mid), **{"color": (0, 0, 0), "width": 0.8, **kw})


class Case(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)

    def pdf(self, *builders, name="notes.pdf", size=(595, 842)):
        """One page per builder. A builder draws on a page that already holds BODY, unless it says text=False."""
        doc = fitz.open()
        for build in builders:
            page = doc.new_page(width=size[0], height=size[1])
            if getattr(build, "blank", False) is False:
                write_text(page)
            build(page)
        path = self.dir / name
        doc.save(str(path))
        doc.close()
        return path

    def marks(self, path, rng="1-99"):
        r = cli("marks", path, rng)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def only(self, build, **kw):
        found = self.marks(self.pdf(build, **kw))["marks"]
        self.assertEqual(len(found), 1, found)
        return found[0]

    def none(self, build, **kw):
        self.assertEqual(self.marks(self.pdf(build, **kw))["marks"], [])


def blank(fn):
    fn.blank = True
    return fn


@needs_pymupdf
class Annotations(Case):
    def test_highlight_annotation_is_emphasis(self):
        m = self.only(lambda p: p.add_highlight_annot(word(p, "energy")))
        self.assertEqual((m["kind"], m["role"], m["origin"]), ("highlight", "emphasis", "annotation"))
        self.assertEqual(m["text"], "energy")

    def test_underline_annotation_is_emphasis_never_excluded(self):
        m = self.only(lambda p: p.add_underline_annot(word(p, "energy")))
        self.assertEqual((m["kind"], m["role"], m["origin"]), ("underline", "emphasis", "annotation"))

    def test_squiggly_annotation_is_emphasis(self):
        m = self.only(lambda p: p.add_squiggly_annot(word(p, "energy")))
        self.assertEqual((m["kind"], m["role"]), ("underline", "emphasis"))

    def test_strikeout_annotation_is_excluded(self):
        m = self.only(lambda p: p.add_strikeout_annot(word(p, "energy")))
        self.assertEqual((m["kind"], m["role"], m["origin"]), ("strikethrough", "excluded", "annotation"))
        self.assertEqual(m["text"], "energy")

    def test_underline_and_strikeout_on_one_page_stay_apart(self):
        def build(p):
            p.add_underline_annot(word(p, "energy"))
            p.add_strikeout_annot(word(p, "proteins"))
        found = {m["text"]: m["role"] for m in self.marks(self.pdf(build))["marks"]}
        self.assertEqual(found, {"energy": "emphasis", "proteins": "excluded"})

    def test_text_comes_from_quads_not_the_whole_rectangle(self):
        def build(p):  # one annotation on two lines: its rectangle holds much more than its quads
            a, b = word(p, "cell"), word(p, "Ribosomes")
            p.add_highlight_annot([a, b])
        m = self.only(build)
        self.assertEqual(m["text"].split(), ["cell", "Ribosomes"])

    def test_part_of_a_word_is_returned_as_marked(self):
        def build(p):
            r = word(p, "Mitochondria")
            p.add_highlight_annot(fitz.Rect(r.x0, r.y0, r.x0 + r.width * 0.42, r.y1))
        self.assertLess(len(self.only(build)["text"]), len("Mitochondria"))

    def test_annotation_is_not_counted_again_as_a_drawn_mark(self):
        def build(p):
            p.add_highlight_annot(word(p, "energy"))
            p.add_underline_annot(word(p, "proteins"))
            p.add_strikeout_annot(word(p, "nucleus"))
        result = self.marks(self.pdf(build))
        self.assertEqual({m["origin"] for m in result["marks"]}, {"annotation"})
        self.assertEqual(len(result["marks"]), 3)

    def test_notes_are_left_to_the_notes_command(self):
        def build(p):
            p.add_text_annot((80, 90), "remember this")
            p.add_freetext_annot(fitz.Rect(300, 60, 500, 90), "see lecture")
            p.add_ink_annot([[(72, 200), (200, 200)]])
        self.none(build)

    def test_annotation_on_a_page_without_text_is_kept_with_empty_text(self):
        @blank
        def build(p):
            p.add_highlight_annot(fitz.Rect(72, 100, 200, 120))
        m = self.only(build)
        self.assertEqual((m["role"], m["text"]), ("emphasis", ""))


@needs_pymupdf
class InkMarks(Case):
    """Tablet pens save an underline as ink, not as an underline annotation."""

    def stroke(self, page, y, x0=None, x1=None, wobble=0):
        r = word(page, "energy")
        x0, x1 = x0 or r.x0, x1 or r.x1
        points = [(x0 + (x1 - x0) * i / 6, y + (wobble if i % 2 else -wobble)) for i in range(7)]
        return page.add_ink_annot([points])

    def test_pen_line_under_a_word_is_emphasis(self):
        m = self.only(lambda p: self.stroke(p, baseline(p, "energy")[0] + 2))
        self.assertEqual((m["kind"], m["role"], m["origin"]), ("underline", "emphasis", "annotation"))
        self.assertEqual(m["text"], "energy")

    def test_shaky_pen_line_under_a_word_is_still_emphasis(self):
        m = self.only(lambda p: self.stroke(p, baseline(p, "energy")[0] + 2, wobble=1.2))
        self.assertEqual((m["kind"], m["role"]), ("underline", "emphasis"))

    def test_pen_line_through_a_word_excludes_it(self):
        m = self.only(lambda p: self.stroke(p, baseline(p, "energy")[0] - 4))
        self.assertEqual((m["kind"], m["role"]), ("strikethrough", "excluded"))

    def test_highlighter_pen_is_emphasis(self):
        def build(p):
            a = self.stroke(p, baseline(p, "energy")[0] - 3)
            a.set_border(width=13)
            a.set_colors(stroke=(1, 0.9, 0))
            a.set_opacity(0.4)
            a.update()
        m = self.only(build)
        self.assertEqual((m["kind"], m["role"]), ("highlight", "emphasis"))

    def test_handwriting_is_no_mark(self):
        def build(p):
            y = baseline(p, "energy")[0] + 80
            for i in range(8):
                p.add_ink_annot([[(100 + i * 14, y), (106 + i * 14, y - 12), (112 + i * 14, y + 4)]])
        self.assertEqual(self.marks(self.pdf(build))["marks"], [])

    def test_pen_stroke_touching_no_text_is_no_mark(self):
        def build(p):
            self.stroke(p, 700, x0=100, x1=300)
        self.assertEqual(self.marks(self.pdf(build))["marks"], [])

    def test_pen_underline_on_a_turned_page(self):
        def build(p):
            self.stroke(p, baseline(p, "energy")[0] + 2)
            p.set_rotation(90)
        m = self.only(build)
        self.assertEqual((m["kind"], m["role"], m["text"]), ("underline", "emphasis", "energy"))


@needs_pymupdf
class DrawnHighlights(Case):
    def test_translucent_rectangle_is_a_highlight(self):
        m = self.only(lambda p: p.draw_rect(word(p, "energy"), color=None, fill=(1, 1, 0), fill_opacity=0.4))
        self.assertEqual((m["kind"], m["role"], m["origin"], m["text"]), ("highlight", "emphasis", "drawn", "energy"))

    def test_pale_opaque_rectangle_behind_words_is_a_highlight(self):
        m = self.only(lambda p: p.draw_rect(word(p, "energy"), color=None, fill=(1, 0.95, 0.5), overlay=False))
        self.assertEqual((m["kind"], m["text"]), ("highlight", "energy"))

    def test_saturated_yellow_behind_words_is_a_highlight(self):
        self.assertEqual(self.only(lambda p: p.draw_rect(word(p, "energy"), color=None, fill=(1, 1, 0),
                                                         overlay=False))["kind"], "highlight")

    def test_white_grey_and_dark_fills_are_not_highlights(self):
        def build(p):
            p.draw_rect(word(p, "energy"), color=None, fill=(1, 1, 1), overlay=False)
            p.draw_rect(word(p, "proteins"), color=None, fill=(0.9, 0.9, 0.9), overlay=False)
            p.draw_rect(word(p, "nucleus"), color=None, fill=(0.1, 0.1, 0.4), overlay=False)
        self.none(build)

    def test_rectangle_on_empty_space_is_not_a_highlight(self):
        self.none(lambda p: p.draw_rect(fitz.Rect(72, 300, 300, 316), color=None, fill=(1, 1, 0), fill_opacity=0.4))

    def test_coloured_page_background_and_large_panel_are_not_highlights(self):
        def build(p):
            p.draw_rect(p.rect, color=None, fill=(1, 0.97, 0.85), overlay=False)
            p.draw_rect(fitz.Rect(60, 130, 540, 280), color=None, fill=(0.85, 0.92, 1), overlay=False)
        self.none(build)

    def test_highlight_split_over_two_lines_is_one_mark(self):
        def build(p):
            a, b = word(p, "respiration"), word(p, "Ribosomes")
            start = word(p, "through")
            p.draw_rect(fitz.Rect(start.x0, a.y0, a.x1, a.y1), color=None, fill=(1, 1, 0), fill_opacity=0.4)
            p.draw_rect(b, color=None, fill=(1, 1, 0), fill_opacity=0.4)
        m = self.only(build)
        self.assertEqual(m["text"].split(), ["through", "respiration.", "Ribosomes"])

    def test_two_separate_highlights_stay_separate(self):
        def build(p):
            p.draw_rect(word(p, "energy"), color=None, fill=(1, 1, 0), fill_opacity=0.4)
            p.draw_rect(word(p, "nucleus"), color=None, fill=(1, 1, 0), fill_opacity=0.4)
        self.assertEqual(len(self.marks(self.pdf(build))["marks"]), 2)


@needs_pymupdf
class DrawnLines(Case):
    """The core of the feature: the position of a line decides between important and cancelled."""

    def test_line_below_the_baseline_is_an_underline(self):
        m = self.only(lambda p: underline_at(p, "energy"))
        self.assertEqual((m["kind"], m["role"], m["origin"], m["text"]), ("underline", "emphasis", "drawn", "energy"))

    def test_line_on_the_baseline_is_an_underline(self):
        self.assertEqual(self.only(lambda p: underline_at(p, "energy", drop=0))["role"], "emphasis")

    def test_line_deep_under_descenders_is_an_underline(self):
        self.assertEqual(self.only(lambda p: underline_at(p, "energy", drop=0.3))["role"], "emphasis")

    def test_line_through_the_x_height_is_a_strikethrough(self):
        m = self.only(lambda p: strike_at(p, "energy"))
        self.assertEqual((m["kind"], m["role"], m["origin"], m["text"]),
                         ("strikethrough", "excluded", "drawn", "energy"))

    def test_position_decides_at_every_font_size(self):
        for size in (7, 9, 12, 18, 30, 48):
            with self.subTest(size=size):
                @blank
                def build(p):
                    write_text(p, ["alpha beta"], size=size)
                    underline_at(p, "alpha", width=max(0.5, size / 15))
                    strike_at(p, "beta", width=max(0.5, size / 15))
                found = {m["text"]: (m["kind"], m["role"]) for m in self.marks(self.pdf(build))["marks"]}
                self.assertEqual(found, {"alpha": ("underline", "emphasis"), "beta": ("strikethrough", "excluded")})

    def test_position_decides_with_a_serif_font(self):
        @blank
        def build(p):
            p.insert_text((72, 200), "alpha beta", fontsize=14, fontname="tiro")
            underline_at(p, "alpha")
            strike_at(p, "beta")
        found = {m["text"]: m["role"] for m in self.marks(self.pdf(build))["marks"]}
        self.assertEqual(found, {"alpha": "emphasis", "beta": "excluded"})

    def test_position_is_read_from_the_baseline_not_the_word_box(self):
        """A line through the box's middle is low on the letters: a box test would call an underline a strike."""
        def build(p):
            r, (y, _) = word(p, "energy"), baseline(p, "energy")
            self.assertLess((r.y0 + r.y1) / 2, y)  # the box's middle is above the baseline
            p.draw_line((r.x0, y + 1), (r.x1, y + 1), color=(0, 0, 0), width=0.5)
        self.assertEqual(self.only(build)["kind"], "underline")

    def test_coloured_lines_follow_the_same_rule(self):
        def build(p):
            underline_at(p, "energy", color=(1, 0, 0))
            strike_at(p, "proteins", color=(1, 0, 0))
        found = {m["text"]: m["role"] for m in self.marks(self.pdf(build))["marks"]}
        self.assertEqual(found, {"energy": "emphasis", "proteins": "excluded"})

    def test_thin_filled_rectangles_count_as_lines(self):
        def build(p):
            r, (y, _) = word(p, "energy"), baseline(p, "energy")
            p.draw_rect(fitz.Rect(r.x0, y + 1, r.x1, y + 1.7), color=None, fill=(0, 0, 0))
            r, (y, _) = word(p, "proteins"), baseline(p, "proteins")
            p.draw_rect(fitz.Rect(r.x0, y - 3.4, r.x1, y - 2.8), color=None, fill=(0, 0, 0))
        found = {m["text"]: (m["kind"], m["role"]) for m in self.marks(self.pdf(build))["marks"]}
        self.assertEqual(found, {"energy": ("underline", "emphasis"), "proteins": ("strikethrough", "excluded")})

    def test_thick_translucent_marker_through_the_middle_is_a_highlight_not_a_strike(self):
        def build(p):
            r, (y, size) = word(p, "energy"), baseline(p, "energy")
            p.draw_line((r.x0, y - 0.3 * size), (r.x1, y - 0.3 * size), color=(1, 1, 0), width=size,
                        stroke_opacity=0.4)
        m = self.only(build)
        self.assertEqual((m["kind"], m["role"]), ("highlight", "emphasis"))

    def test_pale_thin_marker_through_the_middle_is_never_excluded(self):
        self.assertEqual(self.only(lambda p: strike_at(p, "energy", color=(1, 1, 0.4), width=1.5))["role"], "emphasis")

    def test_line_above_the_letters_is_neither(self):
        def build(p):
            r, (y, size) = word(p, "energy"), baseline(p, "energy")
            p.draw_line((r.x0, y - 0.85 * size), (r.x1, y - 0.85 * size), color=(0, 0, 0), width=0.8)
        self.none(build)

    def test_invisible_lines_are_ignored(self):
        self.none(lambda p: strike_at(p, "energy", color=(1, 1, 1)))

    def test_only_the_words_on_the_line_are_marked(self):
        self.assertEqual(self.only(lambda p: strike_at(p, "energy"))["text"], "energy")

    def test_line_across_several_words(self):
        def build(p):
            a, b = word(p, "produce"), word(p, "energy")
            y, _ = baseline(p, "energy")
            p.draw_line((a.x0, y + 1.5), (b.x1, y + 1.5), color=(0, 0, 0), width=0.8)
        self.assertEqual(self.only(build)["text"], "produce energy")

    def test_a_line_per_word_becomes_one_mark(self):
        def build(p):
            underline_at(p, "produce")
            underline_at(p, "energy")
        m = self.only(build)
        self.assertEqual((m["kind"], m["text"]), ("underline", "produce energy"))

    def test_underlined_and_struck_neighbours_are_not_merged(self):
        def build(p):
            underline_at(p, "produce")
            strike_at(p, "energy")
        self.assertEqual({m["role"] for m in self.marks(self.pdf(build))["marks"]}, {"emphasis", "excluded"})

    def test_a_full_underlined_line_of_body_text_is_kept(self):
        def build(p):
            a, b = word(p, "Mitochondria"), word(p, "respiration")
            y, _ = baseline(p, "energy")
            p.draw_line((a.x0, y + 1.5), (b.x1, y + 1.5), color=(0, 0, 0), width=0.8)
        self.assertEqual(self.only(build)["kind"], "underline")

    def test_styles_from_another_layout_engine(self):
        """MuPDF's HTML layout places these lines itself, so nothing here depends on the helpers above."""
        @blank
        def build(p):
            p.insert_htmlbox(fitz.Rect(60, 60, 520, 400), (
                '<p style="font-size:13pt">Plain <u>first underlined words</u> and plain <s>first struck words</s> '
                'and plain <span style="background-color:#ffff66">some marked words</span> and plain '
                '<u>Şeker İnsülin</u> then plain <s>Çay Öğle</s> end.</p>'))
        found = {m["text"]: (m["kind"], m["role"], m["origin"]) for m in self.marks(self.pdf(build))["marks"]}
        self.assertEqual(found, {
            "first underlined words": ("underline", "emphasis", "drawn"),
            "first struck words": ("strikethrough", "excluded", "drawn"),
            "some marked words": ("highlight", "emphasis", "drawn"),
            "Şeker İnsülin": ("underline", "emphasis", "drawn"),
            "Çay Öğle": ("strikethrough", "excluded", "drawn"),
        })

    def test_every_line_of_an_underlined_paragraph_is_kept(self):
        def build(p):
            for first, last in (("Mitochondria", "respiration"), ("Ribosomes", "cytoplasm"), ("The", "membrane")):
                a, b = word(p, first), word(p, last)
                y, _ = baseline(p, last)
                p.draw_line((72, y + 1.5), (b.x1, y + 1.5), color=(0, 0, 0), width=0.8)
        self.assertEqual({m["kind"] for m in self.marks(self.pdf(build))["marks"]}, {"underline"})


@needs_pymupdf
class IgnoredLines(Case):
    def test_page_wide_line_is_ignored(self):
        def build(p):
            y, _ = baseline(p, "energy")
            p.draw_line((10, y + 1.5), (585, y + 1.5), color=(0, 0, 0), width=0.8)
        self.none(build)

    def test_page_wide_line_through_text_is_not_a_strike(self):
        def build(p):
            y, size = baseline(p, "energy")
            p.draw_line((10, y - 0.26 * size), (585, y - 0.26 * size), color=(0, 0, 0), width=0.8)
        self.none(build)

    def test_header_and_footer_rules_are_ignored(self):
        @blank
        def build(p):
            p.insert_text((72, 40), "Cell biology lecture notes and reading list for the spring term", fontsize=11)
            p.insert_text((72, 810), "Department of Biology, printed for the spring term, page 3 of 40", fontsize=9)
            write_text(p)
            a, b = word(p, "Cell"), word(p, "term")
            p.draw_line((a.x0, 42), (b.x1, 42), color=(0, 0, 0), width=0.6)
            a, b = word(p, "Department"), word(p, "40")
            p.draw_line((a.x0, 811), (b.x1, 811), color=(0, 0, 0), width=0.6)
        self.none(build)

    def test_table_rules_with_columns_are_ignored(self):
        @blank
        def build(p):
            rows = ["Organelle  Function", "Ribosome  Proteins", "Lysosome  Digestion"]
            for i, row in enumerate(rows):
                p.insert_text((80, 200 + i * 20), row, fontsize=12)
                y = 202 + i * 20  # rules hug the text, the worst case
                p.draw_line((76, y), (280, y), color=(0, 0, 0), width=0.5)
            p.draw_line((76, 182), (280, 182), color=(0, 0, 0), width=0.5)
            for x in (76, 180, 280):
                p.draw_line((x, 182), (x, 242), color=(0, 0, 0), width=0.5)
        self.none(build)

    def test_table_rules_without_columns_are_ignored(self):
        @blank
        def build(p):
            for i, (a, b) in enumerate([("Organelle", "Function"), ("Ribosome", "Proteins"), ("Lysosome", "Digestion")]):
                p.insert_text((80, 200 + i * 20), a, fontsize=12)
                p.insert_text((300, 200 + i * 20), b, fontsize=12)
                y = 202 + i * 20
                p.draw_line((76, y), (420, y), color=(0, 0, 0), width=0.5)
        self.none(build)

    def test_cell_borders_drawn_as_rectangles_are_ignored(self):
        @blank
        def build(p):
            p.insert_text((80, 200), "Ribosome", fontsize=12)
            r = word(p, "Ribosome")
            p.draw_rect(fitz.Rect(r.x0 - 2, r.y0, r.x1 + 2, 202), color=(0, 0, 0), width=0.5)
        self.none(build)

    def test_line_touching_no_text_is_ignored(self):
        def build(p):
            p.draw_line((72, 500), (300, 500), color=(0, 0, 0), width=0.8)
            p.draw_line((72, FIRST_Y + LEADING / 2), (300, FIRST_Y + LEADING / 2), color=(0, 0, 0), width=0.8)
        self.none(build)

    def test_rule_much_wider_than_a_short_title_is_ignored(self):
        @blank
        def build(p):
            p.insert_text((72, 200), "Summary", fontsize=16)
            y, _ = baseline(p, "Summary")
            p.draw_line((72, y + 2), (400, y + 2), color=(0, 0, 0), width=0.8)
        self.none(build)

    def test_vertical_and_diagonal_lines_are_ignored(self):
        def build(p):
            r = word(p, "energy")
            p.draw_line((r.x0, r.y0), (r.x0, r.y1), color=(0, 0, 0))
            p.draw_line((r.x0, r.y0), (r.x1, r.y1), color=(0, 0, 0))
        self.none(build)

    def test_link_underline_is_not_emphasis(self):
        def build(p):
            underline_at(p, "energy", color=(0, 0, 1))
            p.insert_link({"kind": fitz.LINK_URI, "from": word(p, "energy"), "uri": "https://example.com"})
        self.none(build)


@needs_pymupdf
class Command(Case):
    def test_output_shape(self):
        def build(p):
            p.add_highlight_annot(word(p, "energy"))
            strike_at(p, "proteins")
        result = self.marks(self.pdf(build))
        self.assertEqual(sorted(result["marks"][0]),
                         ["bbox", "id", "kind", "origin", "page", "role", "text"])
        self.assertEqual(result["counts"]["total"], 2)
        self.assertEqual(result["counts"]["emphasis"], 1)
        self.assertEqual(result["counts"]["excluded"], 1)
        self.assertEqual(result["counts"]["highlight"], 1)
        self.assertEqual(result["counts"]["strikethrough"], 1)
        self.assertEqual(result["counts"]["underline"], 0)
        self.assertEqual(result["counts"]["annotation"], 1)
        self.assertEqual(result["counts"]["drawn"], 1)
        bbox = result["marks"][0]["bbox"]
        self.assertEqual(len(bbox), 4)
        self.assertLess(bbox[0], bbox[2])
        self.assertLess(bbox[1], bbox[3])

    def test_ids_count_per_page_in_reading_order(self):
        def first(p):
            underline_at(p, "nucleus")
            p.add_highlight_annot(word(p, "energy"))

        def second(p):
            strike_at(p, "proteins")
        found = self.marks(self.pdf(first, second))["marks"]
        self.assertEqual([m["id"] for m in found], ["mark-p1-1", "mark-p1-2", "mark-p2-1"])
        self.assertEqual([m["text"] for m in found], ["energy", "nucleus", "proteins"])
        self.assertEqual([m["page"] for m in found], [1, 1, 2])

    def test_ids_use_physical_pages_in_a_later_range(self):
        def mark(p):
            underline_at(p, "energy")
        found = self.marks(self.pdf(mark, mark, mark), "2-3")["marks"]
        self.assertEqual([m["id"] for m in found], ["mark-p2-1", "mark-p3-1"])

    def test_range_past_the_end_is_clamped(self):
        self.assertEqual(self.marks(self.pdf(lambda p: None), "1-500")["marks"], [])

    def test_single_page_range(self):
        def mark(p):
            underline_at(p, "energy")
        self.assertEqual([m["page"] for m in self.marks(self.pdf(mark, mark), "2")["marks"]], [2])

    def test_out_writes_the_file(self):
        path = self.pdf(lambda p: underline_at(p, "energy"))
        out = self.dir / "sub" / "marks-1.json"
        r = cli("marks", path, "1-1", "--out", out)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(out.read_text(encoding="utf-8"))["marks"][0]["id"], "mark-p1-1")

    def test_a_plain_pdf_has_no_marks(self):
        result = self.marks(self.pdf(lambda p: None))
        self.assertEqual(result["marks"], [])
        self.assertEqual(result["counts"]["total"], 0)

    def test_scanned_pages_are_named(self):
        @blank
        def scan(p):
            pix = fitz.Pixmap(fitz.csGRAY, fitz.IRect(0, 0, 40, 40))
            pix.clear_with(200)
            p.insert_image(p.rect, pixmap=pix)
        result = self.marks(self.pdf(lambda p: None, scan))
        self.assertEqual(result["scanned_pages"], [2])

    def test_bad_range_exits_2(self):
        path = self.pdf(lambda p: None)
        for rng in ("x", "0-3", "5-2", "3-"):
            with self.subTest(rng=rng):
                r = cli("marks", path, rng)
                self.assertEqual(r.returncode, 2)
                self.assertIn("range", r.stderr)

    def test_missing_pdf_exits_2(self):
        self.assertEqual(cli("marks", self.dir / "gone.pdf", "1-2").returncode, 2)

    def test_rotated_page(self):
        def build(p):
            underline_at(p, "energy")
            strike_at(p, "proteins")
            p.add_highlight_annot(word(p, "nucleus"))
            p.set_rotation(90)
        found = {m["text"]: m["role"] for m in self.marks(self.pdf(build))["marks"]}
        self.assertEqual(found, {"energy": "emphasis", "proteins": "excluded", "nucleus": "emphasis"})

    def turned_page(self, rotation):
        """Text that reads upright only on the rotated page, as in a landscape slide stored sideways.

        Everything is drawn in unrotated coordinates, so a mark helper that skips the rotation sees a line
        running along the letters, or on a page turned by 180, an underline above them.
        """
        w, h, size = 600, 800, 14
        spot = {90: ((100, 700), 90), 180: ((500, 700), 180), 270: ((500, 100), 270)}
        origin, angle = spot[rotation]
        below = {90: (1, 0), 180: (0, -1), 270: (-1, 0)}[rotation]  # "down" on the rotated page

        def line(page, text, offset):
            r = word(page, text)
            if below[0]:  # the words run up or down the unrotated page
                x = origin[0] + below[0] * offset
                page.draw_line((x, r.y0), (x, r.y1), color=(0, 0, 0), width=0.8)
            else:
                y = origin[1] + below[1] * offset
                page.draw_line((r.x0, y), (r.x1, y), color=(0, 0, 0), width=0.8)

        @blank
        def build(p):
            p.insert_text(origin, "alpha beta gamma", fontsize=size, rotate=angle)
            line(p, "alpha", 1.5)  # under the letters
            line(p, "beta", -0.26 * size)  # through them
            p.add_highlight_annot(word(p, "gamma"))
            p.set_rotation(rotation)
        return self.pdf(build, size=(w, h))

    def test_rotated_page_keeps_underline_and_strikethrough_apart(self):
        for rotation in (90, 180, 270):
            with self.subTest(rotation=rotation):
                found = {m["text"]: (m["kind"], m["role"]) for m in self.marks(self.turned_page(rotation))["marks"]}
                self.assertEqual(found, {"alpha": ("underline", "emphasis"), "beta": ("strikethrough", "excluded"),
                                         "gamma": ("highlight", "emphasis")})

    def test_rotated_page_lists_marks_in_reading_order_inside_the_page(self):
        for rotation in (90, 180, 270):
            with self.subTest(rotation=rotation):
                path = self.turned_page(rotation)
                found = self.marks(path)["marks"]
                self.assertEqual([m["text"] for m in found], ["alpha", "beta", "gamma"])
                doc = fitz.open(str(path))
                area = doc[0].rect
                doc.close()
                for m in found:
                    self.assertTrue(0 <= m["bbox"][0] < m["bbox"][2] <= area.width, m)
                    self.assertTrue(0 <= m["bbox"][1] < m["bbox"][3] <= area.height, m)

    def test_page_wide_rule_is_measured_on_the_rotated_page(self):
        @blank
        def build(p):
            p.insert_text((100, 700), "alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu nu xi "
                                      "omicron pi rho sigma tau upsilon phi chi psi omega", fontsize=14, rotate=90)
            p.draw_line((101.5, 770), (101.5, 30), color=(0, 0, 0), width=0.8)  # 740 of the 800 across
            p.set_rotation(90)
        self.none(build, size=(600, 800))

    def test_full_underlined_line_is_measured_on_the_rotated_page(self):
        """Longer than the unrotated page is wide, yet well short of the rotated page's width."""
        @blank
        def build(p):
            p.insert_text((100, 700), "alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu nu xi "
                                      "omicron pi rho sigma tau", fontsize=14, rotate=90)
            first, last = word(p, "alpha"), word(p, "tau")
            self.assertGreater(first.y1 - last.y0, 0.85 * 600)
            self.assertLess(first.y1 - last.y0, 0.85 * 800)
            p.draw_line((101.5, first.y1), (101.5, last.y0), color=(0, 0, 0), width=0.8)
            p.set_rotation(90)
        self.assertEqual(self.only(build, size=(600, 800))["kind"], "underline")

    def test_rotation_leaves_the_file_alone(self):
        path = self.turned_page(90)
        before = path.read_bytes()
        self.marks(path)
        self.assertEqual(path.read_bytes(), before)

    def test_cropped_page(self):
        def build(p):
            underline_at(p, "energy")
            strike_at(p, "proteins")
            p.add_highlight_annot(word(p, "nucleus"))
            p.set_cropbox(fitz.Rect(40, 60, 560, 800))
        found = {m["text"]: m["role"] for m in self.marks(self.pdf(build))["marks"]}
        self.assertEqual(found, {"energy": "emphasis", "proteins": "excluded", "nucleus": "emphasis"})


class NoPymupdf(unittest.TestCase):
    def test_exit_4_with_the_install_advice(self):
        args = argparse.Namespace(pdf="x.pdf", range="1-2", out=None)
        err = io.StringIO()
        with mock.patch.object(forge, "load_fitz", return_value=None), contextlib.redirect_stderr(err):
            with self.assertRaises(SystemExit) as stop:
                forge.cmd_marks(args)
        self.assertEqual(stop.exception.code, 4)
        self.assertIn("doctor --install pdf_tool", err.getvalue())

    def test_module_does_not_import_pymupdf_at_load(self):
        head = FORGE.read_text(encoding="utf-8").split("def config_dir", 1)[0]
        self.assertNotIn("import fitz", head)
        self.assertNotIn("import pymupdf", head)


def question(i=1, page=2, quote="Mitochondria produce energy.", **source):
    return {
        "id": f"p1-q{i:02d}", "section": "Cells", "type": "open", "difficulty": "easy",
        "question": f"What does organelle number {i} do?", "answer": "It works.", "explanation": "Because.",
        "source": {"pdf": "notes.pdf", "section": "Cells", "page": page, **source}, "quote": quote,
    }


def mark(n=1, page=2, kind="highlight", text="Mitochondria produce energy", origin="annotation"):
    role = "excluded" if kind == "strikethrough" else "emphasis"
    return {"id": f"mark-p{page}-{n}", "page": page, "kind": kind, "role": role, "origin": origin,
            "text": text, "bbox": [72, 90, 300, 110]}


class Files(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)

    def chunk(self, questions, **extra):
        path = self.dir / "chunk-1.json"
        path.write_text(json.dumps({"source": "notes.pdf", "chunk": 1, "format": "open", "page_start": 1,
                                    "page_end": 20, "questions": questions, **extra}), encoding="utf-8")
        return path

    def marks(self, marks):
        path = self.dir / "marks-1.json"
        path.write_text(json.dumps({"marks": marks, "counts": {"total": len(marks)}}), encoding="utf-8")
        return path

    def settings(self, **values):
        path = self.dir / "settings.json"
        path.write_text(json.dumps(values), encoding="utf-8")
        return {"EXAM_FORGE_SETTINGS": str(path)}

    def coverage(self, questions, marks, env=None):
        r = cli("marks-coverage", self.chunk(questions), self.marks(marks), env=env)
        return r, json.loads(r.stdout[:r.stdout.rindex("}") + 1])


class QuestionFields(Files):
    def check(self, q):
        return cli("check", self.chunk([q]), "--format", "open")

    def test_highlight_and_underline_are_accepted(self):
        for value in ("highlight", "underline"):
            with self.subTest(value=value):
                r = self.check(question(emphasis=value, mark="mark-p2-1"))
                self.assertEqual(r.returncode, 0, r.stdout)

    def test_fields_are_optional(self):
        self.assertEqual(self.check(question()).returncode, 0)

    def test_other_emphasis_values_fail(self):
        for value in ("strikethrough", "bold", "", 1):
            with self.subTest(value=value):
                r = self.check(question(emphasis=value))
                self.assertEqual(r.returncode, 1)
                self.assertIn("emphasis", r.stdout)

    def test_mark_id_shape_is_checked(self):
        for value in ("mark-1", "mark-p2", "p2-1", "mark-p2-x", 7, "mark-p2-1 "):
            with self.subTest(value=value):
                r = self.check(question(mark=value))
                self.assertEqual(r.returncode, 1)
                self.assertIn("mark", r.stdout)

    def test_question_errors_reports_them_directly(self):
        errors = forge.question_errors(question(emphasis="struck", mark="x"))
        self.assertTrue(any("emphasis" in e for e in errors))
        self.assertTrue(any("mark" in e for e in errors))

    def test_replace_keeps_the_fields(self):
        chunk = self.chunk([question()])
        fix = self.dir / "fix.json"
        fix.write_text(json.dumps({"questions": [question(emphasis="underline", mark="mark-p2-3")]}), encoding="utf-8")
        self.assertEqual(cli("replace", chunk, fix).returncode, 0)
        source = json.loads(chunk.read_text(encoding="utf-8"))["questions"][0]["source"]
        self.assertEqual((source["emphasis"], source["mark"]), ("underline", "mark-p2-3"))

    def test_merge_keeps_the_fields(self):
        folder = self.dir / "notes"
        folder.mkdir()
        q = question(emphasis="highlight", mark="mark-p2-1")
        q["verified"] = True
        (folder / "chunk-1.json").write_text(json.dumps({"source": "notes.pdf", "questions": [q]}), encoding="utf-8")
        out = self.dir / "book.json"
        r = cli("merge", folder, "--out", out, "--title", "T", "--format", "open")
        self.assertEqual(r.returncode, 0, r.stderr)
        source = json.loads(out.read_text(encoding="utf-8"))["parts"][0]["questions"][0]["source"]
        self.assertEqual((source["emphasis"], source["mark"]), ("highlight", "mark-p2-1"))

    def test_source_pages_reads_questions_with_the_fields(self):
        r = cli("source-pages", self.chunk([question(page=5, emphasis="highlight", mark="mark-p5-1")]), "--ids", "p1-q01")
        self.assertEqual(r.stdout.strip(), "4-6")

    def test_merge_and_marks_files_do_not_collide(self):
        folder = self.dir / "notes"
        folder.mkdir()
        q = question()
        q["verified"] = True
        (folder / "chunk-1.json").write_text(json.dumps({"source": "notes.pdf", "questions": [q]}), encoding="utf-8")
        (folder / "marks-1.json").write_text(json.dumps({"marks": []}), encoding="utf-8")
        r = cli("merge", folder, "--out", self.dir / "book.json", "--title", "T", "--format", "open")
        self.assertEqual(r.returncode, 0, r.stderr)


class Coverage(Files):
    def test_counts_marks_with_a_question(self):
        marks = [mark(1), mark(2, text="Ribosomes assemble proteins")]
        r, out = self.coverage([question(emphasis="highlight", mark="mark-p2-1"), question(2, quote="Other text.")], marks)
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertEqual((out["emphasis_marks"], out["covered"]), (2, 1))
        self.assertEqual([m["id"] for m in out["missing"]], ["mark-p2-2"])
        self.assertEqual(out["questions"], 2)
        self.assertEqual(out["emphasized_questions"], 1)
        self.assertEqual(out["emphasized_share"], 0.5)

    def test_quote_overlap_covers_a_mark_without_an_id(self):
        _, out = self.coverage([question(quote="We know that mitochondria  produce\nenergy in cells.")], [mark()])
        self.assertEqual(out["covered"], 1)
        self.assertEqual(out["emphasized_questions"], 1)

    def test_quote_on_a_far_page_does_not_cover(self):
        _, out = self.coverage([question(page=9)], [mark()])
        self.assertEqual(out["covered"], 0)

    def test_struck_quote_fails_with_a_clear_message(self):
        r, out = self.coverage([question()], [mark(kind="strikethrough")])
        self.assertEqual(r.returncode, 1)
        self.assertIn("p1-q01", r.stdout)
        self.assertIn("mark-p2-1", r.stdout)
        self.assertIn("struck", r.stdout.lower())
        self.assertEqual(out["excluded_hits"][0]["question"], "p1-q01")

    def test_partly_struck_quote_fails(self):
        r, _ = self.coverage([question(quote="Cells need oxygen. Mitochondria produce energy for movement.")],
                             [mark(kind="strikethrough")])
        self.assertEqual(r.returncode, 1)

    def test_quote_inside_a_longer_struck_part_fails(self):
        r, _ = self.coverage([question(quote="produce energy")],
                             [mark(kind="strikethrough", text="All cells have mitochondria that produce energy daily")])
        self.assertEqual(r.returncode, 1)

    def test_claiming_a_struck_mark_fails_even_with_another_quote(self):
        r, _ = self.coverage([question(quote="Unrelated sentence here.", mark="mark-p2-1")], [mark(kind="strikethrough")])
        self.assertEqual(r.returncode, 1)

    def test_underlined_quote_never_fails(self):
        r, out = self.coverage([question()], [mark(kind="underline")])
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertEqual(out["excluded_hits"], [])

    def test_highlighted_quote_never_fails(self):
        r, _ = self.coverage([question()], [mark(kind="highlight")])
        self.assertEqual(r.returncode, 0, r.stdout)

    def test_struck_text_elsewhere_on_the_page_does_no_harm(self):
        r, _ = self.coverage([question()], [mark(kind="strikethrough", text="Ribosomes assemble proteins")])
        self.assertEqual(r.returncode, 0, r.stdout)

    def test_struck_text_on_a_far_page_does_no_harm(self):
        r, _ = self.coverage([question(page=9)], [mark(kind="strikethrough")])
        self.assertEqual(r.returncode, 0, r.stdout)

    def test_one_shared_word_is_not_an_overlap(self):
        r, _ = self.coverage([question(quote="The energy of the sun warms oceans.")],
                             [mark(kind="strikethrough", text="energy stored in fat tissue over winter")])
        self.assertEqual(r.returncode, 0, r.stdout)

    def test_unknown_mark_id_fails(self):
        r, _ = self.coverage([question(mark="mark-p2-9")], [mark()])
        self.assertEqual(r.returncode, 1)
        self.assertIn("mark-p2-9", r.stdout)

    def test_turkish_letters_and_line_breaks_compare_equal(self):
        _, out = self.coverage([question(quote="İNSÜLİN salgılanır ve kan şekeri düşer.")],
                               [mark(text="insülin salgıla-\nnır")])
        self.assertEqual(out["covered"], 1)

    def test_empty_mark_text_needs_an_id(self):
        _, out = self.coverage([question()], [mark(text="")])
        self.assertEqual(out["covered"], 0)
        _, out = self.coverage([question(mark="mark-p2-1")], [mark(text="")])
        self.assertEqual(out["covered"], 1)

    def test_marks_outside_the_chunk_are_left_out(self):
        _, out = self.coverage([question()], [mark(), mark(1, page=30, text="Far away part of the notes")])
        self.assertEqual(out["emphasis_marks"], 1)

    def test_share_limit_comes_from_the_setting(self):
        questions = [question(1, emphasis="highlight", mark="mark-p2-1"), question(2, quote="Other.")]
        missing = [mark(1), mark(2, text="Ribosomes assemble proteins")]
        _, out = self.coverage(questions, missing, env=self.settings(marked_text_share=0.25))
        self.assertEqual(out["share_limit"], 0.25)
        self.assertFalse(out["needs_more"])

    def test_missing_marks_need_more_while_the_share_allows(self):
        questions = [question(1, emphasis="highlight", mark="mark-p2-1"), question(2, quote="Other."),
                     question(3, quote="More."), question(4, quote="Still more.")]
        _, out = self.coverage(questions, [mark(1), mark(2, text="Ribosomes assemble proteins")])
        self.assertEqual(out["share_limit"], 0.6)
        self.assertTrue(out["needs_more"])

    def test_full_coverage_needs_nothing_more(self):
        _, out = self.coverage([question(emphasis="highlight", mark="mark-p2-1")], [mark()])
        self.assertFalse(out["needs_more"])

    def test_priority_off_gives_marks_no_weight(self):
        _, out = self.coverage([question(quote="Other.")], [mark()], env=self.settings(marked_text_priority=False))
        self.assertFalse(out["priority"])
        self.assertFalse(out["needs_more"])

    def test_priority_off_still_excludes_struck_text(self):
        r, _ = self.coverage([question()], [mark(kind="strikethrough")], env=self.settings(marked_text_priority=False))
        self.assertEqual(r.returncode, 1)

    def test_no_marks_at_all(self):
        r, out = self.coverage([question()], [])
        self.assertEqual(r.returncode, 0)
        self.assertEqual((out["emphasis_marks"], out["covered"], out["emphasized_share"]), (0, 0, 0))

    def test_bad_marks_file_exits_2(self):
        bad = self.dir / "bad.json"
        bad.write_text('{"marks": 3}', encoding="utf-8")
        self.assertEqual(cli("marks-coverage", self.chunk([question()]), bad).returncode, 2)

    def test_check_with_marks_fails_on_struck_text(self):
        r = cli("check", self.chunk([question()]), "--format", "open", "--marks", self.marks([mark(kind="strikethrough")]))
        self.assertEqual(r.returncode, 1)
        self.assertIn("CHECK FAILED", r.stdout)
        self.assertIn("mark-p2-1", r.stdout)

    def test_check_with_marks_passes_on_underlined_text(self):
        r = cli("check", self.chunk([question()]), "--format", "open", "--marks", self.marks([mark(kind="underline")]))
        self.assertEqual(r.returncode, 0, r.stdout)

    def test_check_without_marks_does_not_look(self):
        self.assertEqual(cli("check", self.chunk([question()]), "--format", "open").returncode, 0)

    def test_fail_out_sends_a_verified_question_back(self):
        q = question()
        q["verified"] = True
        chunk, marks = self.chunk([q]), self.marks([mark(kind="strikethrough")])
        fail = self.dir / "review-marks.json"
        self.assertEqual(cli("marks-coverage", chunk, marks, "--fail-out", fail).returncode, 1)
        result = json.loads(fail.read_text(encoding="utf-8"))["results"][0]
        self.assertEqual((result["id"], result["status"], result["reason"]), ("p1-q01", "FAIL", "scope"))
        cli("approve", chunk, fail)
        self.assertFalse(json.loads(chunk.read_text(encoding="utf-8"))["questions"][0]["verified"])


class Settings(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.env = {"HOME": tmp.name, "USERPROFILE": tmp.name}

    def spec(self, key):
        schema = json.loads((BIN.parent / "settings.schema.json").read_text(encoding="utf-8"))
        return next(s for s in schema["settings"] if s["key"] == key)

    def test_defaults(self):
        s = json.loads(cli("settings", env=self.env).stdout)
        self.assertEqual(s["marked_text_share"], 0.6)
        self.assertIs(s["marked_text_priority"], True)

    def test_schema_entries(self):
        share, priority = self.spec("marked_text_share"), self.spec("marked_text_priority")
        self.assertEqual((share["type"], share["min"], share["max"]), ("float", 0, 1))
        self.assertEqual(priority["type"], "bool")
        for spec in (share, priority):
            self.assertEqual(spec["group"], "questions")
            for field in ("label", "help"):
                self.assertTrue(spec[field]["en"] and spec[field]["tr"])

    def test_share_is_kept_in_bounds(self):
        self.assertEqual(cli("config", "set", "marked_text_share", "0.5", env=self.env).returncode, 0)
        for bad in ("1.5", "-0.1", "much"):
            with self.subTest(bad=bad):
                self.assertNotEqual(cli("config", "set", "marked_text_share", bad, env=self.env).returncode, 0)

    def test_priority_is_a_boolean(self):
        self.assertEqual(cli("config", "set", "marked_text_priority", "false", env=self.env).returncode, 0)
        self.assertNotEqual(cli("config", "set", "marked_text_priority", "maybe", env=self.env).returncode, 0)


class Docs(unittest.TestCase):
    def read(self, name):
        return (ROOT / name).read_text(encoding="utf-8")

    def test_editor_states_the_rule_plainly(self):
        text = self.read("agents/editor.md")
        self.assertRegex(text, r"(?i)underlin\w* and highlight\w* mean important")
        self.assertRegex(text, r"(?i)never cancelled")
        self.assertRegex(text, r"(?i)only a line through")
        self.assertIn('"remove"', text)
        self.assertNotIn("handwritten or marked note", text)

    def test_editor_looks_at_scanned_pages_and_follows_the_priority(self):
        text = self.read("agents/editor.md")
        self.assertRegex(text, r"(?i)scanned")
        self.assertIn("marked_text_share", text)
        self.assertIn("marked_text_priority", text)
        self.assertIn('"emphasis"', text)
        self.assertIn('"mark"', text)

    def test_reviewer_fails_both_mistakes(self):
        text = self.read("agents/reviewer.md")
        self.assertRegex(text, r"(?i)struck")
        self.assertRegex(text, r"(?i)FAIL.*`scope`")
        self.assertRegex(text, r"(?i)emphasi[sz]ed part.*(excluded|out of scope)")
        self.assertRegex(text, r"(?i)share")

    def test_screener_and_chief_editor_know_the_rule(self):
        for name in ("agents/screener.md", "agents/chief-editor.md"):
            with self.subTest(name=name):
                text = self.read(name)
                self.assertRegex(text, r"(?i)underlin")
                self.assertRegex(text, r"(?i)struck")

    def test_orchestrator_runs_both_commands_and_passes_the_settings(self):
        text = self.read("skills/exam-forge/SKILL.md")
        self.assertRegex(text, r'forge\.py|"\$FORGE" marks ')
        self.assertIn('"$FORGE" marks "', text)
        self.assertIn('"$FORGE" marks-coverage ', text)
        self.assertIn("s.marked_text_share", text)
        self.assertIn("s.marked_text_priority", text)
        self.assertRegex(text.split("## 7. Close")[1], r"(?i)marked.*coverage|coverage.*marked")

    def test_helper_lists_the_commands(self):
        head = forge.__doc__
        self.assertIn("marks <pdf>", head)
        self.assertIn("marks-coverage", head)

    def test_readme_and_changelog(self):
        readme = self.read("README.md")
        self.assertRegex(readme.split("## Install")[0], r"(?i)highlight")
        self.assertRegex(readme.split("## Türkçe özet")[1], r"(?i)vurgu|altı çizili")
        unreleased = self.read("CHANGELOG.md").split("## [Unreleased]")[1].split("\n## [")[0]
        self.assertRegex(unreleased, r"`?forge\.py marks`?|`marks`")
        self.assertIn("marks-coverage", unreleased)


if __name__ == "__main__":
    unittest.main()
