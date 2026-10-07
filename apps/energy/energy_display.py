#!/usr/bin/env python3
"""
Energy & Grid Display
How clean the electricity is, fullscreen on the HyperPixel 4.0 (480x800 portrait).

Places (places.json: a region by postcode district, or Great Britain) run along the top:
tap one to switch. Pages run along the bottom: Now (carbon intensity and the greenest
hours to plug in), Mix (where the power comes from) and Forecast (48 hours). Swipe
left/right to change page too. Data is fetched and cached in the background
(energy_data.Store); the dot top right is green when it's fresh.
"""

import math
import os
import sys
import time
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "..", "common"))
import energy_data as ed  # noqa: E402

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
CYCLE_IDLE = 60             # with cycle_seconds set: start cycling pages after this idle time
SWIPE = 80                  # px of sideways travel that counts as a swipe
HOME_REQUEST = "/run/pi3-touch/request-home"   # Pi3 Touch launcher picks this up
FONT_DIR = os.environ.get("PI3_FONT_DIR", "/usr/share/fonts/truetype/dejavu")
PAGES = ["Now", "Mix", "Forecast"]

# ── Colours ───────────────────────────────────────────────────────────────────
BG          = (10,  16,  26)
BG_CARD     = (22,  32,  48)
BG_CARD_HI  = (36,  52,  76)
TEXT_PRI    = (238, 242, 248)
TEXT_SEC    = (150, 165, 185)
TEXT_DIM    = (88,  102, 122)
ACCENT      = (80,  200, 130)   # grid green
GRID        = (36,  50,  70)
OK_GREEN    = (80,  200, 120)
STALE       = (230, 170, 60)
BAD_RED     = (200, 70,  70)

BAND_COLOURS = {"very low": (40, 190, 120), "low": (130, 210, 100), "moderate": (232, 200, 64),
                "high": (240, 140, 50), "very high": (225, 70, 70)}
BAND_SHORT = {"very low": "V low", "low": "Low", "moderate": "Mod", "high": "High", "very high": "V high"}
FUEL_COLOURS = {"wind": (90, 180, 240), "solar": (250, 204, 70), "hydro": (60, 200, 200),
                "nuclear": (170, 125, 240), "biomass": (150, 180, 90), "imports": (150, 162, 184),
                "other": (110, 122, 142), "gas": (238, 120, 62), "coal": (150, 110, 96)}
GROUP_COLOURS = {"zero carbon": ACCENT, "fossil": FUEL_COLOURS["gas"], "other": TEXT_SEC}

# ── Layout constants (portrait 480x800) ───────────────────────────────────────
PAD     = 16
LOC_Y   = 58            # place buttons
LOC_H   = 50
BODY_Y  = 124           # page content
TABS_Y  = 736           # page tabs
TABS_H  = SCREEN_H - TABS_Y


def band_colour(b):
    return BAND_COLOURS.get(b, TEXT_SEC)


def fuel_colour(f):
    return FUEL_COLOURS.get(f, FUEL_COLOURS["other"])


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
    return f"{day:%A}"


def hhmm(t):
    return f"{t.astimezone(ed.LOCAL):%H:%M}"


def span(w):
    return f"{hhmm(w['start'])}–{hhmm(w['end'])}"


class EnergyDisplay:
    def __init__(self, config, store, screen=None):
        self.places = config["places"]
        self.cycle_seconds = config.get("cycle_seconds", 0)
        self.window_hours = config.get("window_hours", 3)
        self.store = store

        if screen is None:
            self.display = pgscreen.Display(SCREEN_W, SCREEN_H, "Energy & Grid")
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
        self.f_smallb = load_font(18, bold=True)
        self.f_tiny  = load_font(14)
        self.f_loc   = load_font(19, bold=True)
        self.f_clock = load_font(28, bold=True)

        self.place_i = 0
        self.page = "Now"
        self.last_touch = time.time()
        self.last_cycle = time.time()
        self.dimmed = False
        self.sleeping = False
        self.down = None            # where the current touch started
        self.buttons = {}           # name -> Rect, rebuilt every frame
        self.cache = {}             # (place, fetched) -> parsed data
        self.drawn = None           # state() of the frame on screen

    # ── Helpers ───────────────────────────────────────────────────────────────

    @property
    def place(self):
        return self.places[self.place_i]

    @property
    def hours_label(self):
        h = self.window_hours
        return f"{h:g} hours" if h != 1 else "hour"

    def text(self, s, font, colour, pos, anchor="topleft"):
        surf = font.render(s, True, colour)
        rect = surf.get_rect(**{anchor: pos})
        self.screen.blit(surf, rect)
        return rect

    def card(self, rect, colour=BG_CARD, radius=14):
        pygame.draw.rect(self.screen, colour, rect, border_radius=radius)

    def data(self):
        """The current place's data, parsed once per fetch."""
        entry = self.store.get(self.place)
        if not entry:
            return None
        key = (self.place["name"], entry["fetched"])
        if key not in self.cache:
            try:
                self.cache[key] = ed.parse_entry(entry)
            except (KeyError, TypeError, ValueError) as e:
                print(f"[energy] bad data for {self.place['name']}: {e}", flush=True)
                self.cache[key] = None
            self.cache = {k: v for k, v in self.cache.items() if k[0] != key[0] or k == key}
        return self.cache[key]

    def draw_arrow(self, cx, cy, length, bearing, colour, width=4):
        """Arrow centred on (cx, cy) pointing towards bearing (degrees, 0 = up)."""
        dx, dy = math.sin(math.radians(bearing)), -math.cos(math.radians(bearing))
        tip = (cx + dx * length / 2, cy + dy * length / 2)
        tail = (cx - dx * length / 2, cy - dy * length / 2)
        head = length * 0.38
        base = (tip[0] - dx * head, tip[1] - dy * head)
        px, py = -dy * head * 0.55, dx * head * 0.55
        pygame.draw.line(self.screen, colour, tail, base, width)
        pygame.draw.polygon(self.screen, colour, [tip, (base[0] + px, base[1] + py), (base[0] - px, base[1] - py)])

    def draw_mix_bar(self, rect, mix):
        """The mix as one bar, cleanest fuels on the left."""
        total = sum(mix.values()) or 1
        x = rect.x
        bar = pygame.Surface(rect.size, pygame.SRCALPHA)
        for fuel in ed.FUELS + sorted(set(mix) - set(ed.FUELS)):
            w = rect.w * mix.get(fuel, 0) / total
            if w > 0:
                pygame.draw.rect(bar, fuel_colour(fuel), (round(x - rect.x), 0, math.ceil(w), rect.h))
                x += w
        mask = pygame.Surface(rect.size, pygame.SRCALPHA)        # round the ends
        pygame.draw.rect(mask, (255, 255, 255, 255), mask.get_rect(), border_radius=rect.h // 2)
        bar.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MIN)
        self.screen.blit(bar, rect)

    def draw_donut(self, cx, cy, r_out, r_in, mix):
        """Mix as a ring, drawn at 3x and scaled down to smooth the edges."""
        k = 3
        size = 2 * r_out * k
        ring = pygame.Surface((size, size), pygame.SRCALPHA)
        c = size / 2
        total = sum(mix.values()) or 1
        a = -90.0
        for fuel in ed.FUELS + sorted(set(mix) - set(ed.FUELS)):
            sweep = 360 * mix.get(fuel, 0) / total
            if sweep <= 0:
                continue
            steps = max(2, int(sweep / 2))
            pts = [(c + math.cos(math.radians(a + sweep * i / steps)) * r_out * k,
                    c + math.sin(math.radians(a + sweep * i / steps)) * r_out * k) for i in range(steps + 1)]
            pts += [(c + math.cos(math.radians(a + sweep * i / steps)) * r_in * k,
                     c + math.sin(math.radians(a + sweep * i / steps)) * r_in * k) for i in range(steps, -1, -1)]
            pygame.draw.polygon(ring, fuel_colour(fuel), pts)
            a += sweep
        ring = pygame.transform.smoothscale(ring, (2 * r_out, 2 * r_out))
        self.screen.blit(ring, (cx - r_out, cy - r_out))

    # ── Chrome: status bar, places, page tabs ────────────────────────────────

    def draw_status_bar(self, now):
        self.text(now.strftime("%H:%M"), self.f_clock, TEXT_PRI, (PAD, 12))

        entry = self.store.get(self.place)
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

    def draw_places(self):
        n = len(self.places)
        gap = 8
        w = (SCREEN_W - 2 * PAD - gap * (n - 1)) / n
        for i, place in enumerate(self.places):
            r = pygame.Rect(round(PAD + i * (w + gap)), LOC_Y, round(w), LOC_H)
            sel = i == self.place_i
            self.card(r, ACCENT if sel else BG_CARD, radius=12)
            label = truncate(place["name"], self.f_loc, r.w - 10)
            self.text(label, self.f_loc, (8, 26, 16) if sel else TEXT_SEC, r.center, "center")
            self.buttons[f"place{i}"] = r

    def draw_tabs(self):
        pygame.draw.rect(self.screen, BG_CARD, (0, TABS_Y, SCREEN_W, TABS_H))
        w = SCREEN_W / len(PAGES)
        for i, p in enumerate(PAGES):
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

    def draw_waiting(self):
        err = self.store.error(self.place)
        self.draw_message(f"Fetching grid data for {self.place['name']}…", f"({err} - retrying)" if err else "")

    # ── Page: Now ─────────────────────────────────────────────────────────────

    def draw_now(self, now):
        d = self.data()
        cur = ed.current(d["periods"], now) if d else None
        if not cur:
            return self.draw_waiting()
        periods = d["periods"]

        # Intensity card: the number, its band, and the five bands as a scale
        r = pygame.Rect(PAD, BODY_Y, SCREEN_W - 2 * PAD, 250)
        self.card(r)
        g = ed.value(cur)
        b = cur["index"] or ed.band(g, periods)
        col = band_colour(b)
        self.text(d["region"], self.f_medb, TEXT_PRI, (r.x + 16, r.y + 14))
        self.text(f"{hhmm(cur['start'])}–{hhmm(cur['end'])}", self.f_small, TEXT_SEC, (r.right - 16, r.y + 16), "topright")
        nr = self.text(f"{g}", self.f_huge, col, (r.x + 12, r.y + 40))
        self.text("g CO₂/kWh", self.f_small, TEXT_SEC, (nr.right + 8, nr.bottom - 52))
        self.text("measured" if cur["actual"] is not None else "forecast", self.f_tiny, TEXT_DIM,
                  (nr.right + 8, nr.bottom - 28))
        self.text((b or "").capitalize(), self.f_large, col, (r.x + 16, r.y + 142))

        tr = ed.trend(periods, now)
        if tr:
            state, avg = tr
            tcol = {"falling": BAND_COLOURS["low"], "rising": BAND_COLOURS["high"]}.get(state, TEXT_SEC)
            ax, ay = r.right - 40, r.y + 150
            self.draw_arrow(ax, ay, 30, {"falling": 135, "rising": 45}.get(state, 90), tcol, 5)
            word = {"falling": "Greener", "rising": "Dirtier", "steady": "Steady"}[state]
            self.text(word, self.f_medb, tcol, (ax - 26, ay - 14), "topright")
            self.text(f"next 3 h · {avg:.0f} g", self.f_tiny, TEXT_SEC, (ax - 26, ay + 12), "topright")

        sy, gap = r.y + 196, 6
        sw = (r.w - 32 - gap * 4) / 5
        for i, name in enumerate(ed.BANDS):
            x = round(r.x + 16 + i * (sw + gap))
            on = name == b
            c = BAND_COLOURS[name]
            pygame.draw.rect(self.screen, c if on else tuple(v // 3 for v in c), (x, sy, round(sw), 12 if on else 8),
                             border_radius=4)
            self.text(BAND_SHORT[name], self.f_tiny, TEXT_PRI if on else TEXT_DIM, (x + sw / 2, sy + 22), "midtop")

        # Greenest hours to plug in, in the next 24
        r = pygame.Rect(PAD, BODY_Y + 264, SCREEN_W - 2 * PAD, 150)
        self.card(r)
        self.text(f"Greenest {self.hours_label} · next 24 h", self.f_small, TEXT_SEC, (r.x + 16, r.y + 14))
        best = ed.best_window(periods, now, self.window_hours)
        if best:
            start = best["start"].astimezone(ed.LOCAL)
            bcol = band_colour(best["band"])
            self.text(day_label(start.date(), now), self.f_medb, TEXT_PRI, (r.x + 16, r.y + 46))
            self.text(span(best), self.f_big, TEXT_PRI, (r.x + 14, r.y + 76))
            self.text(f"{best['avg']:.0f} g", self.f_big, bcol, (r.right - 16, r.y + 40), "topright")
            self.text((best["band"] or "").capitalize(), self.f_small, bcol, (r.right - 16, r.y + 88), "topright")
            when = "now" if best["start"] <= now else f"in {until(best['start'] - now)}"
            self.text(when, self.f_small, TEXT_SEC, (r.right - 16, r.y + 114), "topright")
        else:
            self.text("Not enough forecast yet", self.f_med, TEXT_DIM, (r.x + 16, r.y + 60))

        # Where the power comes from now
        r = pygame.Rect(PAD, BODY_Y + 428, SCREEN_W - 2 * PAD, TABS_Y - BODY_Y - 428 - 14)
        self.card(r)
        mix, _ = ed.mix_now(d, now)
        self.text("Generation mix", self.f_small, TEXT_SEC, (r.x + 16, r.y + 14))
        if not mix:
            return
        z = ed.groups(mix)["zero carbon"]
        self.text(f"Zero carbon {z:.0f}%", self.f_medb, ACCENT, (r.right - 16, r.y + 12), "topright")
        self.draw_mix_bar(pygame.Rect(r.x + 16, r.y + 50, r.w - 32, 26), mix)
        top = [(f, p) for f, p in ed.by_share(mix) if p > 0][:4]
        cw = (r.w - 32) / 2
        for i, (fuel, pct) in enumerate(top):
            x, y = r.x + 16 + (i % 2) * cw, r.y + 98 + (i // 2) * 32
            pygame.draw.circle(self.screen, fuel_colour(fuel), (round(x + 7), y + 11), 7)
            self.text(fuel.capitalize(), self.f_med, TEXT_PRI, (x + 22, y))
            self.text(f"{pct:.0f}%", self.f_medb, TEXT_PRI, (x + cw - 22, y), "topright")

    # ── Page: Mix ─────────────────────────────────────────────────────────────

    def draw_mix(self, now):
        d = self.data()
        mix, when = ed.mix_now(d, now) if d else (None, None)
        if not mix:
            return self.draw_waiting()
        self.text(f"Power in {d['region']}", self.f_medb, TEXT_PRI, (PAD, BODY_Y))
        self.text(f"{hhmm(when)}–{hhmm(when + ed.HALF_HOUR)}", self.f_small, TEXT_SEC, (SCREEN_W - PAD, BODY_Y + 2), "topright")

        # Ring of the mix, with the zero carbon / fossil / other split beside it
        cx, cy = PAD + 112, BODY_Y + 150
        self.draw_donut(cx, cy, 104, 64, mix)
        g = ed.groups(mix)
        self.text(f"{g['zero carbon']:.0f}%", self.f_large, ACCENT, (cx, cy - 2), "center")
        self.text("zero C", self.f_tiny, TEXT_SEC, (cx, cy + 22), "center")
        x = cx + 132
        for i, (name, words) in enumerate((("zero carbon", ["wind, solar,", "hydro, nuclear"]),
                                           ("fossil", ["gas, coal"]),
                                           ("other", ["biomass,", "imports, other"]))):
            y = BODY_Y + 46 + i * 72
            self.text(f"{g[name]:.0f}%", self.f_large, GROUP_COLOURS[name], (x, y))
            self.text(name.capitalize(), self.f_smallb, TEXT_PRI, (x + 82, y + 2))
            for j, line in enumerate(words):
                self.text(line, self.f_tiny, TEXT_SEC, (x + 82, y + 24 + j * 16))

        # One bar per fuel, biggest first
        bx0, bx1 = 162, SCREEN_W - PAD - 70
        top = max(mix.values()) or 1
        row_h = 36
        y0 = BODY_Y + 278
        for i, (fuel, pct) in enumerate(ed.by_share(mix)[:12]):
            cy = y0 + i * row_h + row_h // 2
            if cy + row_h // 2 > TABS_Y - 6:
                break
            if i % 2 == 0:
                self.card(pygame.Rect(PAD - 6, cy - row_h // 2, SCREEN_W - 2 * PAD + 12, row_h), BG_CARD, radius=8)
            col = fuel_colour(fuel)
            pygame.draw.circle(self.screen, col, (PAD + 8, cy), 7)
            self.text(fuel.capitalize(), self.f_med, TEXT_PRI if pct > 0 else TEXT_DIM, (PAD + 24, cy), "midleft")
            w = round((bx1 - bx0) * pct / top)
            if w > 0:
                pygame.draw.rect(self.screen, col, (bx0, cy - 7, max(w, 6), 14), border_radius=7)
            self.text(f"{pct:.1f}%" if 0 < pct < 1 else f"{pct:.0f}%", self.f_medb,
                      TEXT_PRI if pct > 0 else TEXT_DIM, (SCREEN_W - PAD, cy), "midright")

    # ── Page: Forecast (48 hours) ─────────────────────────────────────────────

    def draw_forecast(self, now):
        d = self.data()
        ps = ed.ahead(d["periods"], now) if d else []
        if not ps:
            return self.draw_waiting()
        periods = d["periods"]
        hours = len(ps) // 2
        self.text(f"Next {hours} hours", self.f_medb, TEXT_PRI, (PAD, BODY_Y))
        self.text("g CO₂/kWh · half-hourly", self.f_tiny, TEXT_DIM, (SCREEN_W - PAD, BODY_Y + 6), "topright")

        days = ed.best_by_day(periods, now, self.window_hours)
        chart = pygame.Rect(50, BODY_Y + 62, SCREEN_W - PAD - 50, 230)
        top = max(p["forecast"] for p in ps)
        step = 50 if top <= 300 else 100
        top = step * math.ceil(max(top, 1) / step)
        t0, t1 = ps[0]["start"], ps[0]["start"] + timedelta(hours=48)

        def px(t):
            return chart.x + chart.w * (t - t0).total_seconds() / (t1 - t0).total_seconds()

        def py(g):
            return chart.bottom - chart.h * g / top

        for g in range(0, top + 1, step):
            pygame.draw.line(self.screen, GRID, (chart.x, py(g)), (chart.right, py(g)), 1)
            self.text(f"{g}", self.f_tiny, TEXT_DIM, (chart.x - 8, py(g)), "midright")
        # The greenest window of each day, shaded behind the bars
        for _, w in days:
            self.card(pygame.Rect(round(px(w["start"])), chart.y - 6, round(px(w["end"]) - px(w["start"])),
                                  chart.h + 6), BG_CARD_HI, radius=4)
        # Midnights and every 6 hours along the bottom; day names at midnight
        local0 = t0.astimezone(ed.LOCAL)
        t = local0.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        while t < t1:
            if t.hour % 6 == 0:
                x = px(t)
                if t.hour == 0:
                    pygame.draw.line(self.screen, TEXT_DIM, (x, chart.y - 26), (x, chart.bottom), 1)
                    if x > chart.x + 40:                # clear of the "now" label
                        self.text(f"{t:%a}", self.f_smallb, TEXT_SEC, (x + 4, chart.y - 30))
                self.text(f"{t:%H}", self.f_tiny, TEXT_DIM, (x, chart.bottom + 6), "midtop")
            t += timedelta(hours=1)
        bw = chart.w / 96
        for p in ps:
            x = px(p["start"])
            col = band_colour(p["index"] or ed.band(p["forecast"], periods))
            pygame.draw.rect(self.screen, col, (round(x), round(py(p["forecast"])), max(1, round(bw) - 1),
                                                chart.bottom - round(py(p["forecast"]))))
        pygame.draw.line(self.screen, TEXT_PRI, (chart.x, chart.y - 26), (chart.x, chart.bottom), 2)
        self.text("now", self.f_tiny, TEXT_PRI, (chart.x + 4, chart.y - 28))

        # Greenest window each day, and the one to avoid
        y = chart.bottom + 34
        self.text(f"Greenest {self.hours_label}", self.f_small, TEXT_SEC, (PAD, y))
        row_h = 58
        rows = [(day_label(day, now), w, False) for day, w in days[:3]]
        worst = ed.best_window(periods, now, self.window_hours, worst=True)
        if worst:
            rows.append(("Avoid", worst, True))
        y += 28
        for i, (label, w, bad) in enumerate(rows):
            r = pygame.Rect(PAD - 6, y + i * row_h, SCREEN_W - 2 * PAD + 12, row_h - 6)
            if r.bottom > TABS_Y - 4:
                break
            self.card(r, BG_CARD, radius=10)
            col = band_colour(w["band"])
            if bad:
                self.text(label, self.f_medb, BAND_COLOURS["high"], (PAD + 8, r.centery - 9), "midleft")
                self.text("next 24 h", self.f_tiny, TEXT_SEC, (PAD + 8, r.centery + 13), "midleft")
            else:
                self.text(label, self.f_medb, TEXT_PRI, (PAD + 8, r.centery), "midleft")
            self.text(span(w), self.f_medb, TEXT_PRI, (182, r.centery), "midleft")
            self.text(f"{w['avg']:.0f} g", self.f_medb, col, (SCREEN_W - PAD - 8, r.centery), "midright")

    # ── Drawing a frame ───────────────────────────────────────────────────────

    def draw(self, now=None):
        now = now or datetime.now(ed.LOCAL)
        self.buttons = {}
        self.screen.fill(BG)
        self.draw_status_bar(now)
        self.draw_places()
        {"Now": self.draw_now, "Mix": self.draw_mix, "Forecast": self.draw_forecast}[self.page](now)
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
        elif hit and hit.startswith("place"):
            self.place_i = int(hit[5:])
        elif hit and hit.startswith("page:"):
            self.page = hit[5:]

    def tick(self):
        """Idle bookkeeping: dimming, and cycling pages if cycle_seconds is set."""
        idle = time.time() - self.last_touch
        self.dimmed = idle > DIM_AFTER
        if self.cycle_seconds and idle > CYCLE_IDLE and time.time() - self.last_cycle > self.cycle_seconds:
            self.last_cycle = time.time()
            self.turn_page(1)

    # ── Main loop ─────────────────────────────────────────────────────────────

    def state(self, now):
        """Everything on screen depends on: what's selected, dimming, the minute and the data."""
        return (self.place_i, self.page, self.dimmed, self.sleeping, now.strftime("%Y-%m-%d %H:%M"),
                (self.store.get(self.place) or {}).get("fetched"), self.store.error(self.place))

    def frame(self, now=None):
        """Redraw only if something on screen would change (a Pi 3 at 10 FPS runs warm).
        Returns True if it drew."""
        now = now or datetime.now(ed.LOCAL)
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
    cfg = ed.load_config()
    store = ed.Store(cfg["places"]).start()
    EnergyDisplay(cfg, store).run()
