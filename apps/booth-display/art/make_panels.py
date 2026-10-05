#!/usr/bin/env python3
"""Render the booth display's HyperPixel control panels (run on a Mac; needs Pillow).

Writes panel-playing / panel-paused as .png (preview) and .rgb.gz (raw RGB24 the Pi blits).
Fonts: set FONT_DIR to a folder with Satoshi-Variable.ttf + JetBrainsMono-Medium.ttf
(Neuralift brand fonts, not in this repo); falls back to Pillow's default font.
"""
import gzip
import os
from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
FONT_DIR = os.environ.get("FONT_DIR", os.path.expanduser("~/claudecode/Neuralift/G2E Video/source/fonts"))

W, H = 480, 800
HOME = 80                                   # must match HOME_STRIP in showtouch.py
BG = (7, 4, 15); LINE = (42, 24, 80); TXT = (240, 236, 250); DIM = (150, 130, 190)
VIOLET = (126, 12, 247); LILAC = (196, 155, 255); MAGENTA = (214, 43, 255); STRIP = (20, 12, 38)


def font(size, weight=800):
    try:
        f = ImageFont.truetype(os.path.join(FONT_DIR, "Satoshi-Variable.ttf"), size)
        f.set_variation_by_axes([weight])
        return f
    except OSError:
        return ImageFont.load_default(size)


def mono(size):
    try:
        return ImageFont.truetype(os.path.join(FONT_DIR, "JetBrainsMono-Medium.ttf"), size)
    except OSError:
        return ImageFont.load_default(size)


def centred(d, y, text, f, fill):
    d.text(((W - d.textlength(text, font=f)) / 2, y), text, font=f, fill=fill)


def panel(paused):
    im = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(im)
    band = (H - HOME) / 3
    b1, b2 = round(HOME + band), round(HOME + 2 * band)
    cx = W // 2

    # Home strip
    d.rectangle([0, 0, W, HOME], fill=STRIP)
    d.polygon([(cx - 92, 40), (cx - 76, 26), (cx - 60, 40)], fill=DIM)          # roof
    d.rectangle([cx - 87, 40, cx - 65, 55], fill=DIM)                           # house
    d.text((cx - 48, 28), "HOLD FOR MENU", font=mono(18), fill=DIM)

    if paused:
        d.rectangle([0, b1, W, b2], fill=(40, 10, 80))
    for y in (b1, b2):
        d.line([(24, y), (W - 24, y)], fill=LINE, width=2)

    # Previous
    top = HOME + 50
    d.polygon([(cx - 10, top), (cx - 60, top + 35), (cx - 10, top + 70)], fill=LILAC)
    d.polygon([(cx + 40, top), (cx - 10, top + 35), (cx + 40, top + 70)], fill=LILAC)
    centred(d, top + 92, "PREVIOUS", font(30), TXT)

    # Pause / resume
    cy = b1 + 80
    if paused:
        d.polygon([(cx - 28, cy - 38), (cx - 28, cy + 38), (cx + 40, cy)], fill=MAGENTA)
        centred(d, cy + 58, "RESUME", font(34), TXT)
        centred(d, cy + 100, "PAUSED ON THIS SLIDE", mono(16), LILAC)
    else:
        d.rectangle([cx - 30, cy - 38, cx - 10, cy + 38], fill=VIOLET)
        d.rectangle([cx + 10, cy - 38, cx + 30, cy + 38], fill=VIOLET)
        centred(d, cy + 58, "PAUSE", font(34), TXT)

    # Next
    top = b2 + 50
    d.polygon([(cx - 40, top), (cx + 10, top + 35), (cx - 40, top + 70)], fill=LILAC)
    d.polygon([(cx + 10, top), (cx + 60, top + 35), (cx + 10, top + 70)], fill=LILAC)
    centred(d, top + 92, "NEXT", font(30), TXT)
    return im


if __name__ == "__main__":
    for name, p in (("playing", False), ("paused", True)):
        im = panel(p)
        im.save(os.path.join(HERE, f"panel-{name}.png"))
        with gzip.GzipFile(os.path.join(HERE, f"panel-{name}.rgb.gz"), "wb", mtime=0) as g:
            g.write(im.tobytes())
    print("panels written to", HERE)
