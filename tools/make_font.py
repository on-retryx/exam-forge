#!/usr/bin/env python3
"""Builds the Studio's own pixel font (5x7 with descenders) with no outside assets.

Writes skills/exam-forge/studio/pixel.ttf for the page and font.js for the canvas.
Each glyph is up to 9 rows in base 32: 5 bits per row, left pixel is 16.
Rows 0-6 sit on the baseline, rows 7-8 are descenders.
"""
import json
import struct
import time
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "skills" / "exam-forge" / "studio"
G = {
    "A": "ehhvhhh", "B": "uhhuhhu", "C": "ehggghe", "D": "uhhhhhu", "E": "vgguggv", "F": "vgguggg", "G": "ehgnhhf",
    "H": "hhhvhhh", "I": "e44444e", "J": "72222ic", "K": "hikokih", "L": "ggggggv", "M": "hrllhhh", "N": "hhpljhh",
    "O": "ehhhhhe", "P": "uhhuggg", "Q": "ehhhlid", "R": "uhhukih", "S": "fgge11u", "T": "v444444", "U": "hhhhhhe",
    "V": "hhhhha4", "W": "hhhllrh", "X": "hha4ahh", "Y": "hhha444", "Z": "v1248gv",
    "a": "00e1fhf", "b": "ggmphhu", "c": "00egghe", "d": "11djhhf", "e": "00ehvge", "f": "698s888", "g": "00fhhhf1e",
    "h": "ggmphhh", "i": "40c444e", "j": "2062222ic", "k": "ggikoki", "l": "c44444e", "m": "00qllhh", "n": "00mphhh",
    "o": "00ehhhe", "p": "00uhhhugg", "q": "00fhhhf11", "r": "00mpggg", "s": "00fge1u", "t": "88s8896", "u": "00hhhjd",
    "v": "00hhha4", "w": "00hhlla", "x": "00ha4ah", "y": "00hhhhf1e", "z": "00v248v",
    "0": "ehjlphe", "1": "4c4444e", "2": "eh1248v", "3": "v2421he", "4": "26aiv22", "5": "vgu11he", "6": "68guhhe",
    "7": "v124888", "8": "ehhehhe", "9": "ehhf12c",
    " ": "0000000", ".": "00000cc", ",": "00000c48", ":": "0cc0cc0", ";": "0cc0c48", "!": "4444404", "?": "eh12404",
    "-": "000v000", "–": "000v000", "—": "000v000", "_": "000000v", "+": "044v440", "=": "00v0v00", "/": "11248gg",
    "\\": "gg84211", "(": "2488842", ")": "8422248", "[": "e88888e", "]": "e22222e", "'": "4480000", '"': "aaa0000",
    "%": "pp248jj", "#": "aavavaa", "$": "4fke5u4", "&": "cik8lid", "*": "0a4v4a0", "<": "248g842",
    ">": "8421248", "·": "0004000", "×": "0ha4ah0", "…": "000000l", "●": "0evvve0", "~": "00dm000", "|": "4444444",
    "Ç": "ehggghe48", "ç": "00egghe48", "Ş": "fgge11u48", "ş": "00fge1u48", "Ğ": "hegnhhf", "ğ": "e0fhhhf1e",
    "Ö": "aehhhhe", "ö": "a0ehhhe", "Ü": "ahhhhhe", "ü": "a0hhhjd", "İ": "40v444v", "ı": "00c444e",
}
UNIT, EM = 100, 800
ASCENT, DESCENT = 800, -200
ADVANCE = 6 * UNIT


def rows(code):
    return [int(c, 32) for c in code]


def rects(code):
    out = []
    for r, bits in enumerate(rows(code)):
        c = 0
        while c < 5:
            if bits & (16 >> c):
                start = c
                while c < 5 and bits & (16 >> c):
                    c += 1
                top = (7 - r) * UNIT
                out.append((start * UNIT, top - UNIT, c * UNIT, top))
            else:
                c += 1
    return out


def glyph_data(rs):
    if not rs:
        return b""
    pts, ends = [], []
    for x0, y0, x1, y1 in rs:  # clockwise
        pts += [(x0, y0), (x0, y1), (x1, y1), (x1, y0)]
        ends.append(len(pts) - 1)
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    d = struct.pack(">hhhhh", len(rs), min(xs), min(ys), max(xs), max(ys))
    d += b"".join(struct.pack(">H", e) for e in ends) + struct.pack(">H", 0)
    d += bytes([1] * len(pts))
    px = py = 0
    dx, dy = b"", b""
    for x, y in pts:
        dx += struct.pack(">h", x - px)
        dy += struct.pack(">h", y - py)
        px, py = x, y
    d += dx + dy
    return d + b"\0" * (-len(d) % 4)


def checksum(b):
    b += b"\0" * (-len(b) % 4)
    return sum(struct.unpack(f">{len(b) // 4}L", b)) & 0xFFFFFFFF


def build():
    chars = sorted(G, key=ord)
    notdef = [(0, 0, 500, 700)]
    glyphs = [glyph_data(notdef)] + [glyph_data(rects(G[c])) for c in chars]
    offsets, glyf = [0], b""
    for g in glyphs:
        glyf += g
        offsets.append(len(glyf))
    loca = b"".join(struct.pack(">L", o) for o in offsets)
    n = len(glyphs)
    now = int(time.mktime((2026, 11, 6, 0, 0, 0, 0, 0, 0))) + 2082844800
    head = struct.pack(">HHLLLHHQQhhhhHHhhh", 1, 0, 0x00010000, 0, 0x5F0F3CF5, 0b1011, EM, now, now,
                       0, -200, 600, 800, 0, 8, 2, 1, 0)
    hhea = struct.pack(">HHhhhHhhhhhhhhhhhH", 1, 0, ASCENT, DESCENT, 0, ADVANCE, 0, 0, 600, 1, 0, 0, 0, 0, 0, 0, 0, n)
    maxp = struct.pack(">LHHHHHHHHHHHHHH", 0x00010000, n, 200, 40, 0, 0, 1, 0, 0, 0, 0, 0, 0, 0, 0)
    hmtx = b"".join(struct.pack(">Hh", ADVANCE, 0) for _ in range(n))
    # cmap format 4: one segment per contiguous run
    codes = [ord(c) for c in chars]
    segs, i = [], 0
    while i < len(codes):
        j = i
        while j + 1 < len(codes) and codes[j + 1] == codes[j] + 1:
            j += 1
        segs.append((codes[i], codes[j], (i + 1 - codes[i]) & 0xFFFF))
        i = j + 1
    segs.append((0xFFFF, 0xFFFF, 1))
    sc = len(segs)
    sr = 2 * (1 << (len(bin(sc)) - 3))
    sub = struct.pack(">HHHHHHH", 4, 16 + 8 * sc, 0, sc * 2, sr, len(bin(sc)) - 3, sc * 2 - sr)
    sub += b"".join(struct.pack(">H", s[1]) for s in segs) + struct.pack(">H", 0)
    sub += b"".join(struct.pack(">H", s[0]) for s in segs)
    sub += b"".join(struct.pack(">H", s[2]) for s in segs)
    sub += struct.pack(f">{sc}H", *([0] * sc))
    cmap = struct.pack(">HHHHL", 0, 1, 3, 1, 12) + sub
    names = {1: "Forge Pixel", 2: "Regular", 3: "Forge Pixel 1.0", 4: "Forge Pixel", 5: "Version 1.0", 6: "ForgePixel-Regular"}
    recs, blob = b"", b""
    for nid, text in names.items():
        t = text.encode("utf-16-be")
        recs += struct.pack(">HHHHHH", 3, 1, 0x409, nid, len(t), len(blob))
        blob += t
    name = struct.pack(">HHH", 0, len(names), 6 + 12 * len(names)) + recs + blob
    os2 = struct.pack(">HhHHHhhhhhhhhhhh", 3, ADVANCE, 400, 5, 0, 500, 500, 0, 0, 500, 500, 0, 300, 50, 300, 0)
    os2 += b"\0" * 10 + struct.pack(">LLLL", 1, 0, 0, 0) + b"FRGE" + struct.pack(">HHH", 0x40, min(codes), max(c for c in codes))
    os2 += struct.pack(">hhhHH", ASCENT, DESCENT, 0, ASCENT, -DESCENT)
    os2 += struct.pack(">LL", 1, 0) + struct.pack(">hhHHH", 500, 700, 0, 32, 0)
    post = struct.pack(">LLhhLLLLL", 0x00030000, 0, -100, 50, 1, 0, 0, 0, 0)
    tables = {"OS/2": os2, "cmap": cmap, "glyf": glyf, "head": head, "hhea": hhea, "hmtx": hmtx,
              "loca": loca, "maxp": maxp, "name": name, "post": post}
    tags = sorted(tables)
    num = len(tags)
    power = 1 << (len(bin(num)) - 3)
    header = struct.pack(">LHHHH", 0x00010000, num, power * 16, len(bin(num)) - 3, num * 16 - power * 16)
    offset = 12 + 16 * num
    directory, body = b"", b""
    for tag in tags:
        data = tables[tag]
        directory += tag.encode() + struct.pack(">LLL", checksum(data), offset + len(body), len(data))
        body += data + b"\0" * (-len(data) % 4)
    font = bytearray(header + directory + body)
    adjust = (0xB1B0AFBA - checksum(bytes(font))) & 0xFFFFFFFF
    pos = font.find(head)
    struct.pack_into(">L", font, pos + 8, adjust)
    (OUT / "pixel.ttf").write_bytes(bytes(font))
    data = {c: rows(G[c]) for c in chars}
    (OUT / "font.js").write_text("/* Generated by tools/make_font.py */\nwindow.PIXEL_FONT = "
                                 + json.dumps(data, ensure_ascii=False, separators=(",", ":")) + ";\n", encoding="utf-8")
    print(len(chars), "glyphs,", len(font), "bytes")


if __name__ == "__main__":
    build()
