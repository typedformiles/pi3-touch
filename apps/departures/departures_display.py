#!/usr/bin/env python3
"""
Departures Display
Live train times, fullscreen on the HyperPixel 4.0 (480x800 portrait).

Stations (departures.json) run along the top: tap one to switch, or swipe left/right. A
station can be split into sections by direction (Taplow: to London, to Reading), or show
one direction with the time each train gets to another station (Paddington westbound,
and when it reaches Taplow). The line's status sits above the board; tap it to read all
of TfL's message. Data is fetched in the background (departures_data.Store); the dot top
right is green when it's fresh.
"""

import os
import sys
import time
from datetime import datetime

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "..", "common"))
import departures_data as dd  # noqa: E402

# Use KMS/DRM driver on headless Pi (set before pygame.init)
if "DISPLAY" not in os.environ and "WAYLAND_DISPLAY" not in os.environ:
    os.environ.setdefault("SDL_VIDEODRIVER", "kmsdrm")
    os.environ.setdefault("SDL_KMSDRM_DEVICE_INDEX", "0")
os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
import pygame  # noqa: E402
import pgscreen  # noqa: E402

# ── Config ────────────────────────────────────────────────────────────────────
SCREEN_W, SCREEN_H = 480, 800
POLL = 0.05                 # seconds between checks for touches, new data and the time
DIM_AFTER = 300             # seconds of inactivity before dimming (5 min)
DIM_BRIGHTNESS = 120        # 0-255 (higher = brighter when dimmed)
SWIPE = 80                  # px of sideways travel that counts as a swipe
STALE_AFTER = 120           # seconds without fresh predictions before the dot turns amber
HOME_REQUEST = "/run/pi3-touch/request-home"   # Pi3 Touch launcher picks this up
FONT_DIR = os.environ.get("PI3_FONT_DIR", "/usr/share/fonts/truetype/dejavu")

# ── Colours ───────────────────────────────────────────────────────────────────
BG          = (10,  16,  26)
BG_CARD     = (22,  32,  48)
BG_CARD_HI  = (36,  52,  76)
TEXT_PRI    = (238, 242, 248)
TEXT_SEC    = (150, 165, 185)
TEXT_DIM    = (88,  102, 122)
LINE        = (105, 80,  161)   # Elizabeth line purple
LINE_LIGHT  = (178, 156, 240)
OK_GREEN    = (80,  200, 120)
STALE       = (230, 170, 60)
BAD_RED     = (225, 70,  80)

# ── Layout constants (portrait 480x800) ───────────────────────────────────────
PAD     = 16
LOC_Y   = 58            # station buttons
LOC_H   = 50
BOARD_B = SCREEN_H - 10
ROW_H   = 42
SHORT_ROW_H = 32        # a train that doesn't go where you're going


def load_font(size, bold=False):
    path = os.path.join(FONT_DIR, "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf")
    try:
        return pygame.font.Font(path, size)
    except (OSError, FileNotFoundError):
        return pygame.font.SysFont("dejavusans,freesans,arial", size, bold=bold)


def truncate(text, font, max_width):
    if font.size(text)[0] <= max_width:
        return text
    while text and font.size(text + "…")[0] > max_width:
        text = text[:-1]
    return text + "…"


def wrap(text, font, width):
    """Split text into lines no wider than width."""
    lines, line = [], ""
    for word in text.split():
        trial = f"{line} {word}".strip()
        if line and font.size(trial)[0] > width:
            lines.append(line)
            line = word
        else:
            line = trial
    return lines + ([line] if line else [])


def hhmm(t):
    return f"{t.astimezone(dd.LOCAL):%H:%M}"


def severity_colour(sev):
    if sev >= dd.GOOD_SERVICE:
        return OK_GREEN
    return BAD_RED if sev <= 6 or sev == 20 else STALE      # severe, closures / delays, reduced


class DeparturesDisplay:
    def __init__(self, config, store, screen=None):
        self.stations = config["stations"]
        self.line_name = {"elizabeth": "Elizabeth line"}.get(config.get("line", "elizabeth"),
                                                             config.get("line", "").title())
        self.store = store

        if screen is None:
            self.display = pgscreen.Display(SCREEN_W, SCREEN_H, "Departures")
            screen = self.display.surface
        else:
            self.display = None
            pygame.font.init()
        self.screen = screen

        self.f_huge  = load_font(56, bold=True)
        self.f_big   = load_font(34, bold=True)
        self.f_large = load_font(26, bold=True)
        self.f_med   = load_font(20)
        self.f_medb  = load_font(20, bold=True)
        self.f_small = load_font(16)
        self.f_smallb = load_font(16, bold=True)
        self.f_tiny  = load_font(14)
        self.f_tinyb = load_font(14, bold=True)
        self.f_loc   = load_font(19, bold=True)
        self.f_clock = load_font(28, bold=True)

        self.station_i = 0
        self.status_open = False
        self.last_touch = time.time()
        self.dimmed = False
        self.sleeping = False
        self.down = None            # where the current touch started
        self.buttons = {}           # name -> Rect, rebuilt every frame
        self.drawn = None           # state() of the frame on screen

    # ── Helpers ───────────────────────────────────────────────────────────────

    @property
    def station(self):
        return self.stations[self.station_i]

    def text(self, s, font, colour, pos, anchor="topleft"):
        surf = font.render(s, True, colour)
        rect = surf.get_rect(**{anchor: pos})
        self.screen.blit(surf, rect)
        return rect

    def card(self, rect, colour=BG_CARD, radius=14):
        pygame.draw.rect(self.screen, colour, rect, border_radius=radius)

    def chip_width(self, name):
        return max(34, self.f_tinyb.size(self.chip_label(name))[0] + 12) if name else 0

    @staticmethod
    def chip_label(name):
        return f"P{name}" if name.isdigit() else name

    def platform_chip(self, right, cy, name, colour=LINE):
        """A small platform badge ending at x = right; returns its left edge."""
        if not name:
            return right
        label = self.chip_label(name)
        w = self.chip_width(name)
        r = pygame.Rect(right - w, cy - 12, w, 24)
        pygame.draw.rect(self.screen, colour, r, border_radius=6)
        self.text(label, self.f_tinyb, TEXT_PRI, r.center, "center")
        return r.x

    # ── Chrome: status bar, stations, line status ────────────────────────────

    def draw_status_bar(self, now):
        self.text(now.strftime("%H:%M"), self.f_clock, TEXT_PRI, (PAD, 12))

        entry = self.store.get(self.station["stop"])
        age = time.time() - entry["fetched"] if entry else None
        if age is not None:
            self.text(f"updated {datetime.fromtimestamp(entry['fetched']):%H:%M:%S}", self.f_tiny, TEXT_DIM,
                      (PAD + 92, 24))
        dot = BAD_RED if age is None else OK_GREEN if age < STALE_AFTER and not self.store.error() else STALE
        pygame.draw.circle(self.screen, dot, (SCREEN_W - PAD - 8, 28), 8)

        # Sleep button (moon crescent) - left of the status dot
        mx, my, mr = SCREEN_W - PAD - 40, 26, 10
        pygame.draw.circle(self.screen, TEXT_SEC, (mx, my), mr)
        pygame.draw.circle(self.screen, BG, (mx + 5, my - 4), mr - 2)
        self.buttons["sleep"] = pygame.Rect(mx - 18, my - 18, 36, 36)

        # Home button (house) - back to the Pi3 Touch menu; only when the launcher is installed
        if os.path.isdir(os.path.dirname(HOME_REQUEST)):
            hx, hy = SCREEN_W - PAD - 82, 27
            pygame.draw.polygon(self.screen, TEXT_SEC, [(hx - 13, hy - 1), (hx, hy - 12), (hx + 13, hy - 1)])
            pygame.draw.rect(self.screen, TEXT_SEC, (hx - 9, hy - 1, 18, 12))
            pygame.draw.rect(self.screen, BG, (hx - 2, hy + 4, 5, 7))          # door
            self.buttons["home"] = pygame.Rect(hx - 18, hy - 18, 36, 36)

    def draw_stations(self):
        n = len(self.stations)
        gap = 8
        w = (SCREEN_W - 2 * PAD - gap * (n - 1)) / n
        for i, st in enumerate(self.stations):
            r = pygame.Rect(round(PAD + i * (w + gap)), LOC_Y, round(w), LOC_H)
            sel = i == self.station_i
            self.card(r, LINE if sel else BG_CARD, radius=12)
            self.text(truncate(st["name"], self.f_loc, r.w - 10), self.f_loc, TEXT_PRI if sel else TEXT_SEC,
                      r.center, "center")
            self.buttons[f"station{i}"] = r

    def draw_line_status(self, y):
        """The line's status as a card from y; tap it to open TfL's full message. Returns its bottom."""
        statuses = self.store.status()
        r = pygame.Rect(PAD, y, SCREEN_W - 2 * PAD, 44)
        if statuses is None:
            self.card(r)
            self.text(f"{self.line_name}: checking status…", self.f_small, TEXT_DIM, (r.x + 14, r.centery), "midleft")
            return r.bottom
        worst = statuses[0] if statuses else {"severity": dd.GOOD_SERVICE, "text": "Good Service", "reason": ""}
        col = severity_colour(worst["severity"])
        reasons = [s["reason"] for s in statuses if s["reason"]]
        lines = wrap(" ".join(reasons), self.f_tiny, r.w - 28) if reasons else []
        shown = lines if self.status_open else lines[:1]
        r.h += len(shown) * 19 + (6 if shown else 0)
        self.card(r)
        pygame.draw.circle(self.screen, col, (r.x + 18, r.y + 22), 7)
        words = " · ".join(dict.fromkeys(s["text"] for s in statuses)) or "Good Service"
        self.text(truncate(f"{self.line_name}: {words}", self.f_smallb, r.w - 70), self.f_smallb, TEXT_PRI,
                  (r.x + 34, r.y + 12))
        if lines:
            self.text("less" if self.status_open else "more", self.f_tiny, LINE_LIGHT, (r.right - 14, r.y + 14),
                      "topright")
            for i, line in enumerate(shown):
                if i == len(shown) - 1 and len(shown) < len(lines):
                    line = truncate(line + " …", self.f_tiny, r.w - 28)
                self.text(line, self.f_tiny, TEXT_SEC, (r.x + 14, r.y + 40 + i * 19))
            self.buttons["status"] = r
        return r.bottom

    def draw_message(self, top, line1, line2=""):
        cy = (top + BOARD_B) // 2
        self.text(line1, self.f_med, TEXT_SEC, (SCREEN_W // 2, cy - 16), "center")
        if line2:
            self.text(line2, self.f_small, TEXT_DIM, (SCREEN_W // 2, cy + 18), "center")

    # ── Boards ────────────────────────────────────────────────────────────────

    def draw_row(self, d, y, now, dim=False, extra="", h=ROW_H):
        """One train on a line: time, destination, platform, countdown (and extra under it)."""
        r = pygame.Rect(PAD - 6, y, SCREEN_W - 2 * PAD + 12, h - 4)
        col, sec = (TEXT_DIM, TEXT_DIM) if dim else (TEXT_PRI, TEXT_SEC)
        cy = r.centery - (7 if extra else 0)
        self.text(hhmm(d["time"]), self.f_medb, col, (PAD + 2, cy), "midleft")
        cr = self.text(dd.countdown(d["time"], now), self.f_medb, col, (SCREEN_W - PAD, cy), "midright")
        px = self.platform_chip(cr.x - 12, cy, d["platform"], BG_CARD_HI if dim else LINE)
        self.text(truncate(d["dest"], self.f_med, px - 88 - 8), self.f_med, col, (PAD + 72, cy), "midleft")
        if extra:
            self.text(extra, self.f_tiny, sec, (PAD + 72, cy + 11))

    def draw_hero(self, rect, d, now, label, foot=""):
        """The next train, big: time and countdown, destination and platform, and a footnote."""
        self.card(rect, BG_CARD)
        pygame.draw.rect(self.screen, LINE, (rect.x, rect.y + 10, 5, rect.h - 20), border_radius=2)
        self.text(label, self.f_small, TEXT_SEC, (rect.x + 18, rect.y + 10))
        tr = self.text(hhmm(d["time"]), self.f_big, TEXT_PRI, (rect.x + 16, rect.y + 32))
        self.text(truncate(d["dest"], self.f_medb, rect.right - tr.right - 150), self.f_medb, TEXT_PRI,
                  (tr.right + 14, tr.centery), "midleft")
        cd = dd.countdown(d["time"], now)
        self.text(cd, self.f_big, LINE_LIGHT, (rect.right - 14, rect.y + 32), "topright")
        y = tr.bottom + 8
        px = rect.x + 18
        if d["platform"]:
            px += self.chip_width(d["platform"])
            self.platform_chip(px, y + 12, d["platform"])
            px += 10
        if foot:
            self.text(truncate(foot, self.f_small, rect.right - 14 - px), self.f_small, TEXT_SEC, (px, y + 2))

    def draw_sections(self, top, now):
        """A station by direction: per section, the next train big and a few after it."""
        deps = self.store.departures(self.station, now)
        if deps is None:
            return self.draw_waiting(top)
        sections = self.station["sections"]
        each = (BOARD_B - top) // len(sections)
        for k, sec in enumerate(sections):
            y = top + k * each
            mine = [d for d in deps if d["direction"] == sec["direction"]]
            if not mine:
                self.text(sec["title"], self.f_medb, TEXT_PRI, (PAD, y + 4))
                self.text("No trains in the next hour or two", self.f_small, TEXT_DIM, (PAD, y + 40))
                continue
            hero = pygame.Rect(PAD, y, SCREEN_W - 2 * PAD, 108)
            first = mine[0]
            after = f"then {dd.countdown(mine[1]['time'], now)}" if len(mine) > 1 else "last for now"
            self.draw_hero(hero, first, now, sec["title"], after)
            ry = hero.bottom + 6
            for d in mine[1:]:
                if ry + ROW_H > y + each - 6:
                    break
                self.draw_row(d, ry, now)
                ry += ROW_H

    def draw_journey(self, top, now):
        """One direction, with when each train gets to the "to" station."""
        st = self.station
        to = st["to"]["name"]
        deps = self.store.departures(st, now)
        if deps is None:
            return self.draw_waiting(top)
        home = [d for d in deps if d["calls"]]
        hero = pygame.Rect(PAD, top, SCREEN_W - 2 * PAD, 108)
        if home:
            d = home[0]
            mins = round((d["home"] - d["time"]).total_seconds() / 60)
            foot = f"{to} {'~' if d['estimated'] else ''}{hhmm(d['home'])} · {mins} min ride"
            self.draw_hero(hero, d, now, f"Next train to {to}", foot)
        else:
            self.card(hero)
            self.text(f"Next train to {to}", self.f_small, TEXT_SEC, (hero.x + 18, hero.y + 10))
            self.text("None in the next hour or two", self.f_medb, TEXT_DIM, (hero.x + 18, hero.y + 46))
        y = hero.bottom + 14
        self.text("Westbound" if st.get("direction") == "outbound" else "All trains", self.f_smallb, TEXT_SEC,
                  (PAD, y))
        self.text(f"greyed: not stopping at {to}", self.f_tiny, TEXT_DIM, (SCREEN_W - PAD, y + 1), "topright")
        y += 26
        for d in deps:
            h = ROW_H if d["calls"] else SHORT_ROW_H
            if y + h > BOARD_B:
                break
            extra = f"{to} {'~' if d['estimated'] else ''}{hhmm(d['home'])}" if d["calls"] else ""
            self.draw_row(d, y, now, dim=not d["calls"], extra=extra, h=h)
            y += h

    def draw_waiting(self, top):
        err = self.store.error()
        self.draw_message(top, f"Fetching trains for {self.station['name']}…", f"({err} - retrying)" if err else "")

    # ── Drawing a frame ───────────────────────────────────────────────────────

    def draw(self, now=None):
        now = now or datetime.now(dd.LOCAL)
        self.buttons = {}
        self.screen.fill(BG)
        self.draw_status_bar(now)
        self.draw_stations()
        top = self.draw_line_status(LOC_Y + LOC_H + 10) + 12
        if self.station.get("sections"):
            self.draw_sections(top, now)
        else:
            self.draw_journey(top, now)
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

    def turn(self, step):
        self.station_i = (self.station_i + step) % len(self.stations)

    def touch_down(self, pos):
        self.down = pos

    def touch_up(self, pos):
        """A touch ended: a sideways swipe changes station, anything else is a tap where it started."""
        start, self.down = self.down or pos, None
        dx, dy = pos[0] - start[0], pos[1] - start[1]
        if abs(dx) > SWIPE and abs(dx) > abs(dy) and not (self.sleeping or self.dimmed):
            self.last_touch = time.time()
            self.turn(-1 if dx > 0 else 1)
        else:
            self.tap(start)

    def tap(self, pos):
        self.last_touch = time.time()
        if self.sleeping:
            self.sleeping = False
            return  # First touch just wakes from sleep
        if self.dimmed:
            self.dimmed = False
            return  # First touch just wakes from dim

        hit = next((name for name, r in self.buttons.items() if r.collidepoint(pos)), None)
        if hit == "sleep":
            self.sleeping = True
        elif hit == "home":
            try:
                open(HOME_REQUEST, "w").close()
            except OSError as e:
                print(f"[home] can't request menu: {e}", flush=True)
        elif hit == "status":
            self.status_open = not self.status_open
        elif hit and hit.startswith("station"):
            self.station_i = int(hit[7:])

    def tick(self):
        """Idle bookkeeping: dimming."""
        self.dimmed = time.time() - self.last_touch > DIM_AFTER

    # ── Main loop ─────────────────────────────────────────────────────────────

    def state(self, now):
        """Everything on screen depends on: what's selected, dimming, the minute and the data."""
        return (self.station_i, self.status_open, self.dimmed, self.sleeping, now.strftime("%Y-%m-%d %H:%M"),
                self.store.stamp(), self.store.error())

    def frame(self, now=None):
        """Redraw only if something on screen would change. Returns True if it drew."""
        now = now or datetime.now(dd.LOCAL)
        self.tick()
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
            self.touch_down(self.display.point(event))
        elif event.type == pygame.FINGERUP:
            self.touch_up(self.display.point(event))
        elif event.type == pygame.MOUSEBUTTONDOWN and not getattr(event, "touch", False):
            self.touch_down(self.display.point(event))
        elif event.type == pygame.MOUSEBUTTONUP and not getattr(event, "touch", False):
            self.touch_up(self.display.point(event))
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
    cfg = dd.load_config()
    store = dd.Store(cfg).start()
    DeparturesDisplay(cfg, store).run()
