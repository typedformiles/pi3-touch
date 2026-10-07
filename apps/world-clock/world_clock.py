#!/usr/bin/env python3
"""
World Clock
Six cities as analogue clocks on the HyperPixel 4.0 (480x800 portrait). Each face takes the
colour of that city's sky right now - navy at night, violet and rose at twilight, gold around
sunrise and sunset, blue by day - from where the sun actually is there.

Tap a clock for that city's pages - Now, Weather, News, Money - which turn every 15 s like the
Weather app's; "< Cities" (or 3 minutes untouched) goes back to the clocks. Data for the open
city is fetched in the background and cached (city_data.CityStore); the clocks fetch nothing.
"""

import math
import os
import sys
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "..", "common"))
import city_data as cd  # noqa: E402
import sky  # noqa: E402
from cities import CITIES  # noqa: E402
from wmo import describe  # noqa: E402

# Use KMS/DRM driver on headless Pi (set before pygame.display.init)
if "DISPLAY" not in os.environ and "WAYLAND_DISPLAY" not in os.environ:
    os.environ.setdefault("SDL_VIDEODRIVER", "kmsdrm")
    os.environ.setdefault("SDL_KMSDRM_DEVICE_INDEX", "0")
os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
import pygame  # noqa: E402
import pgscreen  # noqa: E402
from pygame import gfxdraw  # noqa: E402
import weather_icons  # noqa: E402

# ── Config ────────────────────────────────────────────────────────────────────
HOME = ZoneInfo("Europe/London")
SCREEN_W, SCREEN_H = 480, 800
POLL = 0.05                 # seconds between checks for touches; the screen redraws once a second
DIM_AFTER = 300             # seconds of inactivity before dimming (5 min)
DIM_BRIGHTNESS = 120        # 0-255 (higher = brighter when dimmed)
CITY_IDLE = 180             # a city page goes back to the clocks after this long untouched
CYCLE_IDLE = 20             # ... and turns its pages every CYCLE_EVERY s once untouched this long
CYCLE_EVERY = 15
SWIPE = 80                  # px of sideways travel that counts as a swipe
PAGES = ["Now", "Weather", "News", "Money"]
HOME_REQUEST = "/run/pi3-touch/request-home"   # Pi3 Touch launcher picks this up
FONT_DIR = os.environ.get("PI3_FONT_DIR", "/usr/share/fonts/truetype/dejavu")

# ── Colours ───────────────────────────────────────────────────────────────────
BG          = (8,   10,  18)
BG_CARD     = (22,  26,  40)
TEXT_PRI    = (238, 240, 248)
TEXT_SEC    = (140, 148, 170)
TEXT_DIM    = (86,  94,  116)
INK_LIGHT   = (244, 244, 250)   # hands/ticks on dark faces
INK_DARK    = (22,  26,  44)    # ... and on light ones
SECONDS     = (255, 112, 67)    # second hand, and the accent on city pages
SUN         = (255, 214, 102)
MOON        = (226, 230, 244)
MOON_DARK   = (52,  58,  78)
GOOD        = (96,  204, 128)
WARN        = (236, 176, 70)
BAD         = (226, 92,  80)
RAIN        = (96,  170, 240)

# ── Layout (portrait 480x800) ─────────────────────────────────────────────────
TOP_H   = 64                # header (both views)
CELL_W  = SCREEN_W // 2     # clocks: 2 x 3 cells
CELL_H  = (SCREEN_H - TOP_H) // 3
RADIUS  = 84
FACE_Y  = 100               # face centre, from the top of its cell
PAD     = 16                # city pages
TABS_Y  = 736

SYMBOLS = {"GBP": "£", "USD": "$", "EUR": "€", "JPY": "¥", "AUD": "A$", "AED": "AED "}


def load_font(size, bold=False):
    path = os.path.join(FONT_DIR, "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf")
    try:
        return pygame.font.Font(path, size)
    except (OSError, FileNotFoundError):
        return pygame.font.SysFont("dejavusans,freesans,arial", size, bold=bold)


def polygon(surf, pts, colour):
    gfxdraw.aapolygon(surf, pts, colour)
    gfxdraw.filled_polygon(surf, pts, colour)


def disc(surf, x, y, r, colour):
    x, y, r = round(x), round(y), max(1, round(r))
    gfxdraw.aacircle(surf, x, y, r, colour)
    gfxdraw.filled_circle(surf, x, y, r, colour)


def hand(surf, cx, cy, degrees, length, width, colour, tail=0.0):
    """A tapered hand from `tail` behind the centre to `length` in front, pointing at degrees."""
    a = math.radians(degrees)
    dx, dy = math.sin(a), -math.cos(a)
    px, py = -dy * width / 2, dx * width / 2
    back = (cx - dx * tail, cy - dy * tail)
    tip = (cx + dx * length, cy + dy * length)
    polygon(surf, [(back[0] + px, back[1] + py), (tip[0] + px * .55, tip[1] + py * .55),
                   (tip[0] - px * .55, tip[1] - py * .55), (back[0] - px, back[1] - py)], colour)
    disc(surf, tip[0], tip[1], width * .28, colour)


def until(delta):
    """"2h 05m" / "12m" / "3d 4h" for a timedelta."""
    mins = max(0, int(delta.total_seconds() // 60))
    if mins >= 48 * 60:
        return f"{mins // 1440}d {mins % 1440 // 60}h"
    return f"{mins // 60}h {mins % 60:02d}m" if mins >= 60 else f"{mins}m"


def ago(iso, now):
    if not iso:
        return ""
    mins = int((now - datetime.fromisoformat(iso)).total_seconds() // 60)
    if mins < 60:
        return f"{max(mins, 1)}m ago"
    return f"{mins // 60}h ago" if mins < 48 * 60 else f"{mins // 1440}d ago"


def money(amount, currency):
    sym = SYMBOLS.get(currency, currency + " ")
    return f"{sym}{amount:,.0f}" if amount >= 1000 else f"{sym}{amount:,.2f}"


def wrap(text, font, width):
    lines, line = [], ""
    for word in text.split():
        trial = f"{line} {word}".strip()
        if line and font.size(trial)[0] > width:
            lines.append(line)
            line = word
        else:
            line = trial
    return lines + ([line] if line else [])


class WorldClock:
    def __init__(self, screen=None, store=None):
        if screen is None:
            self.display = pgscreen.Display(SCREEN_W, SCREEN_H, "World Clock")
            screen = self.display.surface
        else:
            self.display = None
            pygame.font.init()
        self.screen = screen
        self.store = store or cd.CityStore(CITIES)
        self.f_huge  = load_font(64, bold=True)
        self.f_big   = load_font(40, bold=True)
        self.f_large = load_font(28, bold=True)
        self.f_title = load_font(24, bold=True)
        self.f_date  = load_font(22, bold=True)
        self.f_city  = load_font(22, bold=True)
        self.f_medb  = load_font(20, bold=True)
        self.f_med   = load_font(19)
        self.f_info  = load_font(16)
        self.f_small = load_font(16)
        self.f_tiny  = load_font(13)
        self.cities = [(c["name"], ZoneInfo(c["tz"]), c["lat"], c["lon"]) for c in CITIES]
        self.faces = {}             # (city, colour, sun up) -> pre-drawn face
        self.base = None            # clocks: everything but the hands, redrawn once a minute
        self.base_minute = None
        self.hands_at = []          # (cx, cy, tz, ink) per clock, from the base
        self.buttons = {}
        self.last_touch = time.time()
        self.last_cycle = time.time()
        self.dimmed = self.sleeping = False
        self.drawn = None
        self.city = None            # index of the open city, or None for the clocks
        self.page = PAGES[0]
        self.down = None            # where the current touch started

    # ── Faces ─────────────────────────────────────────────────────────────────

    def face(self, name, colour, sun_up):
        """The static part of a clock: sky-coloured disc, rim, hour marks, sun or moon."""
        key = (name, colour, sun_up)
        if key in self.faces:
            return self.faces[key]
        size = RADIUS * 2 + 8
        c = size // 2
        surf = pygame.Surface((size, size))                    # opaque: much faster to blit
        surf.fill(BG)
        top, bottom = sky.mix(colour, (255, 255, 255), .16), sky.mix(colour, (0, 0, 0), .22)
        for y in range(-RADIUS, RADIUS + 1):                 # vertical gradient, like a sky
            half = math.sqrt(max(0, RADIUS * RADIUS - y * y))
            pygame.draw.line(surf, sky.mix(top, bottom, (y + RADIUS) / (2 * RADIUS)),
                             (c - half, c + y), (c + half, c + y))
        ink = INK_DARK if sky.is_light(colour) else INK_LIGHT
        for r, a in ((RADIUS, 90), (RADIUS - 1, 60)):         # soft anti-aliased rim
            gfxdraw.aacircle(surf, c, c, r, (*ink, a))
        for i in range(12):                                   # hour marks: plain bars
            quarter = i % 3 == 0
            a = math.radians(i * 30)
            dx, dy = math.sin(a), -math.cos(a)
            r0, r1, w = RADIUS - (20 if quarter else 14), RADIUS - 7, (2.4 if quarter else 1.3)
            px, py = -dy * w, dx * w
            polygon(surf, [(c + dx * r0 + px, c + dy * r0 + py), (c + dx * r1 + px, c + dy * r1 + py),
                           (c + dx * r1 - px, c + dy * r1 - py), (c + dx * r0 - px, c + dy * r0 - py)],
                    (*ink, 230 if quarter else 150))
        mx, my = c, c + RADIUS * .46                          # sun or moon, below the centre
        if sun_up:
            disc(surf, mx, my, 9, (*SUN, 70))
            disc(surf, mx, my, 6, SUN)
        else:
            disc(surf, mx, my, 7, MOON)
            disc(surf, mx + 3.5, my - 2.5, 6, bottom)
        if len(self.faces) > 60:
            self.faces.clear()
        self.faces[key] = self.fast(surf)
        return self.faces[key]

    @staticmethod
    def fast(surf):
        """In the display's pixel format, for quick blits (when there is a display)."""
        try:
            return surf.convert()
        except pygame.error:
            return surf

    def sky_now(self, i, now):
        """(colour, sun up) for city i at now."""
        _, _, lat, lon = self.cities[i]
        elevation = sky.sun_elevation(lat, lon, now)
        return sky.sky_colour(elevation), elevation > -0.8

    # ── Shared chrome ─────────────────────────────────────────────────────────

    def text(self, surf, s, font, colour, pos, anchor="topleft"):
        img = font.render(s, True, colour)
        rect = img.get_rect(**{anchor: pos})
        surf.blit(img, rect)
        return rect

    def card(self, rect, colour=BG_CARD, radius=14):
        pygame.draw.rect(self.screen, colour, rect, border_radius=radius)

    def draw_buttons(self, surf):
        """Sleep (moon) and Home (house), top right."""
        mx, my, mr = SCREEN_W - 16 - 12, 30, 10
        pygame.draw.circle(surf, TEXT_SEC, (mx, my), mr)
        pygame.draw.circle(surf, BG, (mx + 5, my - 4), mr - 2)
        self.buttons["sleep"] = pygame.Rect(mx - 20, my - 20, 40, 40)
        # Home button (house) - back to the Pi3 Touch menu; only when the launcher is installed
        if os.path.isdir(os.path.dirname(HOME_REQUEST)):
            hx, hy = SCREEN_W - 16 - 56, 31
            pygame.draw.polygon(surf, TEXT_SEC, [(hx - 13, hy - 1), (hx, hy - 12), (hx + 13, hy - 1)])
            pygame.draw.rect(surf, TEXT_SEC, (hx - 9, hy - 1, 18, 12))
            pygame.draw.rect(surf, BG, (hx - 2, hy + 4, 5, 7))          # door
            self.buttons["home"] = pygame.Rect(hx - 20, hy - 20, 40, 40)

    # ── The clocks ────────────────────────────────────────────────────────────

    def draw_base(self, now):
        """Everything but the hands - header, faces, names, times - for this minute."""
        base = pygame.Surface((SCREEN_W, SCREEN_H))
        base.fill(BG)
        self.buttons, self.hands_at = {}, []
        home = now.astimezone(HOME)
        self.text(base, f"{home:%A} {home.day} {home:%B}", self.f_date, TEXT_PRI, (16, 18))
        self.draw_buttons(base)
        for i, (name, tz, lat, lon) in enumerate(self.cities):
            col, row = i % 2, i // 2
            cx, cy = col * CELL_W + CELL_W // 2, TOP_H + row * CELL_H + FACE_Y
            colour, sun_up = self.sky_now(i, now)
            face = self.face(name, colour, sun_up)
            base.blit(face, face.get_rect(center=(cx, cy)))
            self.hands_at.append((cx, cy, tz, INK_DARK if sky.is_light(colour) else INK_LIGHT))
            self.text(base, name, self.f_city, TEXT_PRI, (cx, cy + RADIUS + 14), "midtop")
            local = now.astimezone(tz)
            info = " · ".join(filter(None, [f"{local:%H:%M}", sky.day_label(now, tz, HOME),
                                            sky.offset_label(now, tz, HOME)]))
            self.text(base, info, self.f_info, TEXT_SEC, (cx, cy + RADIUS + 42), "midtop")
            self.buttons[f"city{i}"] = pygame.Rect(col * CELL_W, TOP_H + row * CELL_H, CELL_W, CELL_H)
        return self.fast(base)

    def draw_hands(self, surf, cx, cy, local, ink, seconds=True):
        h, m, s = local.hour % 12, local.minute, local.second
        hand(surf, cx, cy, (h + m / 60) * 30, RADIUS * .5, 8, ink, tail=10)
        hand(surf, cx, cy, (m + (s / 60 if seconds else 0)) * 6, RADIUS * .76, 6, ink, tail=12)
        if seconds:
            hand(surf, cx, cy, s * 6, RADIUS * .84, 2.5, SECONDS, tail=18)
        disc(surf, cx, cy, 5, SECONDS)
        disc(surf, cx, cy, 2, ink)

    def draw_clocks(self, now):
        minute = int(now.timestamp() // 60)
        if minute != self.base_minute or self.base is None:
            self.base, self.base_minute = self.draw_base(now), minute
        self.screen.blit(self.base, (0, 0))
        for cx, cy, tz, ink in self.hands_at:
            self.draw_hands(self.screen, cx, cy, now.astimezone(tz), ink)

    # ── A city ────────────────────────────────────────────────────────────────

    def get(self, kind):
        entry = self.store.get(CITIES[self.city], kind)
        return entry["data"] if entry else None

    def waiting(self, kind, rect):
        err = self.store.error(CITIES[self.city], kind)
        self.text(self.screen, f"({err} - retrying)" if err else "Fetching…", self.f_small, TEXT_DIM,
                  rect.center, "center")

    def draw_city_header(self):
        x, y = PAD + 2, 32                                    # "< Cities"
        pygame.draw.lines(self.screen, TEXT_PRI, False, [(x + 9, y - 9), (x, y), (x + 9, y + 9)], 3)
        self.text(self.screen, "Cities", self.f_medb, TEXT_PRI, (x + 18, y), "midleft")
        self.buttons["back"] = pygame.Rect(0, 0, 130, TOP_H)
        self.text(self.screen, CITIES[self.city]["name"], self.f_title, TEXT_PRI, (SCREEN_W // 2 - 10, y), "center")
        self.draw_buttons(self.screen)

    def draw_tabs(self):
        pygame.draw.rect(self.screen, BG_CARD, (0, TABS_Y, SCREEN_W, SCREEN_H - TABS_Y))
        w = SCREEN_W / len(PAGES)
        for i, p in enumerate(PAGES):
            r = pygame.Rect(round(i * w), TABS_Y, round(w), SCREEN_H - TABS_Y)
            sel = p == self.page
            if sel:
                pygame.draw.rect(self.screen, SECONDS, (r.x + 14, TABS_Y, r.w - 28, 4), border_radius=2)
            self.text(self.screen, p, self.f_medb if sel else self.f_med, SECONDS if sel else TEXT_SEC,
                      r.center, "center")
            self.buttons[f"page:{p}"] = r

    def draw_city(self, now):
        self.buttons = {}
        self.screen.fill(BG)
        self.draw_city_header()
        {"Now": self.page_now, "Weather": self.page_weather, "News": self.page_news,
         "Money": self.page_money}[self.page](now)
        self.draw_tabs()

    def draw_moon(self, cx, cy, r, age, southern):
        """The moon as seen tonight: lit from the right while waxing (mirrored down south)."""
        disc(self.screen, cx, cy, r, MOON_DARK)
        k = math.cos(2 * math.pi * age)
        for y in range(-r, r + 1):
            half = math.sqrt(max(0, r * r - y * y))
            a, b = (k * half, half) if age < 0.5 else (-half, -k * half)
            if southern:
                a, b = -b, -a
            if b - a > 0.5:
                pygame.draw.line(self.screen, MOON, (cx + a, cy + y), (cx + b, cy + y))

    # Now: the time there, weather now, sun and moon, best time to call (and nearby quakes)
    def page_now(self, now):
        c, tz = CITIES[self.city], self.cities[self.city][1]
        local = now.astimezone(tz)
        colour, sun_up = self.sky_now(self.city, now)
        ink = INK_DARK if sky.is_light(colour) else INK_LIGHT
        ink2 = sky.mix(ink, colour, .35)

        # Hero: the city's sky as a gradient, the time, and its clock face
        r = pygame.Rect(PAD, TOP_H + 8, SCREEN_W - 2 * PAD, 212)
        top, bottom = sky.mix(colour, (255, 255, 255), .12), sky.mix(colour, (0, 0, 0), .25)
        hero = pygame.Surface(r.size, pygame.SRCALPHA)
        for y in range(r.h):
            pygame.draw.line(hero, sky.mix(top, bottom, y / r.h), (0, y), (r.w, y))
        mask = pygame.Surface(r.size, pygame.SRCALPHA)
        pygame.draw.rect(mask, (255, 255, 255, 255), mask.get_rect(), border_radius=18)
        hero.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MIN)
        self.screen.blit(hero, r)
        self.text(self.screen, f"{local:%H:%M}", self.f_huge, ink, (r.x + 20, r.y + 22))
        self.text(self.screen, f"{local:%a} {local.day} {local:%B}", self.f_medb, ink, (r.x + 22, r.y + 112))
        rel = " · ".join(filter(None, [sky.offset_label(now, tz, HOME) or "London time",
                                       sky.day_label(now, tz, HOME)]))
        self.text(self.screen, rel, self.f_small, ink2, (r.x + 22, r.y + 142))
        self.text(self.screen, "sun up" if sun_up else "after dark", self.f_small, ink2, (r.x + 22, r.y + 170))
        face = self.face(c["name"], colour, sun_up).copy()   # its square corners are BG: key them out
        face.set_colorkey(BG)
        fx, fy = r.right - 16 - RADIUS, r.centery
        pygame.draw.circle(self.screen, sky.mix(colour, (0, 0, 0), .2), (fx, fy), RADIUS + 3)
        self.screen.blit(face, face.get_rect(center=(fx, fy)))
        self.draw_hands(self.screen, fx, fy, local, INK_DARK if sky.is_light(colour) else INK_LIGHT, seconds=False)

        # Weather now
        w = self.get("weather")
        r = pygame.Rect(PAD, r.bottom + 12, SCREEN_W - 2 * PAD, 96)
        self.card(r)
        if w:
            cur, day = w["current"], w["daily"]
            weather_icons.draw_icon(self.screen, cur["weather_code"], cur["is_day"], r.x + 52, r.centery, 70, BG_CARD)
            t = self.text(self.screen, f"{round(cur['temperature_2m'])}°", self.f_big, TEXT_PRI, (r.x + 104, r.centery), "midleft")
            self.text(self.screen, describe(cur["weather_code"])[0], self.f_medb, TEXT_PRI, (t.right + 18, r.y + 22))
            self.text(self.screen, f"{round(day['temperature_2m_max'][0])}° / {round(day['temperature_2m_min'][0])}°"
                      f" · wind {round(cur['wind_speed_10m'])} kn", self.f_small, TEXT_SEC, (t.right + 18, r.y + 54))
        else:
            self.waiting("weather", r)

        # Sun and moon
        r = pygame.Rect(PAD, r.bottom + 12, SCREEN_W - 2 * PAD, 118)
        self.card(r)
        if w:
            rise, set_ = (datetime.fromisoformat(w["daily"][k][0]) for k in ("sunrise", "sunset"))
            day_len = int(w["daily"]["daylight_duration"][0] // 60)
            rows = [("Sunrise", f"{rise:%H:%M}"), ("Sunset", f"{set_:%H:%M}"), ("Daylight", f"{day_len // 60}h {day_len % 60:02d}m")]
            for j, (k, v) in enumerate(rows):
                y = r.y + 16 + j * 31
                self.text(self.screen, k, self.f_med, TEXT_SEC, (r.x + 16, y))
                self.text(self.screen, v, self.f_medb, TEXT_PRI, (r.x + 200, y), "topright")
        age, lit, phase = cd.moon(now)
        mx = r.right - 82                                      # the moon, with its phase under it
        self.draw_moon(mx, r.y + 38, 24, age, c["lat"] < 0)
        self.text(self.screen, phase, self.f_small, TEXT_PRI, (mx, r.y + 70), "midtop")
        self.text(self.screen, f"{round(lit * 100)}% lit", self.f_tiny, TEXT_SEC, (mx, r.y + 92), "midtop")

        # Best time to call, and any earthquake nearby
        r = pygame.Rect(PAD, r.bottom + 12, SCREEN_W - 2 * PAD, TABS_Y - 12 - r.bottom - 12)
        self.card(r)
        good, window = cd.call_window(now, tz, HOME)
        if c["tz"] == "Europe/London":
            self.text(self.screen, "Home", self.f_medb, TEXT_PRI, (r.x + 16, r.y + 14))
        else:
            pygame.draw.circle(self.screen, GOOD if good else WARN, (r.x + 24, r.y + 26), 7)
            self.text(self.screen, "Good time to call" if good else f"Not ideal to call - {local:%H:%M} there",
                      self.f_medb, TEXT_PRI, (r.x + 40, r.y + 14))
            if window:
                a, b = window
                self.text(self.screen, f"Best {a:%H:%M}-{b:%H:%M} London · {a.astimezone(tz):%H:%M}-{b.astimezone(tz):%H:%M} there",
                          self.f_small, TEXT_SEC, (r.x + 16, r.y + 46))
        quakes = cd.nearby_quakes(self.get("quakes") or {}, c["lat"], c["lon"])
        if quakes:
            qk = quakes[0]
            self.text(self.screen, f"Earthquake M{qk['mag']:.1f}, {qk['km']} km away · {ago(qk['time'].isoformat(), now)}",
                      self.f_small, WARN, (r.x + 16, r.y + 74))

    # Weather: now, the next 12 hours, 5 days, air quality / UV / sea
    def page_weather(self, now):
        w = self.get("weather")
        body = pygame.Rect(PAD, TOP_H + 8, SCREEN_W - 2 * PAD, TABS_Y - TOP_H - 16)
        if not w:
            return self.waiting("weather", body)
        tz = self.cities[self.city][1]
        cur, daily = w["current"], w["daily"]
        r = pygame.Rect(PAD, TOP_H + 8, SCREEN_W - 2 * PAD, 104)
        self.card(r)
        weather_icons.draw_icon(self.screen, cur["weather_code"], cur["is_day"], r.x + 56, r.centery, 84, BG_CARD)
        t = self.text(self.screen, f"{round(cur['temperature_2m'])}°", self.f_huge, TEXT_PRI, (r.x + 112, r.centery), "midleft")
        self.text(self.screen, describe(cur["weather_code"])[0], self.f_medb, TEXT_PRI, (t.right + 14, r.y + 24))
        self.text(self.screen, f"Feels {round(cur['apparent_temperature'])}° · wind {round(cur['wind_speed_10m'])} kn",
                  self.f_small, TEXT_SEC, (t.right + 14, r.y + 56))

        # Next 12 hours, every 2
        r = pygame.Rect(PAD, r.bottom + 10, SCREEN_W - 2 * PAD, 134)
        self.card(r)
        start = now.astimezone(tz).replace(minute=0, second=0, microsecond=0, tzinfo=None)
        hours = [dict(zip(w["hourly"], vals)) for vals in zip(*w["hourly"].values())]
        hours = [h for h in hours if datetime.fromisoformat(h["time"]) >= start][:12:2]
        cw = r.w / max(1, len(hours))
        for j, h in enumerate(hours):
            cx = r.x + cw * j + cw / 2
            self.text(self.screen, "Now" if j == 0 else h["time"][11:13], self.f_small, TEXT_SEC, (cx, r.y + 10), "midtop")
            weather_icons.draw_icon(self.screen, h["weather_code"], h["is_day"], cx, r.y + 58, 40, BG_CARD)
            self.text(self.screen, f"{round(h['temperature_2m'])}°", self.f_medb, TEXT_PRI, (cx, r.y + 84), "midtop")
            pct = h["precipitation_probability"] or 0
            self.text(self.screen, f"{pct}%", self.f_tiny, RAIN if pct >= 30 else TEXT_DIM, (cx, r.y + 110), "midtop")

        # 5 days
        y = r.bottom + 10
        for j in range(1, min(6, len(daily["time"]))):
            row = pygame.Rect(PAD, y, SCREEN_W - 2 * PAD, 48)
            if j % 2:
                self.card(row, BG_CARD, radius=8)
            day = datetime.fromisoformat(daily["time"][j])
            cy = row.centery
            self.text(self.screen, "Tomorrow" if j == 1 else f"{day:%a} {day.day}", self.f_med, TEXT_PRI, (row.x + 12, cy), "midleft")
            weather_icons.draw_icon(self.screen, daily["weather_code"][j], True, row.x + 170, cy, 36,
                                    BG_CARD if j % 2 else BG)
            hi = self.text(self.screen, f"{round(daily['temperature_2m_max'][j])}°", self.f_medb, TEXT_PRI, (row.x + 214, cy), "midleft")
            self.text(self.screen, f"{round(daily['temperature_2m_min'][j])}°", self.f_med, TEXT_SEC, (hi.right + 10, cy), "midleft")
            pct = daily["precipitation_probability_max"][j] or 0
            self.text(self.screen, f"{pct}%", self.f_med, RAIN if pct >= 30 else TEXT_DIM, (row.right - 12, cy), "midright")
            y += 50

        # Air quality, UV, sea
        air, sea = self.get("air"), self.get("sea") if CITIES[self.city].get("sea") else None
        chips = []
        if air:
            aqi = air["current"]["us_aqi"]
            chips.append(("Air quality", f"{aqi}", cd.band(aqi, cd.AQI), GOOD if aqi < 51 else WARN if aqi < 151 else BAD))
        uv = daily["uv_index_max"][0]
        chips.append(("UV today", f"{uv:.0f}", cd.band(uv, cd.UV), GOOD if uv < 3 else WARN if uv < 8 else BAD))
        if sea and sea["current"].get("sea_surface_temperature") is not None:
            chips.append(("Sea", f"{sea['current']['sea_surface_temperature']:.0f}°",
                          f"waves {sea['current'].get('wave_height') or 0:.1f} m", RAIN))
        r = pygame.Rect(PAD, y + 4, SCREEN_W - 2 * PAD, TABS_Y - 10 - y - 4)
        gap = 10
        cw = (r.w - gap * (len(chips) - 1)) / len(chips)
        for j, (label, value, sub, col) in enumerate(chips):
            cr = pygame.Rect(round(r.x + j * (cw + gap)), r.y, round(cw), r.h)
            self.card(cr)
            self.text(self.screen, label, self.f_tiny, TEXT_SEC, (cr.x + 12, cr.y + 10))
            self.text(self.screen, value, self.f_large, col, (cr.x + 12, cr.y + 30))
            self.text(self.screen, sub, self.f_tiny, TEXT_SEC, (cr.x + 12, cr.y + 68))

    # News: the city's top headlines
    def page_news(self, now):
        c = CITIES[self.city]
        items = self.get("news")
        body = pygame.Rect(PAD, TOP_H + 8, SCREEN_W - 2 * PAD, TABS_Y - TOP_H - 16)
        self.text(self.screen, c["news"][0], self.f_medb, SECONDS, (PAD, TOP_H + 6))
        entry = self.store.get(c, "news")
        if entry:
            self.text(self.screen, f"updated {datetime.fromtimestamp(entry['fetched']):%H:%M}", self.f_tiny, TEXT_DIM,
                      (SCREEN_W - PAD, TOP_H + 12), "topright")
        if not items:
            return self.waiting("news", body)
        y = TOP_H + 40
        for item in items:
            lines = wrap(item["title"], self.f_med, SCREEN_W - 2 * PAD - 8)[:3]
            need = len(lines) * 25 + 22
            if y + need > TABS_Y - 8:
                break
            pygame.draw.line(self.screen, BG_CARD, (PAD, y - 6), (SCREEN_W - PAD, y - 6), 1)
            for line in lines:
                self.text(self.screen, line, self.f_med, TEXT_PRI, (PAD, y))
                y += 25
            self.text(self.screen, ago(item["time"], now), self.f_tiny, TEXT_DIM, (PAD, y + 1))
            y += 30

    # Money: £1 in the local currency, the stock exchange, public holidays
    def page_money(self, now):
        c = CITIES[self.city]
        tz = self.cities[self.city][1]
        rates = self.get("rates")
        r = pygame.Rect(PAD, TOP_H + 8, SCREEN_W - 2 * PAD, 150)
        self.card(r)
        self.text(self.screen, "Exchange rate", self.f_small, TEXT_SEC, (r.x + 16, r.y + 12))
        if rates:
            if c["currency"] == "GBP":
                usd, eur = cd.rate(rates, "USD"), cd.rate(rates, "EUR")
                self.text(self.screen, f"£1 = {money(usd, 'USD')} · {money(eur, 'EUR')}", self.f_big, TEXT_PRI, (r.x + 16, r.y + 40))
                self.text(self.screen, f"$1 = {money(1 / usd, 'GBP')} · €1 = {money(1 / eur, 'GBP')}",
                          self.f_small, TEXT_SEC, (r.x + 16, r.y + 96))
            else:
                per = cd.rate(rates, c["currency"])
                self.text(self.screen, f"£1 = {money(per, c['currency'])}", self.f_big, TEXT_PRI, (r.x + 16, r.y + 40))
                unit = 1000 if per > 50 else 10
                self.text(self.screen, f"{money(unit, c['currency'])} = {money(unit / per, 'GBP')}",
                          self.f_small, TEXT_SEC, (r.x + 16, r.y + 96))
            note = "ECB reference rate" + (", via the dollar peg" if c["currency"] == "AED" else "")
            self.text(self.screen, f"{note} · {rates.get('date', '')}", self.f_tiny, TEXT_DIM, (r.x + 16, r.y + 124))
        else:
            self.waiting("rates", r)

        # Stock exchange
        hols = self.get("holidays") if c.get("holidays") else None
        today = now.astimezone(tz).date().isoformat()
        holiday_today = next((h["name"] for h in hols or [] if h["date"] == today), None)
        ex = c["exchange"]
        st = cd.exchange_status(now, ex)
        r = pygame.Rect(PAD, r.bottom + 12, SCREEN_W - 2 * PAD, 130)
        self.card(r)
        self.text(self.screen, f"{ex['name']} stock exchange", self.f_small, TEXT_SEC, (r.x + 16, r.y + 12))
        label, col = {"open": ("Open", GOOD), "break": ("Lunch break", WARN), "closed": ("Closed", TEXT_SEC)}[st["state"]]
        if holiday_today and st["state"] != "closed":
            label, col = "Public holiday", WARN
        self.text(self.screen, label, self.f_big, col, (r.x + 16, r.y + 38))
        verb = "closes" if st["state"] == "open" else "opens"
        when = st["until"]
        same_day = when.date() == now.astimezone(ZoneInfo(ex["tz"])).date()
        self.text(self.screen, f"{verb} in {until(when - now)} · {when:%H:%M}" + ("" if same_day else f" {when:%a}") +
                  ("" if ex["tz"] == c["tz"] else f" {ex['tz'].split('/')[-1].replace('_', ' ')} time"),
                  self.f_small, TEXT_SEC, (r.x + 16, r.y + 92))

        # Public holidays
        r = pygame.Rect(PAD, r.bottom + 12, SCREEN_W - 2 * PAD, TABS_Y - 12 - r.bottom - 12)
        self.card(r)
        self.text(self.screen, "Public holidays", self.f_small, TEXT_SEC, (r.x + 16, r.y + 12))
        if not c.get("holidays"):
            self.text(self.screen, f"Weekend: {c.get('weekend', '')}", self.f_medb, TEXT_PRI, (r.x + 16, r.y + 44))
            self.text(self.screen, "Holiday dates aren't published for here", self.f_small, TEXT_DIM, (r.x + 16, r.y + 76))
            return
        if hols is None:
            return self.waiting("holidays", r)
        y = r.y + 42
        for h in hols:
            if y + 30 > r.bottom - 6:
                break
            d = datetime.fromisoformat(h["date"]).date()
            days = (d - now.astimezone(tz).date()).days
            col = WARN if days == 0 else TEXT_PRI
            self.text(self.screen, f"{d:%a} {d.day} {d:%b}", self.f_med, col, (r.x + 16, y))
            name, room = h["name"], r.right - 62 - (r.x + 150)
            while self.f_medb.size(name + "…")[0] > room and len(name) > 4:
                name = name[:-1]
            self.text(self.screen, name if name == h["name"] else name.rstrip() + "…", self.f_medb, col, (r.x + 150, y))
            self.text(self.screen, "today" if days == 0 else f"{days}d", self.f_small, TEXT_DIM, (r.right - 16, y + 2), "topright")
            y += 34

    # ── Frames ────────────────────────────────────────────────────────────────

    def draw(self, now=None):
        now = now or datetime.now(sky.UTC)
        if self.city is None:
            self.draw_clocks(now)
        else:
            self.draw_city(now)
        if self.sleeping or self.dimmed:                      # darken in place (no alpha layer)
            level = 10 if self.sleeping else DIM_BRIGHTNESS
            self.screen.fill((level, level, level), special_flags=pygame.BLEND_MULT)

    def state(self, now):
        """Everything on screen depends on: clocks change every second, city pages every minute
        (or when their data, page or dimming changes)."""
        if self.city is None:
            return ("clocks", now.replace(microsecond=0), self.dimmed, self.sleeping)
        c = CITIES[self.city]
        data = tuple((self.store.get(c, k) or {}).get("fetched") for k in cd.kinds(c))
        errors = tuple(self.store.error(c, k) for k in cd.kinds(c))
        return ("city", self.city, self.page, now.strftime("%Y-%m-%d %H:%M"), data, errors,
                self.dimmed, self.sleeping)

    def tick(self):
        """Idle bookkeeping: dimming; on a city, turning pages and going back to the clocks."""
        idle = time.time() - self.last_touch
        self.dimmed = idle > DIM_AFTER
        if self.city is not None and not self.sleeping:
            if idle > CITY_IDLE:
                self.close_city()
            elif idle > CYCLE_IDLE and time.time() - self.last_cycle > CYCLE_EVERY:
                self.turn_page(1)

    def frame(self, now=None):
        """Redraw only when something on screen would change. Returns True if it drew."""
        now = now or datetime.now(sky.UTC)
        self.tick()
        if self.state(now) == self.drawn:
            return False
        self.draw(now)
        self.drawn = self.state(now)
        return True

    # ── Touch ─────────────────────────────────────────────────────────────────

    def open_city(self, i):
        self.city, self.page, self.last_cycle = i, PAGES[0], time.time()
        self.store.set_active(CITIES[i])

    def close_city(self):
        self.city = None
        self.store.set_active(None)

    def turn_page(self, step):
        self.page = PAGES[(PAGES.index(self.page) + step) % len(PAGES)]
        self.last_cycle = time.time()

    def touch_down(self, pos):
        self.down = pos

    def touch_up(self, pos):
        """A touch ended: on a city, a sideways swipe turns the page; anything else is a tap."""
        start, self.down = self.down or pos, None
        dx, dy = pos[0] - start[0], pos[1] - start[1]
        if (self.city is not None and abs(dx) > SWIPE and abs(dx) > abs(dy)
                and not (self.sleeping or self.dimmed)):
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
        elif hit == "back":
            self.close_city()
        elif hit and hit.startswith("city"):
            self.open_city(int(hit[4:]))
        elif hit and hit.startswith("page:"):
            self.page = hit[5:]
            self.last_cycle = time.time()

    def run(self):
        while True:
            for event in pygame.event.get():
                if event.type == pygame.QUIT or (event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE):
                    pygame.quit()
                    return
                # SDL also turns touches into mouse events; take the finger ones only
                if event.type == pygame.FINGERDOWN:
                    self.touch_down(self.display.point(event))
                elif event.type == pygame.FINGERUP:
                    self.touch_up(self.display.point(event))
                elif event.type == pygame.MOUSEBUTTONDOWN and not getattr(event, "touch", False):
                    self.touch_down(self.display.point(event))
                elif event.type == pygame.MOUSEBUTTONUP and not getattr(event, "touch", False):
                    self.touch_up(self.display.point(event))
            if self.frame():
                self.display.present()
            time.sleep(POLL)


if __name__ == "__main__":
    WorldClock(store=cd.CityStore(CITIES).start()).run()
