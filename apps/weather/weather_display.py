#!/usr/bin/env python3
"""
Weather Display
Conditions, wind and - at the coast - tides, fullscreen on the HyperPixel 4.0 (480x800 portrait).

Locations (locations.json) run along the top: tap one to switch. Pages run along the bottom:
Now, Wind (next 12 hours, for sailing), Week, and Tides where the location has a tide station.
Swipe left/right to change page too. Data is fetched and cached in the background
(weather_data.Store); the dot top right is green when it's fresh.
"""

import math
import os
import sys
import time
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, HERE)
import weather_data as wd  # noqa: E402  (also puts common/ on the path)

# Use KMS/DRM driver on headless Pi (set before pygame.init)
if "DISPLAY" not in os.environ and "WAYLAND_DISPLAY" not in os.environ:
    os.environ.setdefault("SDL_VIDEODRIVER", "kmsdrm")
    os.environ.setdefault("SDL_KMSDRM_DEVICE_INDEX", "0")
os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
import pygame  # noqa: E402
import pgscreen  # noqa: E402
import weather_icons  # noqa: E402

# ── Config ────────────────────────────────────────────────────────────────────
SCREEN_W, SCREEN_H = 480, 800
POLL = 0.05                 # seconds between checks for touches, new data and the time
DIM_AFTER = 300             # seconds of inactivity before dimming (5 min)
DIM_BRIGHTNESS = 120        # 0-255 (higher = brighter when dimmed)
CYCLE_IDLE = 60             # with cycle_seconds set: start cycling pages after this idle time
SWIPE = 80                  # px of sideways travel that counts as a swipe
HOME_REQUEST = "/run/pi3-touch/request-home"   # Pi3 Touch launcher picks this up
FONT_DIR = os.environ.get("PI3_FONT_DIR", "/usr/share/fonts/truetype/dejavu")

# ── Colours ───────────────────────────────────────────────────────────────────
BG          = (10,  16,  26)
BG_CARD     = (22,  32,  48)
BG_CARD_HI  = (32,  46,  68)
TEXT_PRI    = (238, 242, 248)
TEXT_SEC    = (150, 165, 185)
TEXT_DIM    = (88,  102, 122)
ACCENT      = (72,  164, 230)   # sea blue
ACCENT_DIM  = (30,  64,  98)
GRID        = (36,  50,  70)
RAIN        = (96,  170, 240)
OK_GREEN    = (80,  200, 120)
STALE       = (230, 170, 60)
BAD_RED     = (200, 70,  70)

# Wind colours by knots, for a dinghy on a lake: drifting, sailing, lively, hard work, stay ashore
WIND_BANDS = [(5, (130, 150, 175)), (12, (80, 200, 120)), (18, (232, 200, 64)),
              (25, (240, 140, 50)), (999, (225, 70, 70))]

# ── Layout constants (portrait 480x800) ───────────────────────────────────────
PAD     = 16
LOC_Y   = 58            # location buttons
LOC_H   = 50
BODY_Y  = 124           # page content
TABS_Y  = 736           # page tabs
TABS_H  = SCREEN_H - TABS_Y


def wind_colour(knots):
    return next(c for limit, c in WIND_BANDS if (knots or 0) < limit)


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


def until(delta):
    """"1h 56m" / "12m" for a timedelta."""
    mins = max(0, int(delta.total_seconds() // 60))
    return f"{mins // 60}h {mins % 60:02d}m" if mins >= 60 else f"{mins}m"


def day_label(day, now):
    if day == now.date():
        return "Today"
    if day == now.date() + timedelta(days=1):
        return "Tomorrow"
    return f"{day:%a} {day.day}"


class WeatherDisplay:
    def __init__(self, config, store, screen=None):
        self.locations = config["locations"]
        self.cycle_seconds = config.get("cycle_seconds", 0)
        self.store = store

        if screen is None:
            self.display = pgscreen.Display(SCREEN_W, SCREEN_H, "Weather")
            screen = self.display.surface
        else:
            self.display = None
            pygame.font.init()
        self.screen = screen

        self.f_huge  = load_font(84, bold=True)
        self.f_big   = load_font(40, bold=True)
        self.f_large = load_font(30, bold=True)
        self.f_med   = load_font(22)
        self.f_medb  = load_font(22, bold=True)
        self.f_small = load_font(18)
        self.f_tiny  = load_font(14)
        self.f_loc   = load_font(19, bold=True)
        self.f_clock = load_font(28, bold=True)

        self.loc_i = 0
        self.page = "Now"
        self.tide_day = 0           # days ahead on the Tides page
        self.last_touch = time.time()
        self.last_cycle = time.time()
        self.dimmed = False
        self.sleeping = False
        self.down = None            # where the current touch started
        self.buttons = {}           # name -> Rect, rebuilt every frame
        self.cache = {}             # (location, kind, fetched) -> parsed data
        self.drawn = None           # state() of the frame on screen

    # ── Helpers ───────────────────────────────────────────────────────────────

    @property
    def loc(self):
        return self.locations[self.loc_i]

    def pages(self, loc=None):
        loc = loc or self.loc
        return ["Now", "Wind", "Week"] + (["Tides"] if loc.get("tide_station") else [])

    def text(self, s, font, colour, pos, anchor="topleft"):
        surf = font.render(s, True, colour)
        rect = surf.get_rect(**{anchor: pos})
        self.screen.blit(surf, rect)
        return rect

    def card(self, rect, colour=BG_CARD, radius=14):
        pygame.draw.rect(self.screen, colour, rect, border_radius=radius)

    def parsed(self, kind, parse):
        """The current location's data, parsed once per fetch."""
        entry = self.store.get(self.loc, kind)
        if not entry:
            return None
        key = (self.loc["name"], kind, entry["fetched"])
        if key not in self.cache:
            try:
                self.cache[key] = parse(entry)
            except (KeyError, TypeError, ValueError) as e:
                print(f"[weather] bad {kind} data for {self.loc['name']}: {e}", flush=True)
                self.cache[key] = None
            self.cache = {k: v for k, v in self.cache.items() if k[:2] != key[:2] or k == key}
        return self.cache[key]

    def weather(self):
        return self.parsed("weather", lambda e: wd.parse_weather(e["data"]))

    def tides(self):
        return self.parsed("tides", lambda e: (wd.parse_tides(e["events"]), e["station"])) or (None, None)

    # ── Icons ─────────────────────────────────────────────────────────────────

    def draw_arrow(self, cx, cy, length, bearing, colour, width=4):
        """Arrow centred on (cx, cy) pointing towards bearing (degrees, 0 = up/north)."""
        dx, dy = math.sin(math.radians(bearing)), -math.cos(math.radians(bearing))
        tip = (cx + dx * length / 2, cy + dy * length / 2)
        tail = (cx - dx * length / 2, cy - dy * length / 2)
        head = length * 0.38
        base = (tip[0] - dx * head, tip[1] - dy * head)
        px, py = -dy * head * 0.55, dx * head * 0.55
        pygame.draw.line(self.screen, colour, tail, base, width)
        pygame.draw.polygon(self.screen, colour, [tip, (base[0] + px, base[1] + py), (base[0] - px, base[1] - py)])

    def draw_wind_arrow(self, cx, cy, length, from_deg, colour, width=4):
        """Wind arrows point the way the wind blows (from_deg + 180), as on weather maps."""
        self.draw_arrow(cx, cy, length, (from_deg or 0) + 180, colour, width)

    def draw_icon(self, code, is_day, cx, cy, s, bg=BG_CARD):
        weather_icons.draw_icon(self.screen, code, is_day, cx, cy, s, bg)

    # ── Chrome: status bar, locations, page tabs ─────────────────────────────

    def draw_status_bar(self, now):
        self.text(now.strftime("%H:%M"), self.f_clock, TEXT_PRI, (PAD, 12))

        entry = self.store.get(self.loc, "weather")
        age = time.time() - entry["fetched"] if entry else None
        if age is not None:
            self.text(f"updated {datetime.fromtimestamp(entry['fetched']):%H:%M}", self.f_tiny, TEXT_DIM, (PAD + 92, 24))
        dot = OK_GREEN if age is not None and age < 3600 else STALE if age is not None else BAD_RED
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

    def draw_locations(self):
        n = len(self.locations)
        gap = 8
        w = (SCREEN_W - 2 * PAD - gap * (n - 1)) / n
        for i, loc in enumerate(self.locations):
            r = pygame.Rect(round(PAD + i * (w + gap)), LOC_Y, round(w), LOC_H)
            sel = i == self.loc_i
            self.card(r, ACCENT if sel else BG_CARD, radius=12)
            label = truncate(loc["name"], self.f_loc, r.w - 10)
            self.text(label, self.f_loc, (8, 20, 34) if sel else TEXT_SEC, r.center, "center")
            self.buttons[f"loc{i}"] = r

    def draw_tabs(self):
        pages = self.pages()
        pygame.draw.rect(self.screen, BG_CARD, (0, TABS_Y, SCREEN_W, TABS_H))
        w = SCREEN_W / len(pages)
        for i, p in enumerate(pages):
            r = pygame.Rect(round(i * w), TABS_Y, round(w), TABS_H)
            sel = p == self.page
            if sel:
                pygame.draw.rect(self.screen, ACCENT, (r.x + 14, TABS_Y, r.w - 28, 4), border_radius=2)
            self.text(p, self.f_medb if sel else self.f_med, ACCENT if sel else TEXT_SEC, r.center, "center")
            self.buttons[f"page:{p}"] = r

    def draw_message(self, line1, line2=""):
        cy = (BODY_Y + TABS_Y) // 2
        self.text(line1, self.f_med, TEXT_SEC, (SCREEN_W // 2, cy - 16), "center")
        if line2:
            self.text(line2, self.f_small, TEXT_DIM, (SCREEN_W // 2, cy + 18), "center")

    def draw_waiting(self, kind="weather"):
        err = self.store.error(self.loc, kind)
        self.draw_message(f"Fetching {kind} for {self.loc['name']}…", f"({err} - retrying)" if err else "")

    # ── Page: Now ─────────────────────────────────────────────────────────────

    def draw_now(self, now):
        w = self.weather()
        if not w:
            return self.draw_waiting()
        cur, day = w["current"], wd.today(w, now)

        # Conditions card
        r = pygame.Rect(PAD, BODY_Y, SCREEN_W - 2 * PAD, 196)
        self.card(r)
        self.draw_icon(cur["weather_code"], cur["is_day"], r.x + 92, r.y + 98, 130)
        x = r.x + 190
        self.text(f"{round(cur['temperature_2m'])}°", self.f_huge, TEXT_PRI, (x - 4, r.y + 8))
        self.text(truncate(wd.describe(cur["weather_code"])[0], self.f_medb, r.right - x - 12),
                  self.f_medb, TEXT_PRI, (x, r.y + 110))
        line = f"Feels {round(cur['apparent_temperature'])}°"
        if day:
            line += f" · {round(day['temperature_2m_max'])}° / {round(day['temperature_2m_min'])}°"
        self.text(line, self.f_small, TEXT_SEC, (x, r.y + 144))

        # Wind card: compass dial + numbers
        r = pygame.Rect(PAD, BODY_Y + 210, SCREEN_W - 2 * PAD, 250)
        self.card(r)
        kn, gust, frm = cur["wind_speed_10m"], cur["wind_gusts_10m"], cur["wind_direction_10m"]
        col = wind_colour(kn)
        cx, cy, rad = r.x + 116, r.centery, 96
        pygame.draw.circle(self.screen, GRID, (cx, cy), rad, 3)
        for i in range(16):
            a = math.radians(i * 22.5)
            inner = rad - (14 if i % 4 == 0 else 8)
            pygame.draw.line(self.screen, TEXT_DIM, (cx + math.sin(a) * inner, cy - math.cos(a) * inner),
                             (cx + math.sin(a) * (rad - 2), cy - math.cos(a) * (rad - 2)), 2)
        for label, a in (("N", 0), ("E", 90), ("S", 180), ("W", 270)):
            rr = rad - 30
            self.text(label, self.f_small, TEXT_SEC,
                      (cx + math.sin(math.radians(a)) * rr, cy - math.cos(math.radians(a)) * rr), "center")
        self.draw_wind_arrow(cx, cy, rad * 1.1, frm, col, 7)

        x = r.x + 238
        sr = self.text(f"{round(kn)}", self.f_huge, col, (x, r.y + 14))
        self.text("kn", self.f_large, col, (sr.right + 6, sr.bottom - 52))
        self.text(f"Gusts {round(gust)} kn", self.f_medb, TEXT_PRI, (x, r.y + 120))
        force, name = wd.beaufort(kn)
        self.text(f"From {wd.compass(frm)} · F{force}", self.f_med, TEXT_SEC, (x, r.y + 156))
        self.text(name, self.f_med, TEXT_SEC, (x, r.y + 186))

        # Footer card: tide summary at the coast, else rain in the next few hours; sun times
        r = pygame.Rect(PAD, BODY_Y + 474, SCREEN_W - 2 * PAD, TABS_Y - BODY_Y - 474 - 14)
        self.card(r)
        y1, y2 = r.y + 18, r.y + 58
        events, _ = self.tides() if self.loc.get("tide_station") else (None, None)
        state = wd.tide_state(events, now) if events else None
        if state:
            nxt = wd.next_event(events, now)
            self.draw_arrow(r.x + 26, y1 + 13, 24, 45 if state == "rising" else 135, ACCENT, 4)
            self.text(f"Tide {state}", self.f_medb, TEXT_PRI, (r.x + 48, y1))
            self.text(f"{nxt['kind'].title()} {nxt['time'].astimezone(wd.LOCAL):%H:%M} · {until(nxt['time'] - now)}",
                      self.f_med, TEXT_SEC, (r.right - 14, y1), "topright")
        else:
            ahead = wd.hours_ahead(w, now, 3)
            pct = max((h["precipitation_probability"] or 0 for h in ahead), default=0)
            self.text("Rain next 3 h", self.f_med, TEXT_SEC, (r.x + 14, y1))
            self.text(f"{pct}%", self.f_medb, RAIN if pct >= 30 else TEXT_PRI, (r.right - 14, y1), "topright")
        if day:
            self.text(f"Sunrise {day['sunrise']:%H:%M}", self.f_med, TEXT_SEC, (r.x + 14, y2))
            self.text(f"Sunset {day['sunset']:%H:%M}", self.f_med, TEXT_SEC, (r.right - 14, y2), "topright")

    # ── Page: Wind (next 12 hours) ────────────────────────────────────────────

    def draw_wind(self, now):
        w = self.weather()
        if not w:
            return self.draw_waiting()
        hours = wd.hours_ahead(w, now, 12)
        self.text("Wind · next 12 hours", self.f_medb, TEXT_PRI, (PAD, BODY_Y))
        self.text("knots · bar to the gust", self.f_tiny, TEXT_DIM, (SCREEN_W - PAD, BODY_Y + 6), "topright")
        top = max(25, 5 * math.ceil(max((h["wind_gusts_10m"] or 0 for h in hours), default=0) / 5))
        bx0, bx1 = 196, SCREEN_W - PAD - 82
        row_h = 48
        for i, h in enumerate(hours):
            y = BODY_Y + 40 + i * row_h
            cy = y + row_h // 2
            if i % 2 == 0:
                self.card(pygame.Rect(PAD - 6, y, SCREEN_W - 2 * PAD + 12, row_h), BG_CARD, radius=8)
            kn, gust = h["wind_speed_10m"] or 0, h["wind_gusts_10m"] or 0
            col = wind_colour(kn)
            self.text("Now" if i == 0 else f"{h['time']:%H:%M}", self.f_medb if i == 0 else self.f_med,
                      TEXT_PRI, (PAD, cy), "midleft")
            self.draw_wind_arrow(108, cy, 30, h["wind_direction_10m"], col, 4)
            self.text(wd.compass(h["wind_direction_10m"]), self.f_small, TEXT_SEC, (128, cy), "midleft")
            span = bx1 - bx0
            gw = round(span * min(gust, top) / top)
            sw = round(span * min(kn, top) / top)
            pygame.draw.rect(self.screen, GRID, (bx0, cy - 7, span, 14), border_radius=7)
            pygame.draw.rect(self.screen, tuple(c // 2 for c in wind_colour(gust)), (bx0, cy - 7, max(gw, 14), 14),
                             border_radius=7)
            pygame.draw.rect(self.screen, col, (bx0, cy - 7, max(sw, 14), 14), border_radius=7)
            nr = self.text(f"{round(kn)}", self.f_medb, col, (SCREEN_W - PAD - 40, cy), "midright")
            self.text(f"g{round(gust)}", self.f_small, TEXT_SEC, (nr.right + 4, cy + 1), "midleft")

    # ── Page: Week ────────────────────────────────────────────────────────────

    def draw_week(self, now):
        w = self.weather()
        if not w:
            return self.draw_waiting()
        self.text("Next 7 days", self.f_medb, TEXT_PRI, (PAD, BODY_Y))
        self.text("°C · wind kn · rain", self.f_tiny, TEXT_DIM, (SCREEN_W - PAD, BODY_Y + 6), "topright")
        row_h = 80
        for i, d in enumerate(w["days"][:7]):
            y = BODY_Y + 38 + i * row_h
            cy = y + row_h // 2
            if i % 2 == 0:
                self.card(pygame.Rect(PAD - 6, y, SCREEN_W - 2 * PAD + 12, row_h), BG_CARD, radius=8)
            self.text(day_label(d["date"], now), self.f_medb if i == 0 else self.f_med, TEXT_PRI, (PAD, cy), "midleft")
            self.draw_icon(d["weather_code"], True, 158, cy, 54)
            hi = self.text(f"{round(d['temperature_2m_max'])}°", self.f_medb, TEXT_PRI, (196, cy), "midleft")
            self.text(f"{round(d['temperature_2m_min'])}°", self.f_med, TEXT_SEC, (hi.right + 8, cy), "midleft")
            kn = d["wind_speed_10m_max"] or 0
            col = wind_colour(kn)
            self.draw_wind_arrow(304, cy, 26, d["wind_direction_10m_dominant"], col, 4)
            kr = self.text(f"{round(kn)}", self.f_medb, col, (322, cy), "midleft")
            self.text(f"g{round(d['wind_gusts_10m_max'] or 0)}", self.f_small, TEXT_SEC, (kr.right + 3, cy + 1), "midleft")
            pct = d["precipitation_probability_max"] or 0
            self.text(f"{pct}%", self.f_med, RAIN if pct >= 30 else TEXT_DIM, (SCREEN_W - PAD, cy), "midright")

    # ── Page: Tides ───────────────────────────────────────────────────────────

    def draw_tides(self, now):
        events, station = self.tides()
        if not events:
            if self.store.error(self.loc, "tides") == "no tide key":
                return self.draw_message("Tide times need an ADMIRALTY key", "On the Mac: bash mac/set-tide-key.sh")
            return self.draw_waiting("tides")
        pts = wd.curve_points(events)
        y = BODY_Y

        # Rising / falling + height now
        state = wd.tide_state(events, now)
        h_now = wd.height_at(pts, now)
        pygame.draw.circle(self.screen, ACCENT, (PAD + 32, y + 34), 32)
        if state:
            self.draw_arrow(PAD + 32, y + 34, 34, 45 if state == "rising" else 135, BG, 6)
        self.text(f"Tide at {self.loc['name']} is", self.f_small, TEXT_SEC, (PAD + 80, y + 4))
        self.text((state or "unknown").title(), self.f_big, ACCENT, (PAD + 78, y + 24))
        if h_now is not None:
            self.text(f"{h_now:.1f} m", self.f_large, TEXT_PRI, (SCREEN_W - PAD, y + 30), "topright")
            self.text("now", self.f_tiny, TEXT_DIM, (SCREEN_W - PAD, y + 12), "topright")

        # Next high / low, soonest first
        y += 82
        nexts = sorted(filter(None, (wd.next_event(events, now, k) for k in ("low", "high"))), key=lambda e: e["time"])
        bw = (SCREEN_W - 2 * PAD - 10) // 2
        for i, e in enumerate(nexts[:2]):
            r = pygame.Rect(PAD + i * (bw + 10), y, bw, 74)
            self.card(r)
            self.text(f"Next {e['kind']}", self.f_small, TEXT_SEC, (r.x + 12, r.y + 8))
            if e["height"] is not None:
                self.text(f"{e['height']:.1f} m", self.f_small, TEXT_SEC, (r.right - 12, r.y + 8), "topright")
            self.text(until(e["time"] - now), self.f_large, TEXT_PRI, (r.x + 12, r.y + 32))
            self.text(f"{e['time'].astimezone(wd.LOCAL):%H:%M}", self.f_med, TEXT_SEC, (r.right - 12, r.y + 38), "topright")

        # Day picker
        y += 88
        days = sorted({e["time"].astimezone(wd.LOCAL).date() for e in events if e["time"].astimezone(wd.LOCAL).date() >= now.date()})
        self.tide_day = max(0, min(self.tide_day, len(days) - 1))
        day = days[self.tide_day] if days else now.date()
        for name, x, ok in (("tide-prev", PAD, self.tide_day > 0), ("tide-next", SCREEN_W - PAD - 56, self.tide_day < len(days) - 1)):
            r = pygame.Rect(x, y, 56, 40)
            self.card(r, BG_CARD if ok else BG, radius=10)
            if ok:
                self.draw_arrow(r.centerx, r.centery, 22, 270 if name == "tide-prev" else 90, TEXT_PRI, 4)
                self.buttons[name] = r
        self.text(f"{day_label(day, now)} · {day:%a %-d %b}" if self.tide_day < 2 else f"{day:%A %-d %B}",
                  self.f_medb, TEXT_PRI, (SCREEN_W // 2, y + 20), "center")

        # Curve for the day, on a scale fixed across the week
        y += 52
        heights = [e["height"] for e in events if e["height"] is not None]
        lo, hi = math.floor(min(heights)), math.ceil(max(heights))
        if hi - lo < 2:
            hi = lo + 2
        step = 1 if hi - lo <= 8 else 2
        chart = pygame.Rect(54, y + 8, SCREEN_W - PAD - 54, 196)
        start = datetime(day.year, day.month, day.day, tzinfo=wd.LOCAL)
        span = ((start + timedelta(days=1)).astimezone(wd.UTC) - start.astimezone(wd.UTC)).total_seconds()

        def px(t):
            return chart.x + chart.w * (t - start).total_seconds() / span

        def py(h):
            return chart.bottom - chart.h * (h - lo) / (hi - lo)

        for m in range(lo, hi + 1, step):
            yy = py(m)
            pygame.draw.line(self.screen, GRID, (chart.x, yy), (chart.right, yy), 1)
            self.text(f"{m}m", self.f_tiny, TEXT_DIM, (chart.x - 8, yy), "midright")
        for hr in range(0, 25, 4):
            xx = chart.x + chart.w * hr / 24
            pygame.draw.line(self.screen, GRID, (xx, chart.y), (xx, chart.bottom), 1)
            self.text(f"{hr % 24:02d}:00" if hr < 24 else "", self.f_tiny, TEXT_DIM, (xx, chart.bottom + 6), "midtop")
        curve = []
        for i in range(0, 24 * 6 + 1):                  # every 10 minutes
            t = start + timedelta(minutes=10 * i)
            h = wd.height_at(pts, t)
            if h is not None:
                curve.append((px(t), py(h)))
        if len(curve) > 1:
            pygame.draw.polygon(self.screen, ACCENT_DIM, curve + [(curve[-1][0], chart.bottom), (curve[0][0], chart.bottom)])
            pygame.draw.lines(self.screen, ACCENT, False, curve, 3)
        if day == now.date() and h_now is not None:
            p = (round(px(now)), round(py(h_now)))
            pygame.draw.circle(self.screen, TEXT_PRI, p, 10)
            pygame.draw.circle(self.screen, ACCENT, p, 6)
        self.text("© Crown copyright, UKHO", self.f_tiny, TEXT_DIM, (chart.right, chart.y - 2), "bottomright")

        # The day's highs and lows
        y = chart.bottom + 30
        todays = wd.day_events(events, day)
        row_h = min(34, (TABS_Y - 6 - y) // max(1, len(todays)))
        for i, e in enumerate(todays):
            past = e["time"] <= now
            col = TEXT_DIM if past else TEXT_PRI
            cy = y + i * row_h + row_h // 2
            self.text(e["kind"].title(), self.f_medb, col, (PAD, cy), "midleft")
            self.text(f"{e['time'].astimezone(wd.LOCAL):%H:%M}", self.f_med, col, (SCREEN_W // 2, cy), "center")
            ht = f"{e['height']:.1f} m" if e["height"] is not None else "–"
            self.text(ht, self.f_med, col, (SCREEN_W - PAD, cy), "midright")

    # ── Drawing a frame ───────────────────────────────────────────────────────

    def draw(self, now=None):
        now = now or datetime.now(wd.LOCAL)
        self.buttons = {}
        self.screen.fill(BG)
        self.draw_status_bar(now)
        self.draw_locations()
        if self.page not in self.pages():
            self.page = "Now"
        {"Now": self.draw_now, "Wind": self.draw_wind, "Week": self.draw_week,
         "Tides": self.draw_tides}[self.page](now)
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
        pages = self.pages()
        i = pages.index(self.page) if self.page in pages else 0
        self.page = pages[(i + step) % len(pages)]

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
        elif hit and hit.startswith("loc"):
            i = int(hit[3:])
            if i != self.loc_i:
                self.loc_i, self.tide_day = i, 0
                if self.page not in self.pages():
                    self.page = "Now"
        elif hit and hit.startswith("page:"):
            self.page = hit[5:]
        elif hit == "tide-prev":
            self.tide_day -= 1
        elif hit == "tide-next":
            self.tide_day += 1

    def tick(self):
        """Idle bookkeeping: dimming, and cycling pages if cycle_seconds is set."""
        idle = time.time() - self.last_touch
        self.dimmed = idle > DIM_AFTER
        if self.cycle_seconds and idle > CYCLE_IDLE and time.time() - self.last_cycle > self.cycle_seconds:
            self.last_cycle = time.time()
            self.tide_day = 0
            self.turn_page(1)

    # ── Main loop ─────────────────────────────────────────────────────────────

    def state(self, now):
        """Everything on screen depends on: what's selected, dimming, the minute and the data."""
        data = tuple((self.store.get(self.loc, k) or {}).get("fetched") for k in ("weather", "tides"))
        errors = tuple(self.store.error(self.loc, k) for k in ("weather", "tides"))
        return (self.loc_i, self.page, self.tide_day, self.dimmed, self.sleeping,
                now.strftime("%Y-%m-%d %H:%M"), data, errors)

    def frame(self, now=None):
        """Redraw only if something on screen would change (a Pi 3 at 10 FPS runs warm).
        Returns True if it drew."""
        now = now or datetime.now(wd.LOCAL)
        self.tick()
        if self.state(now) == self.drawn:
            return False
        self.draw(now)
        self.drawn = self.state(now)            # after drawing: draw() can settle page / tide_day
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
    cfg = wd.load_config()
    store = wd.Store(cfg["locations"]).start()
    WeatherDisplay(cfg, store).run()
