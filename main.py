#!/usr/bin/env python3
"""coolest-art-01 entry: HokiiDoge, a Shiba astronaut adrift in a purple-orange nebula.

A single deterministic program (Python 3.12/3.13, standard library only). It draws
the whole picture procedurally -- vector shapes rasterised into an RGB framebuffer --
and writes it out with a hand-built PNG encoder (zlib + struct, no Pillow).

Run: mkdir -p out && MAKEFUN_OUTPUT_DIR=out python3 main.py
Output: <out>/entry.png, 800x800 RGB PNG, byte-identical on every run.
"""

import binascii
import math
import os
import struct
import zlib

W, H = 800, 800


# ---------------------------------------------------------------------------
# Deterministic pseudo-random numbers (fixed-seed LCG; no time, no os.urandom).
# ---------------------------------------------------------------------------
def lcg(seed):
    state = seed & 0xFFFFFFFF

    def nxt():
        nonlocal state
        state = (1664525 * state + 1013904223) & 0xFFFFFFFF
        return state / 4294967296.0

    return nxt


# ---------------------------------------------------------------------------
# Framebuffer: list of bytearray rows, 3 bytes (RGB) per pixel.
# ---------------------------------------------------------------------------
def new_canvas(w, h, top_rgb, bottom_rgb):
    rows = []
    for y in range(h):
        t = y / (h - 1)
        r = int(top_rgb[0] + (bottom_rgb[0] - top_rgb[0]) * t)
        g = int(top_rgb[1] + (bottom_rgb[1] - top_rgb[1]) * t)
        b = int(top_rgb[2] + (bottom_rgb[2] - top_rgb[2]) * t)
        rows.append(bytearray([r, g, b]) * w)
    return rows


def blend(px, x, y, rgb, a):
    """Blend rgb over px[y][x] with alpha a (0..255)."""
    if a <= 0:
        return
    if x < 0 or y < 0 or x >= W or y >= H:
        return
    row = px[y]
    i = x * 3
    if a >= 255:
        row[i], row[i + 1], row[i + 2] = rgb
        return
    ia = 255 - a
    row[i] = (rgb[0] * a + row[i] * ia) // 255
    row[i + 1] = (rgb[1] * a + row[i + 1] * ia) // 255
    row[i + 2] = (rgb[2] * a + row[i + 2] * ia) // 255


def disc(px, cx, cy, r, rgb, a=255):
    """Filled circle with a 1px soft edge."""
    ri = r - 0.75
    ro = r + 0.75
    ri2 = ri * ri
    ro2 = ro * ro
    x0 = max(0, int(cx - ro))
    x1 = min(W - 1, int(cx + ro))
    y0 = max(0, int(cy - ro))
    y1 = min(H - 1, int(cy + ro))
    for y in range(y0, y1 + 1):
        dy = y - cy
        for x in range(x0, x1 + 1):
            dx = x - cx
            d2 = dx * dx + dy * dy
            if d2 <= ri2:
                blend(px, x, y, rgb, a)
            elif d2 <= ro2:
                cov = (ro - math.sqrt(d2)) / 1.5
                blend(px, x, y, rgb, int(a * cov))


def ellipse(px, cx, cy, rx, ry, rgb, a=255, rot=0.0):
    """Filled rotated ellipse with a soft edge."""
    cosr = math.cos(rot)
    sinr = math.sin(rot)
    span = int(max(rx, ry)) + 2
    x0 = max(0, int(cx - span))
    x1 = min(W - 1, int(cx + span))
    y0 = max(0, int(cy - span))
    y1 = min(H - 1, int(cy + span))
    for y in range(y0, y1 + 1):
        for x in range(x0, x1 + 1):
            dx = x - cx
            dy = y - cy
            u = dx * cosr + dy * sinr
            v = -dx * sinr + dy * cosr
            q = (u / rx) ** 2 + (v / ry) ** 2
            if q <= 0.94:
                blend(px, x, y, rgb, a)
            elif q <= 1.06:
                blend(px, x, y, rgb, int(a * (1.06 - q) / 0.12))


def _inside_poly(x, y, pts):
    inside = False
    n = len(pts)
    j = n - 1
    for i in range(n):
        xi, yi = pts[i]
        xj, yj = pts[j]
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    return inside


def poly(px, pts, rgb, a=255):
    """Scanline polygon fill, 2x2 supersampled for a soft edge."""
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    x0 = max(0, int(min(xs)) - 1)
    x1 = min(W - 1, int(max(xs)) + 1)
    y0 = max(0, int(min(ys)) - 1)
    y1 = min(H - 1, int(max(ys)) + 1)
    for y in range(y0, y1 + 1):
        for x in range(x0, x1 + 1):
            hits = 0
            for sx, sy in ((0.25, 0.25), (0.75, 0.25), (0.25, 0.75), (0.75, 0.75)):
                if _inside_poly(x + sx, y + sy, pts):
                    hits += 1
            if hits:
                blend(px, x, y, rgb, (a * hits) // 4)


def blob(px, cx, cy, r, rgb, peak):
    """Soft radial glow: alpha falls off quadratically to 0 at radius r."""
    x0 = max(0, int(cx - r))
    x1 = min(W - 1, int(cx + r))
    y0 = max(0, int(cy - r))
    y1 = min(H - 1, int(cy + r))
    r2 = r * r
    for y in range(y0, y1 + 1):
        dy = y - cy
        for x in range(x0, x1 + 1):
            dx = x - cx
            q = 1.0 - (dx * dx + dy * dy) / r2
            if q > 0:
                blend(px, x, y, rgb, int(peak * q * q))


def ring_discs(px, cx, cy, rx, ry, thick, rot, rgb, a=255, front=None):
    """Tilted elliptical ring, drawn as overlapping discs along the path.

    front=None draws the whole ring; front=True only the lower half (screen
    y >= cy, the part in front of a planet); front=False only the upper half.
    """
    cosr = math.cos(rot)
    sinr = math.sin(rot)
    step = thick / (2.0 * max(rx, ry))
    n = int(2.0 * math.pi / step) + 1
    rad = thick / 2.0 + 1.0
    for k in range(n):
        th = k * step
        lx = rx * math.cos(th)
        ly = ry * math.sin(th)
        x = cx + lx * cosr - ly * sinr
        y = cy + lx * sinr + ly * cosr
        if front is True and y < cy:
            continue
        if front is False and y >= cy:
            continue
        disc(px, x, y, rad, rgb, a)


def sparkle(px, cx, cy, r, rgb, a=255):
    """Four-point star: two tapered spikes, long in x and in y."""
    x0 = max(0, int(cx - r - 1))
    x1 = min(W - 1, int(cx + r + 1))
    y0 = max(0, int(cy - r - 1))
    y1 = min(H - 1, int(cy + r + 1))
    for y in range(y0, y1 + 1):
        for x in range(x0, x1 + 1):
            dx = abs(x - cx) / r
            dy = abs(y - cy) / r
            s = max(0.0, 1.0 - (dx + 4.0 * dy)) + max(0.0, 1.0 - (dy + 4.0 * dx))
            if s > 0:
                blend(px, x, y, rgb, min(255, int(a * min(1.0, s))))


# ---------------------------------------------------------------------------
# Hand-built PNG encoder: 8-bit RGB, filter 0, zlib level 9.
# ---------------------------------------------------------------------------
def _png_chunk(typ, data):
    out = struct.pack(">I", len(data)) + typ + data
    out += struct.pack(">I", binascii.crc32(typ + data) & 0xFFFFFFFF)
    return out


def write_png(path, w, h, rows):
    raw = bytearray()
    for row in rows:
        raw.append(0)
        raw += row
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    png = b"\x89PNG\r\n\x1a\n"
    png += _png_chunk(b"IHDR", ihdr)
    png += _png_chunk(b"IDAT", zlib.compress(bytes(raw), 9))
    png += _png_chunk(b"IEND", b"")
    with open(path, "wb") as f:
        f.write(png)
    return len(png)


# ---------------------------------------------------------------------------
# Scene: HokiiDoge, a Shiba astronaut adrift in a purple-orange nebula.
# Back to front: sky gradient, nebula glows, stars, ringed planet, Shiba
# (tether, suit, head, helmet glass + rim + antenna), reflections, sparkles,
# and a corner vignette.
# ---------------------------------------------------------------------------

def draw():
    px = new_canvas(W, H, (10, 6, 24), (26, 11, 46))

    # Nebula glows.
    blob(px, 150, 180, 260, (168, 60, 180), 90)
    blob(px, 640, 620, 300, (255, 120, 40), 80)
    blob(px, 120, 620, 220, (40, 180, 180), 55)
    blob(px, 700, 120, 200, (120, 80, 220), 60)
    blob(px, 400, 430, 300, (255, 170, 90), 35)

    # Stars (deterministic LCG).
    rnd = lcg(20261113)
    for _ in range(230):
        x = rnd() * W
        y = rnd() * H
        r = 0.8 + rnd() * 1.8
        b = int(140 + rnd() * 115)
        pick = rnd()
        if pick < 0.6:
            col = (255, 255, 255)
        elif pick < 0.8:
            col = (255, 230, 180)
        else:
            col = (190, 210, 255)
        disc(px, x, y, r, col, b)
    for _ in range(12):
        x = 40 + rnd() * (W - 80)
        y = 40 + rnd() * (H - 80)
        sparkle(px, x, y, 6 + rnd() * 5, (255, 255, 255), int(150 + rnd() * 90))

    # Ringed planet, upper right. Back half of the ring first.
    pcx, pcy, pr = 628, 150, 118
    ring_discs(px, pcx, pcy, 185, 52, 16, -0.32, (240, 210, 130), 230, front=False)
    # Planet body: clipped horizontal bands as chord ellipses.
    disc(px, pcx, pcy, pr, (217, 106, 43))
    for yb, hb, col in (
        (48, 14, (250, 220, 170)),
        (76, 14, (230, 120, 50)),
        (104, 14, (220, 90, 90)),
        (132, 14, (250, 220, 170)),
        (162, 15, (200, 90, 40)),
        (192, 15, (240, 170, 80)),
        (222, 15, (170, 70, 60)),
        (248, 12, (190, 80, 70)),
    ):
        dy = yb - pcy
        half = math.sqrt(max(0.0, pr * pr - dy * dy))
        ellipse(px, pcx, yb, half, hb, col)
    blob(px, 590, 110, 45, (255, 240, 210), 55)
    ring_discs(px, pcx, pcy, 185, 52, 16, -0.32, (240, 210, 130), 230, front=True)

    # Tether: a gentle quadratic curve from the suit off to the lower right.
    for k in range(51):
        t = k / 50.0
        x = (1 - t) * (1 - t) * 455 + 2 * (1 - t) * t * 600 + t * t * 720
        y = (1 - t) * (1 - t) * 600 + 2 * (1 - t) * t * 645 + t * t * 720
        disc(px, x, y, 3, (200, 200, 210), 200)

    # Suit torso, chest panel, mission patch, soft shading.
    ellipse(px, 400, 645, 130, 85, (235, 240, 245))
    ellipse(px, 348, 672, 90, 38, (180, 190, 200), 60)
    ellipse(px, 400, 632, 80, 50, (240, 130, 50))
    disc(px, 400, 618, 22, (40, 70, 160))
    sparkle(px, 400, 618, 8, (255, 255, 255), 220)

    # Shiba head and ears.
    disc(px, 400, 440, 150, (222, 133, 52))
    poly(px, [(298, 338), (312, 252), (372, 330)], (222, 133, 52))
    poly(px, [(312, 322), (320, 282), (352, 322)], (190, 95, 55))
    poly(px, [(428, 330), (488, 252), (502, 338)], (222, 133, 52))
    poly(px, [(448, 322), (480, 282), (488, 322)], (190, 95, 55))
    # Muzzle, nose, eyes, smile, cheeks.
    ellipse(px, 400, 502, 85, 55, (250, 230, 200))
    ellipse(px, 400, 476, 22, 15, (40, 25, 20))
    disc(px, 345, 432, 16, (30, 20, 18))
    disc(px, 455, 432, 16, (30, 20, 18))
    disc(px, 350, 427, 5, (255, 255, 255))
    disc(px, 460, 427, 5, (255, 255, 255))
    for k in range(9):
        ang = math.radians(25 + k * 16.25)
        disc(px, 400 + 32 * math.cos(ang), 498 + 32 * math.sin(ang), 3, (60, 35, 25))
    blob(px, 318, 492, 22, (255, 140, 140), 70)
    blob(px, 482, 492, 22, (255, 140, 140), 70)

    # Helmet glass tint over the head, then antenna, rim, reflections.
    disc(px, 400, 435, 182, (200, 225, 245), 55)
    for k in range(11):
        t = k / 10.0
        disc(px, 400, 253 - t * 41, 4, (220, 225, 235))
    blob(px, 400, 206, 26, (255, 80, 80), 90)
    disc(px, 400, 206, 9, (255, 70, 70))
    ring_discs(px, 400, 435, 182, 182, 12, 0.0, (255, 255, 255), 255)
    ellipse(px, 332, 322, 46, 18, (255, 255, 255), 150, -0.5)
    ellipse(px, 472, 300, 26, 11, (255, 255, 255), 120, -0.5)
    disc(px, 300, 380, 4, (255, 255, 255), 180)
    disc(px, 500, 360, 3, (255, 255, 255), 160)

    # Foreground sparkles.
    sparkle(px, 180, 300, 14, (255, 215, 130))
    sparkle(px, 620, 420, 11, (255, 255, 255))
    sparkle(px, 250, 640, 9, (255, 255, 255))
    sparkle(px, 560, 700, 12, (255, 215, 130))
    sparkle(px, 90, 480, 8, (255, 255, 255))

    # Corner vignette to pull focus to the centre.
    blob(px, 0, 0, 380, (0, 0, 0), 55)
    blob(px, 800, 0, 380, (0, 0, 0), 55)
    blob(px, 0, 800, 380, (0, 0, 0), 55)
    blob(px, 800, 800, 380, (0, 0, 0), 55)
    return px


def main():
    outdir = os.environ.get("MAKEFUN_OUTPUT_DIR", "out")
    os.makedirs(outdir, exist_ok=True)
    px = draw()
    path = os.path.join(outdir, "entry.png")
    n = write_png(path, W, H, px)
    print("wrote %s %dx%d %d bytes" % (path, W, H, n))


if __name__ == "__main__":
    main()
