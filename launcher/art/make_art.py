#!/usr/bin/env python3
"""Render the launcher's menu and countdown banners from apps.json (run on a Mac; needs Pillow).

Outputs menu.{png,rgb.gz} (480x800) and banner-<app id>.{png,rgb.gz} (480 x banner_height).
The countdown bar itself is drawn live by launcher.py.
Fonts: FONT_DIR as for apps/booth-display/art/make_panels.py.
"""
import gzip
import json
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


def menu(cfg):
    L = cfg["layout"]
    im = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(im)
    centred(d, 52, "PI3 TOUCH", mono(18), DIM)
    centred(d, 82, "Choose an app", font(40), TXT)
    w = W - 2 * L["card_margin"]
    for i, app in enumerate(cfg["apps"]):
        x = L["card_margin"]
        y = L["card_top"] + i * (L["card_height"] + L["card_gap"])
        h = L["card_height"]
        d.rounded_rectangle([x, y, x + w, y + h], radius=22, fill=CARD, outline=EDGE, width=2)
        d.rectangle([x + 28, y + h * 0.21, x + 34, y + h * 0.79], fill=VIOLET)    # accent bar
        top = y + (h - 86) // 2                                                    # name + blurb, centred
        d.text((x + 56, top), app["name"], font=font(38), fill=TXT)
        d.text((x + 58, top + 56), app["blurb"], font=mono(16), fill=LILAC)
    return im


def banner(cfg, app):
    L = cfg["layout"]
    im = Image.new("RGB", (W, L["banner_height"]), STRIP)
    d = ImageDraw.Draw(im)
    centred(d, 22, f"Starting {app['name']}", font(30), TXT)
    centred(d, 66, "TAP A CARD TO SWITCH · ANYWHERE ELSE TO STAY", mono(13), DIM)
    return im


if __name__ == "__main__":
    with open(os.path.join(HERE, "..", "apps.json")) as f:
        cfg = json.load(f)
    save(menu(cfg), "menu")
    for app in cfg["apps"]:
        save(banner(cfg, app), f"banner-{app['id']}")
    print("launcher art written to", HERE)
