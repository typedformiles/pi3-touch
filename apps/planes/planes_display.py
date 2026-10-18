#!/usr/bin/env python3
"""
Planes Overhead Display
The aircraft around home, fullscreen on the HyperPixel 4.0 (480x800 portrait).

Pages run along the bottom: Radar (a map of the sky around home - each aircraft pointing
the way it's flying, coloured by height, with the last few minutes of its track) and List
(every aircraft in range, nearest first). The card under the radar shows the selected
aircraft - the nearest, until you tap another: its flight, route, type, height, speed and
a photo. Tap the range buttons to zoom. Swipe left/right to change page too. Data is
fetched in the background (planes_data.Store); the dot top right is green when it's fresh.
"""

import io
import math
import os
import sys
import time
from datetime import datetime

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "..", "common"))
import planes_data as pd  # noqa: E402

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
TAP_NEAR = 34               # px: a tap this close to an aircraft selects it
LABELS = 8                  # label at most this many aircraft (nearest first) on the radar
OVERHEAD_NM = 1.5           # "Overhead now" within this distance...
OVERHEAD_FT = 10000         # ...and below this height
HOME_REQUEST = "/run/pi3-touch/request-home"   # Pi3 Touch launcher picks this up
FONT_DIR = os.environ.get("PI3_FONT_DIR", "/usr/share/fonts/truetype/dejavu")
PAGES = ["Radar", "List"]

# ── Colours ───────────────────────────────────────────────────────────────────
BG          = (10,  16,  26)
BG_CARD     = (22,  32,  48)
BG_CARD_HI  = (36,  52,  76)
TEXT_PRI    = (238, 242, 248)
TEXT_SEC    = (150, 165, 185)
TEXT_DIM    = (88,  102, 122)
ACCENT      = (90,  180, 240)   # sky blue
SKY         = (13,  24,  40)
RING        = (40,  60,  86)
LAND        = (110, 125, 145)
RUNWAY      = (150, 160, 175)
OK_GREEN    = (80,  200, 120)
STALE       = (230, 170, 60)
BAD_RED     = (225, 70,  80)
# Height bands, low to high (ft): the colour of an aircraft and its track
ALT_COLOURS = [(0, (250, 140, 60)), (2000, (250, 200, 70)), (5000, (140, 220, 110)),
               (10000, (80, 200, 220)), (20000, (110, 140, 250)), (30000, (190, 130, 250))]
GROUND = (130, 130, 130)

# ── Layout constants (portrait 480x800) ───────────────────────────────────────
PAD     = 16
RADAR_C = (240, 286)    # centre of the radar (home)
RADAR_R = 222
CARD_Y  = 518
TABS_Y  = 736
TABS_H  = SCREEN_H - TABS_Y
PHOTO_BOX = (150, 100)


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


def alt_colour(p):
    if p["ground"] or p["alt"] is None:
        return GROUND
    return [c for a, c in ALT_COLOURS if p["alt"] >= a][-1] if p["alt"] >= 0 else ALT_COLOURS[0][1]


def height(p):
    if p["ground"]:
        return "on the ground"
    return f"{p['alt']:,} ft" if p["alt"] is not None else "height ?"


class PlanesDisplay:
    def __init__(self, config, store, screen=None):
        self.config = config
        self.ranges = config.get("ranges_nm", [5, 10, 20])
        self.range_nm = config.get("range_nm", self.ranges[len(self.ranges) // 2])
        self.show_ground = config.get("show_ground", False)
        self.store = store

        if screen is None:
            self.display = pgscreen.Display(SCREEN_W, SCREEN_H, "Planes Overhead")
            screen = self.display.surface
        else:
            self.display = None
            pygame.font.init()
        self.screen = screen

        self.f_big   = load_font(30, bold=True)
        self.f_large = load_font(24, bold=True)
        self.f_med   = load_font(19)
        self.f_medb  = load_font(19, bold=True)
        self.f_small = load_font(16)
        self.f_smallb = load_font(16, bold=True)
        self.f_tiny  = load_font(13)
        self.f_tinyb = load_font(13, bold=True)
        self.f_clock = load_font(28, bold=True)

        self.page = "Radar"
        self.selected = None        # hex of the tapped aircraft (None: the nearest)
        self.last_touch = time.time()
        self.dimmed = False
        self.sleeping = False
        self.down = None            # where the current touch started
        self.buttons = {}           # name -> Rect, rebuilt every frame
        self.spots = {}             # hex -> screen position on the radar, rebuilt every frame
        self.photos = {}            # hex -> Surface (or None if it wouldn't load)
        self.drawn = None           # state() of the frame on screen

    # ── Helpers ───────────────────────────────────────────────────────────────

    def text(self, s, font, colour, pos, anchor="topleft"):
        surf = font.render(s, True, colour)
        rect = surf.get_rect(**{anchor: pos})
        self.screen.blit(surf, rect)
        return rect

    def card(self, rect, colour=BG_CARD, radius=14):
        pygame.draw.rect(self.screen, colour, rect, border_radius=radius)

    def planes(self):
        return pd.visible(self.store.planes(), self.range_nm, self.show_ground)

    def focus(self, planes):
        """The aircraft the card shows: the tapped one while it's in range, else the nearest airborne."""
        by = {p["hex"]: p for p in planes}
        if self.selected in by:
            return by[self.selected]
        airborne = [p for p in planes if not p["ground"]]
        return airborne[0] if airborne else None

    def xy(self, lat, lon):
        e, n = pd.offset(self.config["home"], lat, lon)
        k = RADAR_R / self.range_nm
        return RADAR_C[0] + e * k, RADAR_C[1] - n * k

    def inside(self, x, y, margin=0):
        return math.hypot(x - RADAR_C[0], y - RADAR_C[1]) <= RADAR_R - margin

    def photo(self, hex_):
        """The aircraft's photo as a Surface, once fetched and decoded (None otherwise)."""
        if hex_ not in self.photos:
            data = self.store.photo(hex_)
            if data is None:
                return None
            try:
                pic = pygame.image.load(io.BytesIO(data)) if data else None
                if pic:                         # scaled once, to fit the card
                    k = min(PHOTO_BOX[0] / pic.get_width(), PHOTO_BOX[1] / pic.get_height())
                    pic = pygame.transform.smoothscale(pic.convert(), (round(pic.get_width() * k),
                                                                       round(pic.get_height() * k)))
                self.photos[hex_] = pic
            except (pygame.error, ValueError):
                self.photos[hex_] = None
            if len(self.photos) > pd.PHOTOS_KEPT:
                self.photos.pop(next(iter(self.photos)))
        return self.photos[hex_]

    # ── Chrome: status bar, page tabs ────────────────────────────────────────

    def draw_status_bar(self, now):
        self.text(now.strftime("%H:%M"), self.f_clock, TEXT_PRI, (PAD, 12))
        t = self.store.fetched
        if t:
            self.text(f"updated {datetime.fromtimestamp(t):%H:%M:%S}", self.f_tiny, TEXT_DIM, (PAD + 92, 24))
        age = time.time() - t if t else None
        dot = BAD_RED if age is None else OK_GREEN if age < 90 and not self.store.error() else STALE
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

    def draw_tabs(self):
        pygame.draw.rect(self.screen, BG_CARD, (0, TABS_Y, SCREEN_W, TABS_H))
        w = SCREEN_W / len(PAGES)
        for i, p in enumerate(PAGES):
            r = pygame.Rect(round(i * w), TABS_Y, round(w), TABS_H)
            sel = p == self.page
            if sel:
                pygame.draw.rect(self.screen, ACCENT, (r.x + 14, TABS_Y, r.w - 28, 4), border_radius=2)
            label = p if p != "List" else f"List ({len(self.planes())})"
            self.text(label, self.f_medb if sel else self.f_med, ACCENT if sel else TEXT_SEC, r.center, "center")
            self.buttons[f"page:{p}"] = r

    # ── Page: Radar ───────────────────────────────────────────────────────────

    def draw_plane(self, x, y, track, colour, size=1.0, ring=False):
        """An aircraft symbol pointing along its track (degrees, 0 = north)."""
        a = math.radians(track or 0)
        fwd, side = (math.sin(a), -math.cos(a)), (math.cos(a), math.sin(a))

        def pt(f, s):
            return (x + (fwd[0] * f + side[0] * s) * size, y + (fwd[1] * f + side[1] * s) * size)

        body = [pt(11, 0), pt(7, 1.6), pt(2, 1.8), pt(-1, 10), pt(-3, 10), pt(-3, 1.8), pt(-8, 1.4),
                pt(-11, 5), pt(-12, 5), pt(-11, 0), pt(-12, -5), pt(-11, -5), pt(-8, -1.4), pt(-3, -1.8),
                pt(-3, -10), pt(-1, -10), pt(2, -1.8), pt(7, -1.6)]
        if ring:
            pygame.draw.circle(self.screen, TEXT_PRI, (round(x), round(y)), round(17 * size), 2)
        pygame.draw.polygon(self.screen, (0, 0, 0), [(px + 1, py + 1) for px, py in body])
        pygame.draw.polygon(self.screen, colour, body)

    def draw_map(self):
        cx, cy = RADAR_C
        pygame.draw.circle(self.screen, SKY, RADAR_C, RADAR_R)
        # Range rings at a half and the full range, with the compass round the edge
        for frac in (0.5, 1.0):
            r = round(RADAR_R * frac)
            pygame.draw.circle(self.screen, RING, RADAR_C, r, 1 if frac < 1 else 2)
            nm = self.range_nm * frac
            self.text(f"{nm:g} nm", self.f_tiny, TEXT_DIM, (cx + r * 0.71 + 4, cy + r * 0.71 - 16))
        for i, name in enumerate("NESW"):
            a = math.radians(i * 90)
            pos = (cx + math.sin(a) * (RADAR_R - 13), cy - math.cos(a) * (RADAR_R - 13))
            self.text(name, self.f_tinyb, TEXT_SEC, pos, "center")
        # Runways and places
        for rw in self.config.get("runways", []):
            (a1, o1), (a2, o2) = rw["ends"]
            p1, p2 = self.xy(a1, o1), self.xy(a2, o2)
            if self.inside(*p1, 4) and self.inside(*p2, 4):
                pygame.draw.line(self.screen, RUNWAY, p1, p2, 3)
                if rw.get("label") and rw.get("name"):
                    lx, ly = self.xy(*rw["label"])
                    if self.inside(lx, ly, 20):
                        self.text(rw["name"], self.f_tiny, LAND, (lx, ly), "midtop")
        for lm in self.config.get("landmarks", []):
            x, y = self.xy(lm["lat"], lm["lon"])
            if self.inside(x, y, 24):
                pygame.draw.circle(self.screen, LAND, (round(x), round(y)), 2)
                self.text(lm["name"], self.f_tiny, LAND, (x + 5, y - 1), "midleft")
        # Home
        pygame.draw.circle(self.screen, ACCENT, RADAR_C, 5)
        pygame.draw.circle(self.screen, ACCENT, RADAR_C, 10, 1)

    def draw_radar(self, now):
        self.draw_map()
        self.spots = {}
        planes = self.planes()
        focus = self.focus(planes)
        labelled = {p["hex"] for p in planes[:LABELS]} | ({focus["hex"]} if focus else set())
        # Tracks first, then aircraft, then labels on top - the focus last of all
        order = sorted(planes, key=lambda p: (focus is not None and p["hex"] == focus["hex"], -p["dist"]))
        for p in order:
            col = alt_colour(p)
            pts = [self.xy(la, lo) for _, la, lo in self.store.trail(p["hex"])]
            pts = [q for q in pts if self.inside(*q)]
            if len(pts) > 1:
                dim = tuple(c // 2 for c in col)
                pygame.draw.lines(self.screen, dim, False, pts, 2)
        for p in order:
            x, y = self.xy(p["lat"], p["lon"])
            if not self.inside(x, y, 6):
                continue
            is_focus = focus is not None and p["hex"] == focus["hex"]
            col = BAD_RED if p["emergency"] else alt_colour(p)
            self.draw_plane(x, y, p["track"], col, 1.25 if is_focus else 1.0, ring=is_focus)
            self.spots[p["hex"]] = (x, y)
        # Labels: the focus first, then nearest first; each goes right of its aircraft, or
        # left if that would cover another label, or is left out
        taken = []
        for p in reversed(order):
            if p["hex"] not in labelled or p["hex"] not in self.spots:
                continue
            x, y = self.spots[p["hex"]]
            name = p["callsign"] or p["reg"] or p["hex"]
            alt = "gnd" if p["ground"] else f"{round((p['alt'] or 0) / 100):d}"
            w = max(self.f_tinyb.size(name)[0], self.f_tiny.size(alt)[0])
            for box in (pygame.Rect(x + 14, y - 15, w, 30), pygame.Rect(x - 14 - w, y - 15, w, 30)):
                if not any(box.colliderect(t) for t in taken):
                    taken.append(box)
                    col = TEXT_PRI if focus and p["hex"] == focus["hex"] else TEXT_SEC
                    r = self.text(name, self.f_tinyb, col, box.topleft)
                    self.text(alt, self.f_tiny, alt_colour(p), (r.x, r.bottom - 1))
                    break

        # Range buttons, bottom right of the radar square
        bw, bh = 46, 34
        x0 = SCREEN_W - PAD - len(self.ranges) * (bw + 4) + 4
        y0 = RADAR_C[1] + RADAR_R - bh + 6
        for i, nm in enumerate(self.ranges):
            r = pygame.Rect(x0 + i * (bw + 4), y0, bw, bh)
            sel = nm == self.range_nm
            self.card(r, ACCENT if sel else BG_CARD_HI, radius=8)
            self.text(f"{nm:g}", self.f_smallb, (10, 20, 34) if sel else TEXT_SEC, r.center, "center")
            self.buttons[f"range:{nm}"] = r
        if not self.store.fetched:
            err = self.store.error()
            self.text("Looking for aircraft…" if not err else f"({err} - retrying)", self.f_small, TEXT_SEC,
                      (RADAR_C[0], RADAR_C[1] + 40), "center")
        self.draw_card(focus)

    def draw_card(self, p):
        r = pygame.Rect(PAD, CARD_Y, SCREEN_W - 2 * PAD, TABS_Y - CARD_Y - 10)
        self.card(r)
        if not p:
            msg = "Nothing in the air within" if self.store.fetched else "Waiting for the first fetch"
            self.text(f"{msg} {self.range_nm:g} nm" if self.store.fetched else msg, self.f_med, TEXT_SEC,
                      r.center, "center")
            return
        self.store.want_photo = p["hex"]
        route = self.store.route(p["callsign"]) if p["callsign"] else {}
        info = self.store.aircraft(p["hex"]) or {}
        airline = (route or {}).get("airline", {}).get("name") or info.get("registered_owner") or ""

        # Photo on the right, if there is one
        text_right = r.right - 14
        img = self.photo(p["hex"])
        if img:
            box = pygame.Rect(0, 0, *PHOTO_BOX)
            box.topright = (r.right - 12, r.y + 12)
            ir = img.get_rect(center=box.center)
            self.screen.blit(img, ir)
            text_right = ir.x - 10

        w = text_right - r.x - 14
        name = p["callsign"] or p["reg"] or p["hex"].upper()
        nr = self.text(truncate(name, self.f_big, w), self.f_big, alt_colour(p), (r.x + 14, r.y + 10))
        overhead = p["dist"] <= OVERHEAD_NM and not p["ground"] and (p["alt"] or 0) < OVERHEAD_FT
        tag = p["emergency"] or ("Overhead now" if overhead else "")
        if tag:
            self.text(tag, self.f_tinyb, BAD_RED if p["emergency"] else STALE, (nr.right + 10, nr.y + 4))
        self.text(truncate(airline, self.f_small, w), self.f_small, TEXT_SEC, (r.x + 14, r.y + 46))
        if route:
            self.text(truncate(pd.place(route.get("origin")), self.f_medb, w), self.f_medb, TEXT_PRI, (r.x + 14, r.y + 70))
            self.text(truncate(f"→ {pd.place(route.get('destination'))}", self.f_medb, w), self.f_medb, TEXT_PRI,
                      (r.x + 14, r.y + 94))
        else:
            looked_up = p["callsign"] and route is not None
            self.text("Route not known" if looked_up else "Looking up route…" if p["callsign"] else "No callsign",
                      self.f_med, TEXT_DIM, (r.x + 14, r.y + 80))
        kind = pd.nice_type(p["desc"], p["type"])
        reg = p["reg"]
        self.text(truncate(" · ".join(x for x in (kind, reg) if x), self.f_small, r.w - 28), self.f_small, TEXT_SEC,
                  (r.x + 14, r.y + 124))
        # Height, speed, where
        y = r.y + 152
        arrow = {"up": " ↑", "down": " ↓"}.get(pd.climb(p["rate"]), "")
        cols = [(height(p) + arrow, "height"), (f"{round(p['speed'])} kt" if p["speed"] is not None else "–", "speed"),
                (f"{p['dist']:.1f} nm {pd.compass(p['bearing'])}", "away")]
        cw = (r.w - 28) / 3
        for i, (v, k) in enumerate(cols):
            x = r.x + 14 + i * cw
            self.text(truncate(v, self.f_medb, cw - 6), self.f_medb, TEXT_PRI, (x, y))
            self.text(k, self.f_tiny, TEXT_DIM, (x, y + 24))

    # ── Page: List ────────────────────────────────────────────────────────────

    def draw_list(self, now):
        planes = self.planes()
        y = 62
        self.text(f"{len(planes)} aircraft within {self.range_nm:g} nm", self.f_medb, TEXT_PRI, (PAD, y))
        if self.store.source:
            self.text(f"via {self.store.source}", self.f_tiny, TEXT_DIM, (SCREEN_W - PAD, y + 4), "topright")
        y += 34
        row_h = 54
        focus = self.focus(planes)
        for i, p in enumerate(planes):
            if y + row_h > TABS_Y - 4:
                self.text(f"+ {len(planes) - i} more", self.f_small, TEXT_DIM, (SCREEN_W // 2, y + 6), "midtop")
                break
            rr = pygame.Rect(PAD - 6, y, SCREEN_W - 2 * PAD + 12, row_h - 4)
            if focus and p["hex"] == focus["hex"]:
                self.card(rr, BG_CARD_HI, radius=10)
            elif i % 2 == 0:
                self.card(rr, BG_CARD, radius=10)
            col = BAD_RED if p["emergency"] else alt_colour(p)
            self.draw_plane(PAD + 12, rr.centery, p["track"], col, 0.9)
            route = self.store.route(p["callsign"]) if p["callsign"] else None
            sub = ""
            if route:
                o, d = route.get("origin") or {}, route.get("destination") or {}
                sub = f"{o.get('iata_code') or o.get('icao_code') or '?'} → {d.get('iata_code') or d.get('icao_code') or '?'}"
            kind = pd.nice_type(p["desc"], p["type"])
            self.text(p["callsign"] or p["reg"] or p["hex"].upper(), self.f_medb, TEXT_PRI, (PAD + 32, rr.y + 5))
            self.text(truncate(" · ".join(x for x in (sub, kind) if x), self.f_tiny, 230), self.f_tiny, TEXT_SEC,
                      (PAD + 32, rr.y + 29))
            arrow = {"up": "↑", "down": "↓"}.get(pd.climb(p["rate"]), "")
            self.text(("gnd" if p["ground"] else f"{p['alt'] or 0:,} ft") + arrow, self.f_smallb, col,
                      (SCREEN_W - PAD - 8, rr.y + 6), "topright")
            self.text(f"{p['dist']:.1f} nm {pd.compass(p['bearing'])}", self.f_tiny, TEXT_SEC,
                      (SCREEN_W - PAD - 8, rr.y + 29), "topright")
            self.buttons[f"plane:{p['hex']}"] = rr
            y += row_h
        if not planes:
            self.text("Nothing in range just now" if self.store.fetched else "Looking for aircraft…", self.f_med,
                      TEXT_SEC, (SCREEN_W // 2, 380), "center")

    # ── Drawing a frame ───────────────────────────────────────────────────────

    def draw(self, now=None):
        now = now or datetime.now()
        self.buttons = {}
        self.screen.fill(BG)
        self.draw_status_bar(now)
        {"Radar": self.draw_radar, "List": self.draw_list}[self.page](now)
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

    def turn_page(self, step):
        self.page = PAGES[(PAGES.index(self.page) + step) % len(PAGES)]

    def touch_down(self, pos):
        self.down = pos

    def touch_up(self, pos):
        """A touch ended: a sideways swipe turns the page, anything else is a tap where it started."""
        start, self.down = self.down or pos, None
        dx, dy = pos[0] - start[0], pos[1] - start[1]
        if abs(dx) > SWIPE and abs(dx) > abs(dy) and not (self.sleeping or self.dimmed):
            self.last_touch = time.time()
            self.turn_page(-1 if dx > 0 else 1)
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
        elif hit and hit.startswith("range:"):
            self.range_nm = float(hit[6:]) if "." in hit[6:] else int(hit[6:])
        elif hit and hit.startswith("page:"):
            self.page = hit[5:]
        elif hit and hit.startswith("plane:"):
            self.selected, self.page = hit[6:], "Radar"
        elif self.page == "Radar" and self.spots:
            near = min(self.spots.items(), key=lambda kv: math.hypot(kv[1][0] - pos[0], kv[1][1] - pos[1]))
            if math.hypot(near[1][0] - pos[0], near[1][1] - pos[1]) <= TAP_NEAR:
                self.selected = near[0]
            elif math.hypot(pos[0] - RADAR_C[0], pos[1] - RADAR_C[1]) <= RADAR_R:
                self.selected = None                    # tap empty sky: back to the nearest

    def tick(self):
        """Idle bookkeeping: dimming, and fetching less often while dimmed."""
        self.dimmed = time.time() - self.last_touch > DIM_AFTER
        self.store.idle = self.dimmed or self.sleeping

    # ── Main loop ─────────────────────────────────────────────────────────────

    def state(self, now):
        """Everything on screen depends on: what's selected, dimming, the minute and the data."""
        return (self.page, self.range_nm, self.selected, self.dimmed, self.sleeping, now.strftime("%H:%M"),
                self.store.stamp())

    def frame(self, now=None):
        """Redraw only if something on screen would change. Returns True if it drew."""
        now = now or datetime.now()
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
    cfg = pd.load_config()
    store = pd.Store(cfg).start()
    PlanesDisplay(cfg, store).run()
