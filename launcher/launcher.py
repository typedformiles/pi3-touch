#!/usr/bin/env python3
"""Pi3 Touch launcher: the boot menu on the HyperPixel.

Shows the apps (from apps.json) as tiles, a page of cols x rows at a time - swipe sideways
or tap the dots under them to change page, opening on the page of the last app used. Just
after power-up it also counts down and starts that app again by itself (so a booth display
recovers from a power cut) - tap a tile to pick one now, or anywhere else to stay on the
menu. Coming back via an app's Home button never counts down: the menu stays put.
Starting an app means starting its systemd target; the launcher then exits
(the targets conflict with it), and the app's Home control brings it back.

Wi-Fi: the menu (and any app without its own "wifi") uses the top-level "wifi" network in
apps.json; an app can name its own. Switching is done by pi/bin/pi3-wifi in the background,
only when that network is in range.

Runs as root: it unbinds the text console, reads/writes /var/lib/pi3-touch and runs systemctl.
"""
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "common"))
import pitouch  # noqa: E402

LAST_APP = "/var/lib/pi3-touch/last-app"
BOOTED = "/run/pi3-touch/menu-shown"   # /run empties at boot: absent means we just powered up
WIFI = os.path.join(HERE, "..", "pi", "bin", "pi3-wifi")
ART = os.path.join(HERE, "art")
REDRAW_EVERY = 10
SWIPE = 0.15                    # sideways travel (fraction of the width) that turns the page
DOT_HIT = 40                    # px either side of the dots' row that counts as tapping them
BAR = (126, 12, 247)
BAR_TRACK = (42, 24, 80)
HIGHLIGHT = (196, 155, 255)     # a tapped tile's outline (the art's lilac)
TILE_RADIUS = 22                # the tiles' rounded corners, as make_art.py draws them


def load_config():
    with open(os.path.join(HERE, "apps.json")) as f:
        return json.load(f)


def per_page(cfg):
    return cfg["layout"]["cols"] * cfg["layout"]["rows"]


def page_count(cfg):
    return max(1, -(-len(cfg["apps"]) // per_page(cfg)))


def tile_rects(cfg):
    """(page, x, y, w, h) of each app's tile, in apps.json order: rows filled left to right."""
    L = cfg["layout"]
    w = (pitouch.PW - 2 * L["margin"] - (L["cols"] - 1) * L["gap"]) // L["cols"]
    out = []
    for i in range(len(cfg["apps"])):
        page, slot = divmod(i, per_page(cfg))
        row, col = divmod(slot, L["cols"])
        out.append((page, L["margin"] + col * (w + L["gap"]), L["tile_top"] + row * (L["tile_height"] + L["gap"]),
                    w, L["tile_height"]))
    return out


def dot_centres(cfg):
    """Pixel centres of the page dots (none when everything fits on one page)."""
    n = page_count(cfg)
    if n < 2:
        return []
    step = cfg["layout"]["dot_spacing"]
    x0 = pitouch.PW // 2 - step * (n - 1) // 2
    return [(x0 + i * step, cfg["layout"]["dots_y"]) for i in range(n)]


def bar_rect(cfg):
    L = cfg["layout"]
    return 40, L["banner_top"] + 112, pitouch.PW - 80, 12


def app_at(cfg, page, nx, ny):
    """The app whose tile on this page contains the portrait-normalised point, or None."""
    px, py = nx * pitouch.PW, ny * pitouch.PH
    for app, (p, x, y, w, h) in zip(cfg["apps"], tile_rects(cfg)):
        if p == page and x <= px < x + w and y <= py < y + h:
            return app
    return None


def page_of(cfg, app):
    return cfg["apps"].index(app) // per_page(cfg)


def dot_at(cfg, nx, ny):
    """The page whose dot (generously) contains the point, or None."""
    px, py = nx * pitouch.PW, ny * pitouch.PH
    dots = dot_centres(cfg)
    if not dots or abs(py - dots[0][1]) > DOT_HIT:
        return None
    half = cfg["layout"]["dot_spacing"] / 2
    return next((i for i, (x, _) in enumerate(dots) if abs(px - x) <= half), None)


def swipe(nx, ny, sx, sy):
    """-1 (swiped right: previous page), +1 (swiped left: next page) or 0 for a tap."""
    dx, dy = (nx - sx) * pitouch.PW, (ny - sy) * pitouch.PH
    if abs(dx) < SWIPE * pitouch.PW or abs(dx) < abs(dy):
        return 0
    return -1 if dx > 0 else 1


def outline_boxes(x, y, w, h, r, t):
    """Boxes (x, y, w, h) that draw a t px outline of a w x h rectangle with corners of radius r
    (boxes are all the framebuffer can draw): a row at a time through the corners."""
    import math
    ri = r - t                                   # the inside edge's corner radius

    def inset(rad, d):                           # how far a rounded corner cuts into row d
        return rad - math.sqrt(max(0.0, rad * rad - d * d))

    boxes = []
    for j in list(range(r)) + list(range(h - r, h)):
        d = r - 0.5 - (j if j < r else h - 1 - j)          # distance above the corner's centre
        o = round(inset(r, d))
        if j < t or j >= h - t:                  # top or bottom band: one solid row
            boxes.append((x + o, y + j, w - 2 * o, 1))
            continue
        i = t + round(inset(ri, d - t)) if d - t > 0 else t
        boxes += [(x + o, y + j, i - o, 1), (x + w - i, y + j, i - o, 1)]
    boxes += [(x, y + r, t, h - 2 * r), (x + w - t, y + r, t, h - 2 * r)]
    return [b for b in boxes if b[2] > 0 and b[3] > 0]


def read_last(cfg):
    try:
        with open(LAST_APP) as f:
            last = f.read().strip()
    except OSError:
        return None
    return next((a for a in cfg["apps"] if a["id"] == last), None)


def first_since_boot():
    """True the first time the menu runs after power-up (and remembers that it has)."""
    if os.path.exists(BOOTED):
        return False
    try:
        open(BOOTED, "w").close()
    except OSError as e:
        pitouch.log("can't note the menu has run:", e)
    return True


def write_last(app):
    try:
        os.makedirs(os.path.dirname(LAST_APP), exist_ok=True)
        with open(LAST_APP, "w") as f:
            f.write(app["id"])
    except OSError as e:
        pitouch.log("can't remember last app:", e)


def switch_wifi(ssid):
    """Move to a Wi-Fi network in the background (its own transient unit, so it outlives us)."""
    if ssid:
        subprocess.run(["systemd-run", "--no-block", "--collect", "--quiet",
                        os.path.realpath(WIFI), ssid], check=False)


class Launcher:
    def __init__(self, cfg, screen):
        self.cfg, self.screen = cfg, screen
        self.L = cfg["layout"]
        self.page = 0

    def draw_menu(self):
        self.screen.blit(os.path.join(ART, f"menu-{self.page + 1}.rgb.gz"))

    def draw_banner(self, app, remaining):
        self.screen.blit(os.path.join(ART, f"banner-{app['id']}.rgb.gz"),
                         0, self.L["banner_top"], pitouch.PW, self.L["banner_height"])
        self.draw_bar(remaining)

    def draw_bar(self, remaining):
        x, y, w, h = bar_rect(self.cfg)
        filled = round(w * max(0.0, min(1.0, remaining / self.cfg["countdown"])))
        self.screen.fill(x, y, filled, h, BAR)
        self.screen.fill(x + filled, y, w - filled, h, BAR_TRACK)

    def highlight(self, app):
        """Outline a tapped tile - the answer to the tap while the app starts (~2 s on a Pi 3)."""
        _, x, y, w, h = tile_rects(self.cfg)[self.cfg["apps"].index(app)]
        for box in outline_boxes(x, y, w, h, TILE_RADIUS, 5):
            self.screen.fill(*box, HIGHLIGHT)

    def launch(self, app, tapped=False):
        pitouch.log(f"starting {app['name']} ({app['target']})")
        write_last(app)
        if tapped:
            self.highlight(app)
        else:
            self.draw_banner(app, 0)
        switch_wifi(app.get("wifi") or self.cfg.get("wifi"))
        subprocess.run(["systemctl", "start", "--no-block", app["target"]], check=False)
        sys.exit(0)

    def turn_to(self, page):
        page = max(0, min(page, page_count(self.cfg) - 1))
        if page != self.page:
            self.page = page
            self.draw_menu()

    def run(self, touch):
        switch_wifi(self.cfg.get("wifi"))
        last = read_last(self.cfg)
        countdown_app = last if first_since_boot() else None
        deadline = time.time() + self.cfg["countdown"] if countdown_app else None
        if last:
            self.page = page_of(self.cfg, last)
        self.draw_menu()
        if countdown_app:
            self.draw_banner(countdown_app, self.cfg["countdown"])
        last_draw = last_bar = time.time()

        while True:
            for nx, ny, _, sx, sy in touch.touches(0.2):
                step = swipe(nx, ny, sx, sy)
                app = None if step else app_at(self.cfg, self.page, sx, sy)
                if app:
                    self.launch(app, tapped=True)
                if deadline:
                    pitouch.log("countdown cancelled")
                    deadline = None
                    self.draw_menu()
                if step:
                    self.turn_to(self.page + step)
                elif dot_at(self.cfg, sx, sy) is not None:
                    self.turn_to(dot_at(self.cfg, sx, sy))
            now = time.time()
            if deadline:
                if now >= deadline:
                    self.launch(countdown_app)
                if now - last_bar >= 0.25:
                    self.draw_bar(deadline - now)
                    last_bar = now
            if now - last_draw > REDRAW_EVERY:
                self.draw_menu()
                if deadline:
                    self.draw_banner(countdown_app, deadline - now)
                last_draw = now


def main():
    pitouch.fbcon_off()
    cfg = load_config()
    touch = pitouch.Touch()
    Launcher(cfg, pitouch.Screen()).run(touch)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
