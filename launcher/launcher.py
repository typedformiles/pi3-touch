#!/usr/bin/env python3
"""Pi3 Touch launcher: the boot menu on the HyperPixel.

Shows one card per app (from apps.json). If an app was used last time, counts down and
starts it again automatically - tap a card to pick one now, or anywhere else to stay on
the menu. Starting an app means starting its systemd target; the launcher then exits
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
WIFI = os.path.join(HERE, "..", "pi", "bin", "pi3-wifi")
ART = os.path.join(HERE, "art")
REDRAW_EVERY = 10
BAR = (126, 12, 247)
BAR_TRACK = (42, 24, 80)


def load_config():
    with open(os.path.join(HERE, "apps.json")) as f:
        return json.load(f)


def card_rects(cfg):
    """Pixel rect (x, y, w, h) of each app card, in apps.json order."""
    L = cfg["layout"]
    w = pitouch.PW - 2 * L["card_margin"]
    return [(L["card_margin"], L["card_top"] + i * (L["card_height"] + L["card_gap"]), w, L["card_height"])
            for i in range(len(cfg["apps"]))]


def bar_rect(cfg):
    L = cfg["layout"]
    return 40, L["banner_top"] + 112, pitouch.PW - 80, 12


def app_at(cfg, nx, ny):
    """The app whose card contains the portrait-normalised point, or None."""
    px, py = nx * pitouch.PW, ny * pitouch.PH
    for app, (x, y, w, h) in zip(cfg["apps"], card_rects(cfg)):
        if x <= px < x + w and y <= py < y + h:
            return app
    return None


def read_last(cfg):
    try:
        with open(LAST_APP) as f:
            last = f.read().strip()
    except OSError:
        return None
    return next((a for a in cfg["apps"] if a["id"] == last), None)


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

    def draw_menu(self):
        self.screen.blit(os.path.join(ART, "menu.rgb.gz"))

    def draw_banner(self, app, remaining):
        self.screen.blit(os.path.join(ART, f"banner-{app['id']}.rgb.gz"),
                         0, self.L["banner_top"], pitouch.PW, self.L["banner_height"])
        self.draw_bar(remaining)

    def draw_bar(self, remaining):
        x, y, w, h = bar_rect(self.cfg)
        filled = round(w * max(0.0, min(1.0, remaining / self.cfg["countdown"])))
        self.screen.fill(x, y, filled, h, BAR)
        self.screen.fill(x + filled, y, w - filled, h, BAR_TRACK)

    def launch(self, app):
        pitouch.log(f"starting {app['name']} ({app['target']})")
        write_last(app)
        self.draw_banner(app, 0)
        switch_wifi(app.get("wifi") or self.cfg.get("wifi"))
        subprocess.run(["systemctl", "start", "--no-block", app["target"]], check=False)
        sys.exit(0)

    def run(self, touch):
        switch_wifi(self.cfg.get("wifi"))
        countdown_app = read_last(self.cfg)
        deadline = time.time() + self.cfg["countdown"] if countdown_app else None
        self.draw_menu()
        if countdown_app:
            self.draw_banner(countdown_app, self.cfg["countdown"])
        last_draw = last_bar = time.time()

        while True:
            for nx, ny, _ in touch.taps(0.2):
                app = app_at(self.cfg, nx, ny)
                if app:
                    self.launch(app)
                if deadline:
                    pitouch.log("countdown cancelled")
                    deadline = None
                    self.draw_menu()
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
