#!/usr/bin/env python3
"""
Swifty Display
Taylor Swift games, fullscreen on the HyperPixel 4.0 (480x800 portrait).

The games run along the bottom: Swiftle (guess the day's song title, letter by letter),
Swiftie Quiz (rounds of ten questions about the songs) and Song Showdown (two songs at a
time: pick the one you love more, and it ranks them all). Everything comes from songs.json
in this folder - no network - and progress is kept on disk (swifty_data), so it carries on
after leaving the app.
"""

import os
import sys
import time
from datetime import datetime

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "..", "common"))
import swifty_data as sd  # noqa: E402

# Use KMS/DRM driver on headless Pi (set before pygame.init)
if "DISPLAY" not in os.environ and "WAYLAND_DISPLAY" not in os.environ:
    os.environ.setdefault("SDL_VIDEODRIVER", "kmsdrm")
    os.environ.setdefault("SDL_KMSDRM_DEVICE_INDEX", "0")
os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
import pygame  # noqa: E402
import pgscreen  # noqa: E402
from swifty_ui import ACCENT, BG_CARD, GOLD, PAD, SCREEN_H, SCREEN_W, TEXT_PRI, TEXT_SEC, Ui  # noqa: E402
from quiz_screen import QuizScreen  # noqa: E402
from showdown_screen import ShowdownScreen  # noqa: E402
from swiftle_screen import SwiftleScreen  # noqa: E402

# ── Config ────────────────────────────────────────────────────────────────────
POLL = 0.05                 # seconds between checks for touches and the time
DIM_AFTER = 300             # seconds of inactivity before dimming (5 min)
DIM_BRIGHTNESS = 120        # 0-255 (higher = brighter when dimmed)
HOME_REQUEST = "/run/pi3-touch/request-home"   # Pi3 Touch launcher picks this up
GAMES = ["Swiftle", "Quiz", "Showdown"]
TABS_Y = 736
TABS_H = SCREEN_H - TABS_Y


class SwiftyDisplay:
    def __init__(self, albums, songs, saves, screen=None):
        if screen is None:
            self.display = pgscreen.Display(SCREEN_W, SCREEN_H, "Swifty")
            screen = self.display.surface
        else:
            self.display = None
            pygame.font.init()
        self.ui = Ui(screen)
        self.screen = screen
        self.swiftle = sd.SwiftleStore(albums, songs, saves)
        self.showdown = sd.ShowdownStore(albums, songs, saves)
        self.quiz = sd.QuizStore(albums, songs, saves)
        self.games = {
            "Swiftle": SwiftleScreen(self.ui, self.swiftle),
            "Quiz": QuizScreen(self.ui, self.quiz),
            "Showdown": ShowdownScreen(self.ui, self.showdown),
        }
        self.game = saves.section("app").get("game", "Swiftle")
        self.saves = saves
        self.last_touch = time.time()
        self.dimmed = False
        self.sleeping = False
        self.drawn = None           # state() of the frame on screen

    @property
    def current(self):
        return self.games[self.game]

    # ── Chrome ────────────────────────────────────────────────────────────────

    def draw_status_bar(self, now, bg):
        ui = self.ui
        ui.text(now.strftime("%H:%M"), ui.font(28, True), TEXT_PRI, (PAD, 12))
        r = ui.text("Swifty", ui.serif(30, True, True), TEXT_PRI, (SCREEN_W // 2 - 6, 30), "center")
        ui.star(GOLD, (r.right + 10, r.top + 6), 8)

        # Sleep button (moon crescent), right of the Home button
        mx, my, mr = SCREEN_W - PAD - 14, 26, 10
        pygame.draw.circle(self.screen, TEXT_SEC, (mx, my), mr)
        pygame.draw.circle(self.screen, bg, (mx + 5, my - 4), mr - 2)
        ui.buttons["sleep"] = pygame.Rect(mx - 18, my - 18, 36, 36)

        # Home button (house) - back to the Pi3 Touch menu; only when the launcher is installed
        if os.path.isdir(os.path.dirname(HOME_REQUEST)):
            hx, hy = SCREEN_W - PAD - 56, 27
            pygame.draw.polygon(self.screen, TEXT_SEC, [(hx - 13, hy - 1), (hx, hy - 12), (hx + 13, hy - 1)])
            pygame.draw.rect(self.screen, TEXT_SEC, (hx - 9, hy - 1, 18, 12))
            pygame.draw.rect(self.screen, bg, (hx - 2, hy + 4, 5, 7))          # door
            ui.buttons["home"] = pygame.Rect(hx - 18, hy - 18, 36, 36)

    def draw_tabs(self):
        ui = self.ui
        pygame.draw.rect(self.screen, BG_CARD, (0, TABS_Y, SCREEN_W, TABS_H))
        w = SCREEN_W / len(GAMES)
        for i, g in enumerate(GAMES):
            r = pygame.Rect(round(i * w), TABS_Y, round(w), TABS_H)
            sel = g == self.game
            if sel:
                pygame.draw.rect(self.screen, ACCENT, (r.x + 14, TABS_Y, r.w - 28, 4), border_radius=2)
            ui.text(g, ui.font(20, sel), ACCENT if sel else TEXT_SEC, r.center, "center")
            ui.buttons[f"game:{g}"] = r

    # ── Drawing a frame ───────────────────────────────────────────────────────

    def draw(self, now=None):
        now = now or datetime.now()
        self.ui.buttons = {}
        bg = self.current.background(now)
        self.screen.fill(bg)
        self.draw_status_bar(now, bg)
        self.current.draw(now)
        self.draw_tabs()
        self._apply_dim()

    def _apply_dim(self):
        if self.sleeping:
            alpha = 245                                  # manual sleep - near-black
        elif self.dimmed:
            alpha = 255 - DIM_BRIGHTNESS
        else:
            return
        dim = pygame.Surface((SCREEN_W, SCREEN_H), pygame.SRCALPHA)
        dim.fill((0, 0, 0, alpha))
        self.screen.blit(dim, (0, 0))

    # ── Touch handling ────────────────────────────────────────────────────────

    def wake(self):
        """Note a touch; True if it only woke the screen."""
        self.last_touch = time.time()
        if self.sleeping or self.dimmed:
            self.sleeping = self.dimmed = False
            return True
        return False

    def tap(self, pos, now=None):
        now = now or datetime.now()
        if self.wake():
            return  # First touch just wakes from sleep or dim
        hit = next((name for name, r in self.ui.buttons.items() if r.collidepoint(pos)), None)
        if hit == "sleep":
            self.sleeping = True
        elif hit == "home":
            try:
                open(HOME_REQUEST, "w").close()
            except OSError as e:
                print(f"[home] can't request menu: {e}", flush=True)
        elif hit and hit.startswith("game:"):
            self.game = hit[5:]
            self.saves.section("app")["game"] = self.game
            self.saves.save()
        elif hit:
            self.current.tap(hit, now)

    def tick(self, now):
        self.dimmed = time.time() - self.last_touch > DIM_AFTER
        if hasattr(self.current, "tick"):
            self.current.tick(now)

    # ── Main loop ─────────────────────────────────────────────────────────────

    def state(self, now):
        """Everything on screen depends on: the game and its state, dimming, the minute."""
        return (self.game, self.current.state(now), self.dimmed, self.sleeping, now.strftime("%H:%M"))

    def frame(self, now=None):
        """Redraw only if something on screen would change. Returns True if it drew."""
        now = now or datetime.now()
        self.tick(now)
        if self.state(now) == self.drawn:
            return False
        self.draw(now)
        self.drawn = self.state(now)
        return True

    def handle(self, event):
        """One pygame event. Returns False to quit."""
        if event.type == pygame.QUIT or (event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE):
            return False
        # SDL also turns touches into mouse events; take the finger ones only
        if event.type == pygame.FINGERDOWN:
            self.tap(self.display.point(event))
        elif event.type == pygame.MOUSEBUTTONDOWN and not getattr(event, "touch", False):
            self.tap(self.display.point(event))
        elif event.type == pygame.KEYDOWN and event.unicode.isalpha() and not self.wake():
            self.current.key(event.unicode.upper(), datetime.now())
        return True

    def run(self):
        while True:
            for event in pygame.event.get():
                if not self.handle(event):
                    pygame.quit()
                    return
            if self.frame():
                self.display.present()
            # Plain sleep: SDL's event.wait() spins on KMS/DRM (no native wait), costing ~8% CPU
            time.sleep(POLL)


if __name__ == "__main__":
    albums, songs = sd.load_catalogue()
    SwiftyDisplay(albums, songs, sd.Saves()).run()
