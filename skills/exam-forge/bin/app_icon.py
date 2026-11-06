"""The Exam Forge icon: an open book on an anvil, drawn from text like the rest of the Studio.

Standard library only, so no image file lives in the repository.
"""
import struct
import zlib

GLYPH = """
..........oo..........
....y.....ooo.....y...
.........oooo.........
.......o.oyyoo.o......
.......ooyyyyoo.......
.......ooyyyyoo.......
.wwwwwwwwwllwwwwwwwww.
.wwbbbbbbwllwbbbbbbww.
.wwwwwwwwwllwwwwwwwww.
.wwbbbbbwwllwwbbbbbww.
.wwwwwwwwwllwwwwwwwww.
.wwbbbbbbwllwbbbbbbww.
.gggggggggggggggggggg.
......................
..ssssssssssssssssss..
ssssssssssssssssssss..
.ddssssssssssssssssdd.
...ddddssssssssdddd...
.......ssssssss.......
.......dssssssd.......
.....ssssssssssss.....
....dddddddddddddd....
"""
COLORS = {"y": (255, 205, 117), "o": (239, 125, 87), "w": (244, 240, 228), "b": (148, 176, 194), "l": (86, 108, 134),
          "g": (255, 205, 117), "s": (148, 176, 194), "d": (86, 108, 134)}
BACK = (26, 28, 44)
GRID = 32


def cells():
    """A GRID x GRID list of colours, None where the background shows."""
    art = [[None] * GRID for _ in range(GRID)]
    rows = GLYPH.strip().splitlines()
    top, left = (GRID - len(rows)) // 2, (GRID - len(rows[0])) // 2
    for dy, row in enumerate(rows):
        for dx, c in enumerate(row):
            if c != ".":
                art[top + dy][left + dx] = COLORS[c]
    return art


def png(size):
    """The icon as PNG bytes, each pixel of art a sharp square. It is opaque edge to edge:
    macOS rounds such an icon itself, and puts anything see-through on a grey plate."""
    art, scale = cells(), size / GRID
    lines = []
    for y in range(size):
        line = bytearray(b"\0")
        row = art[int(y / scale)]
        for x in range(size):
            line += bytes((*(row[int(x / scale)] or BACK), 255))
        lines.append(bytes(line))

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"".join(lines), 9)) + chunk(b"IEND", b""))
