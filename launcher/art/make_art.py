#!/usr/bin/env python3
"""Render the launcher's menu pages and countdown banners from apps.json (run on a Mac; needs Pillow).

Outputs menu-<page>.{png,rgb.gz} (480x800, a cols x rows grid of app tiles per page) and
banner-<app id>.{png,rgb.gz} (480 x banner_height). Tile icons are drawn below, one per app id.
The countdown bar itself is drawn live by launcher.py.
Fonts: FONT_DIR as for apps/booth-display/art/make_panels.py.
"""
import gzip
import glob
import json
import math
import os
from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
FONT_DIR = os.environ.get("FONT_DIR", os.path.expanduser("~/claudecode/Neuralift/G2E Video/source/fonts"))
W, H = 480, 800
BG = (7, 4, 15); CARD = (24, 14, 46); EDGE = (70, 40, 130); TXT = (240, 236, 250)
DIM = (150, 130, 190); LILAC = (196, 155, 255); VIOLET = (126, 12, 247); STRIP = (20, 12, 38)


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


def centred(d, y, text, f, fill, w=W):
    d.text(((w - d.textlength(text, font=f)) / 2, y), text, font=f, fill=fill)


def save(im, name):
    im.save(os.path.join(HERE, name + ".png"))
    with gzip.GzipFile(os.path.join(HERE, name + ".rgb.gz"), "wb", mtime=0) as g:
        g.write(im.tobytes())


# ── Tile icons: drawn 4x size on a transparent canvas, then scaled down (anti-aliasing) ──

def icon_booth(d, s):                       # a screen on a stand, showing a play mark
    d.rounded_rectangle([s * .12, s * .2, s * .88, s * .7], radius=s * .06, outline=LILAC, width=round(s * .06))
    d.polygon([(s * .43, s * .34), (s * .43, s * .56), (s * .62, s * .45)], fill=TXT)
    d.rectangle([s * .46, s * .7, s * .54, s * .8], fill=LILAC)
    d.rounded_rectangle([s * .32, s * .78, s * .68, s * .85], radius=s * .03, fill=LILAC)


def icon_moode(d, s):                       # two beamed quavers
    w = round(s * .06)
    for x in (.3, .68):
        d.ellipse([s * (x - .14), s * .64, s * (x + .04), s * .8], fill=TXT)
        d.rectangle([s * (x + .01), s * .25, s * (x + .01) + w, s * .72], fill=TXT)
    d.polygon([(s * .31, s * .2), (s * .75, s * .1), (s * .75, s * .2), (s * .31, s * .3)], fill=LILAC)


def icon_weather(d, s):                     # sun behind a cloud
    cx, cy, r = s * .36, s * .38, s * .14
    for i in range(8):
        a = math.radians(i * 45)
        d.line([(cx + math.cos(a) * r * 1.4, cy + math.sin(a) * r * 1.4),
                (cx + math.cos(a) * r * 1.95, cy + math.sin(a) * r * 1.95)], fill=(250, 196, 64), width=round(s * .045))
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(250, 196, 64))
    for bx, by, br in ((.4, .64, .14), (.58, .55, .2), (.76, .66, .12)):
        d.ellipse([s * (bx - br), s * (by - br), s * (bx + br), s * (by + br)], fill=TXT)
    d.rectangle([s * .4, s * .62, s * .76, s * .78], fill=TXT)


def icon_clock(d, s):                       # a clock face with an orbit, for "world"
    c, r = s * .5, s * .3
    d.ellipse([c - r * 1.42, c - r * .55, c + r * 1.42, c + r * .55], outline=VIOLET, width=round(s * .035))
    d.ellipse([c - r, c - r, c + r, c + r], fill=TXT)
    for i in range(12):
        a = math.radians(i * 30)
        d.line([(c + math.sin(a) * r * .78, c - math.cos(a) * r * .78),
                (c + math.sin(a) * r * .92, c - math.cos(a) * r * .92)], fill=CARD, width=round(s * .025))
    d.line([(c, c), (c + r * .45, c - r * .3)], fill=CARD, width=round(s * .05))
    d.line([(c, c), (c - r * .1, c - r * .72)], fill=CARD, width=round(s * .035))
    d.ellipse([c - s * .03, c - s * .03, c + s * .03, c + s * .03], fill=VIOLET)


def icon_energy(d, s):                      # a green leaf with a lightning bolt over it
    pts = []
    for i in range(31):                     # two arcs from the stalk (bottom left) to the tip
        t = i / 30
        pts.append((s * (.16 + .68 * t) + s * .2 * math.sin(math.pi * t), s * (.84 - .68 * t) + s * .2 * math.sin(math.pi * t)))
    for i in range(30, -1, -1):
        t = i / 30
        pts.append((s * (.16 + .68 * t) - s * .2 * math.sin(math.pi * t), s * (.84 - .68 * t) - s * .2 * math.sin(math.pi * t)))
    d.polygon(pts, fill=(80, 200, 130))
    d.line([(s * .1, s * .9), (s * .3, s * .7)], fill=(80, 200, 130), width=round(s * .05))
    d.polygon([(s * .56, s * .14), (s * .3, s * .54), (s * .48, s * .54), (s * .4, s * .86),
               (s * .7, s * .42), (s * .52, s * .42), (s * .62, s * .14)], fill=(250, 204, 70))


ICONS = {"booth-display": icon_booth, "moode-remote": icon_moode, "weather": icon_weather,
         "world-clock": icon_clock, "energy": icon_energy}


def icon(app, size):
    big = size * 4
    im = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    if app["id"] in ICONS:
        ICONS[app["id"]](d, big)
    else:                                   # no icon yet: the app's initial
        d.rounded_rectangle([big * .15, big * .15, big * .85, big * .85], radius=big * .16, fill=VIOLET)
        f = font(big // 2)
        letter = app["name"][:1].upper()
        d.text(((big - d.textlength(letter, font=f)) / 2, big * .2), letter, font=f, fill=TXT)
    return im.resize((size, size), Image.LANCZOS)


def wrap(d, text, f, width):
    """Split text into lines no wider than width."""
    lines, line = [], ""
    for word in text.split():
        trial = f"{line} {word}".strip()
        if line and d.textlength(trial, font=f) > width:
            lines.append(line)
            line = word
        else:
            line = trial
    return lines + ([line] if line else [])


def tiles(cfg):
    """(page, x, y, w, h) per app - the same sums as launcher.tile_rects()."""
    L = cfg["layout"]
    per = L["cols"] * L["rows"]
    w = (W - 2 * L["margin"] - (L["cols"] - 1) * L["gap"]) // L["cols"]
    out = []
    for i in range(len(cfg["apps"])):
        page, slot = divmod(i, per)
        row, col = divmod(slot, L["cols"])
        out.append((page, L["margin"] + col * (w + L["gap"]), L["tile_top"] + row * (L["tile_height"] + L["gap"]),
                    w, L["tile_height"]))
    return out


def menu(cfg, page, pages):
    L = cfg["layout"]
    im = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(im)
    centred(d, 52, "PI3 TOUCH", mono(18), DIM)
    centred(d, 82, "Choose an app", font(40), TXT)
    for app, (p, x, y, w, h) in zip(cfg["apps"], tiles(cfg)):
        if p != page:
            continue
        d.rounded_rectangle([x, y, x + w, y + h], radius=22, fill=CARD, outline=EDGE, width=2)
        ic = icon(app, 84)
        im.paste(ic, (x + (w - 84) // 2, y + 26), ic)
        nf = font(25)
        d.text((x + (w - d.textlength(app["name"], font=nf)) / 2, y + 122), app["name"], font=nf, fill=TXT)
        bf = mono(13)
        for j, line in enumerate(wrap(d, app["blurb"], bf, w - 24)[:2]):
            d.text((x + (w - d.textlength(line, font=bf)) / 2, y + 166 + j * 19), line, font=bf, fill=LILAC)
    if pages > 1:                           # page dots - same sums as launcher.dot_centres()
        step = L["dot_spacing"]
        x0 = W // 2 - step * (pages - 1) // 2
        for i in range(pages):
            r = 6 if i == page else 5
            cx, cy = x0 + i * step, L["dots_y"]
            d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=LILAC if i == page else EDGE)
    return im


def banner(cfg, app):
    L = cfg["layout"]
    im = Image.new("RGB", (W, L["banner_height"]), STRIP)
    d = ImageDraw.Draw(im)
    centred(d, 22, f"Starting {app['name']}", font(30), TXT)
    centred(d, 66, "TAP A TILE TO SWITCH · ANYWHERE ELSE TO STAY", mono(13), DIM)
    return im


if __name__ == "__main__":
    with open(os.path.join(HERE, "..", "apps.json")) as f:
        cfg = json.load(f)
    per = cfg["layout"]["cols"] * cfg["layout"]["rows"]
    pages = max(1, -(-len(cfg["apps"]) // per))
    for old in glob.glob(os.path.join(HERE, "menu*.png")) + glob.glob(os.path.join(HERE, "menu*.rgb.gz")):
        os.remove(old)
    for page in range(pages):
        save(menu(cfg, page, pages), f"menu-{page + 1}")
    for app in cfg["apps"]:
        save(banner(cfg, app), f"banner-{app['id']}")
    print("launcher art written to", HERE)
