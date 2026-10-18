#!/usr/bin/env python3
"""
FPL Live Display
A Fantasy Premier League team, fullscreen on the HyperPixel 4.0 (480x800 portrait).

Pages run along the bottom: Live (the team on a pitch with this gameweek's points as they
come in), Leagues (the team's mini-league tables; tap a league along the top to switch) and
Next (the next deadline, injury news and five gameweeks of fixtures). Swipe left/right to
change page too. Data is fetched and cached in the background (fpl_data.Store); the dot
top right is green when it's fresh.
"""

import os
import sys
import time
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "..", "common"))
import fpl_data as fd  # noqa: E402

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
FLASH = 180                 # seconds a player's new points stay highlighted
HOME_REQUEST = "/run/pi3-touch/request-home"   # Pi3 Touch launcher picks this up
FONT_DIR = os.environ.get("PI3_FONT_DIR", "/usr/share/fonts/truetype/dejavu")
PAGES = ["Live", "Leagues", "Next"]

# ── Colours ───────────────────────────────────────────────────────────────────
BG          = (10,  16,  26)
BG_CARD     = (22,  32,  48)
BG_CARD_HI  = (36,  52,  76)
TEXT_PRI    = (238, 242, 248)
TEXT_SEC    = (150, 165, 185)
TEXT_DIM    = (88,  102, 122)
ACCENT      = (0,   255, 135)   # FPL green
PURPLE      = (55,  0,   60)    # FPL purple
GOLD        = (250, 204, 70)
OK_GREEN    = (80,  200, 120)
STALE       = (230, 170, 60)
BAD_RED     = (225, 70,  80)
PITCH       = ((22, 74, 48), (26, 82, 53))     # mown stripes
PITCH_LINE  = (64, 120, 90)

# Shirts by team short name: (body, sleeves, stripes or None). Unknown teams get grey.
KITS = {
    "ARS": ((219, 0, 7), (255, 255, 255), None), "AVL": ((122, 0, 60), (149, 191, 229), None),
    "BOU": ((218, 41, 28), (20, 20, 20), (20, 20, 20)), "BRE": ((227, 6, 19), (227, 6, 19), (255, 255, 255)),
    "BHA": ((0, 87, 184), (0, 87, 184), (255, 255, 255)), "BUR": ((108, 29, 69), (153, 214, 234), None),
    "CHE": ((3, 70, 148), (3, 70, 148), None), "COV": ((119, 196, 236), (119, 196, 236), None),
    "CRY": ((27, 69, 143), (27, 69, 143), (196, 18, 46)), "EVE": ((0, 51, 153), (0, 51, 153), None),
    "FUL": ((245, 245, 245), (20, 20, 20), None), "HUL": ((245, 160, 30), (20, 20, 20), None),
    "IPS": ((0, 60, 140), (0, 60, 140), None), "LEE": ((245, 245, 245), (29, 66, 138), None),
    "LEI": ((0, 83, 160), (0, 83, 160), None), "LIV": ((200, 16, 46), (200, 16, 46), None),
    "LUT": ((247, 143, 30), (0, 32, 91), None), "MCI": ((108, 171, 221), (108, 171, 221), None),
    "MUN": ((218, 41, 28), (218, 41, 28), None), "NEW": ((245, 245, 245), (20, 20, 20), (20, 20, 20)),
    "NFO": ((221, 0, 0), (221, 0, 0), None), "SHU": ((238, 39, 55), (238, 39, 55), (255, 255, 255)),
    "SOU": ((215, 25, 32), (215, 25, 32), (255, 255, 255)), "SUN": ((235, 23, 43), (20, 20, 20), (255, 255, 255)),
    "TOT": ((245, 245, 245), (19, 34, 87), None), "WHU": ((122, 38, 58), (27, 177, 231), None),
    "WOL": ((253, 185, 19), (253, 185, 19), None), "NOR": ((255, 242, 0), (0, 166, 80), None),
    "WAT": ((251, 238, 35), (237, 33, 39), None), "MID": ((227, 6, 19), (227, 6, 19), None),
}
GREY_KIT = ((120, 130, 145), (120, 130, 145), None)
# FPL's fixture difficulty colours, 1 (easiest) to 5
FDR = {1: ((55, 85, 35), TEXT_PRI), 2: ((1, 252, 122), (20, 20, 20)), 3: ((231, 231, 231), (20, 20, 20)),
       4: ((255, 23, 81), TEXT_PRI), 5: ((128, 7, 45), TEXT_PRI)}
STATUS_COLOURS = {"live": BAD_RED, "bonus": GOLD, "waiting": TEXT_SEC, "done": TEXT_SEC}

# ── Layout constants (portrait 480x800) ───────────────────────────────────────
PAD     = 16
BODY_Y  = 58            # page content
TABS_Y  = 736           # page tabs
TABS_H  = SCREEN_H - TABS_Y
TOKEN_W = 88            # a player on the pitch
BENCH_H = 52


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
    """"3d 04h" / "5h 12m" / "12m" for a timedelta."""
    mins = max(0, int(delta.total_seconds() // 60))
    if mins >= 24 * 60:
        return f"{mins // 1440}d {mins % 1440 // 60:02d}h"
    return f"{mins // 60}h {mins % 60:02d}m" if mins >= 60 else f"{mins}m"


def rank_text(n):
    return f"{n:,}" if n else "–"


def money(tenths):
    return f"£{tenths / 10:.1f}m" if tenths is not None else "–"


def kit(short):
    return KITS.get(short, GREY_KIT)


class FplDisplay:
    def __init__(self, config, store, screen=None):
        self.config = config
        self.cycle_seconds = config.get("cycle_seconds", 0)
        self.store = store

        if screen is None:
            self.display = pgscreen.Display(SCREEN_W, SCREEN_H, "FPL Live")
            screen = self.display.surface
        else:
            self.display = None
            pygame.font.init()
        self.screen = screen

        self.f_huge  = load_font(64, bold=True)
        self.f_big   = load_font(34, bold=True)
        self.f_large = load_font(26, bold=True)
        self.f_med   = load_font(20)
        self.f_medb  = load_font(20, bold=True)
        self.f_small = load_font(16)
        self.f_smallb = load_font(16, bold=True)
        self.f_tiny  = load_font(13)
        self.f_tinyb = load_font(13, bold=True)
        self.f_name  = load_font(13, bold=True)
        self.f_pts   = load_font(17, bold=True)
        self.f_clock = load_font(28, bold=True)

        self.page = "Live"
        self.league_i = 0
        self.last_touch = time.time()
        self.last_cycle = time.time()
        self.dimmed = False
        self.sleeping = False
        self.down = None            # where the current touch started
        self.buttons = {}           # name -> Rect, rebuilt every frame
        self.cache = {}             # stamp -> computed team
        self.seen = {}              # player id -> points last seen (for the flash on new points)
        self.flash = {}             # player id -> (points gained, when)
        self.drawn = None           # state() of the frame on screen

    # ── Helpers ───────────────────────────────────────────────────────────────

    def text(self, s, font, colour, pos, anchor="topleft"):
        surf = font.render(s, True, colour)
        rect = surf.get_rect(**{anchor: pos})
        self.screen.blit(surf, rect)
        return rect

    def card(self, rect, colour=BG_CARD, radius=14):
        pygame.draw.rect(self.screen, colour, rect, border_radius=radius)

    def team(self, now):
        """(squad, totals, phase, gameweek) for the latest gameweek, or None - worked out once
        per new data (and per minute, for the kick-off times)."""
        static, picks, live = self.store.get("static"), self.store.get("picks"), self.store.get("live")
        if not (static and picks):
            return None
        key = (self.store.fetched("static"), self.store.fetched("picks"), self.store.fetched("live"),
               now.strftime("%Y%m%d%H%M"))
        if key not in self.cache:
            live = live if live and live["event"] == picks["event"] else None
            sq = fd.squad(static, picks, live, now)
            self.cache = {key: (sq, fd.totals(sq, picks), fd.phase(live["fixtures"], now) if live else None,
                                picks["event"])}
            self.note_points(sq, picks["event"])
        return self.cache[key]

    def note_points(self, squad, gw):
        """Flash players whose points went up since the last data (a goal, an assist...)."""
        now = time.time()
        for p in squad:
            k = (gw, p["id"])
            pts = p["pts"] + p["bonus"]
            if k in self.seen and pts > self.seen[k]:
                self.flash[p["id"]] = (pts - self.seen[k], now)
            self.seen[k] = pts
        self.flash = {k: v for k, v in self.flash.items() if now - v[1] < FLASH}

    def flashing(self):
        now = time.time()
        return {k: v[0] for k, v in self.flash.items() if now - v[1] < FLASH}

    def leagues(self):
        return fd.leagues_to_show(self.store.get("entry"), self.config)

    # ── Chrome: status bar, page tabs ────────────────────────────────────────

    def draw_status_bar(self, now):
        self.text(now.strftime("%H:%M"), self.f_clock, TEXT_PRI, (PAD, 12))

        t = self.store.fetched("live") or self.store.fetched("static")
        if t:
            self.text(f"updated {datetime.fromtimestamp(t):%H:%M}", self.f_tiny, TEXT_DIM, (PAD + 92, 24))
        err = self.store.error()
        dot = BAD_RED if not t else STALE if err else OK_GREEN
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
            self.text(p, self.f_medb if sel else self.f_med, ACCENT if sel else TEXT_SEC, r.center, "center")
            self.buttons[f"page:{p}"] = r

    def draw_message(self, line1, line2=""):
        cy = (BODY_Y + TABS_Y) // 2
        self.text(line1, self.f_med, TEXT_SEC, (SCREEN_W // 2, cy - 16), "center")
        if line2:
            self.text(line2, self.f_small, TEXT_DIM, (SCREEN_W // 2, cy + 18), "center")

    def draw_waiting(self, what="your team"):
        err = self.store.error()
        self.draw_message(f"Fetching {what} from FPL…", f"({err} - retrying)" if err else "")

    # ── Page: Live ────────────────────────────────────────────────────────────

    def draw_shirt(self, cx, top, short, faded=False):
        """A football shirt, 44 wide and 38 tall, in the team's colours."""
        body, sleeve, stripe = kit(short)
        if faded:
            body, sleeve, stripe = (70, 80, 92), (70, 80, 92), None
        s = pygame.Surface((44, 38), pygame.SRCALPHA)
        pygame.draw.polygon(s, sleeve, [(0, 9), (11, 1), (13, 15), (4, 19)])           # left sleeve
        pygame.draw.polygon(s, sleeve, [(44, 9), (33, 1), (31, 15), (40, 19)])         # right sleeve
        pygame.draw.polygon(s, body, [(11, 1), (17, 0), (22, 4), (27, 0), (33, 1), (33, 38), (11, 38)])
        if stripe:
            for x in (15, 22, 29):
                pygame.draw.rect(s, stripe, (x - 2, 3 if x != 22 else 5, 4, 35))
        pygame.draw.polygon(s, (0, 0, 0, 90), [(17, 0), (22, 4), (27, 0), (22, 7)])   # collar
        self.screen.blit(s, (cx - 22, top))

    def draw_badge(self, cx, cy, label, fill, colour=TEXT_PRI, font=None):
        font = font or self.f_tinyb
        w = max(20, font.size(label)[0] + 10)
        r = pygame.Rect(0, 0, w, 20)
        r.center = (cx, cy)
        pygame.draw.rect(self.screen, fill, r, border_radius=10)
        self.text(label, font, colour, r.center, "center")

    def draw_token(self, p, cx, top, flash):
        """One player: shirt, name plate, points plate and his game underneath."""
        out = p["sub"] == "out"
        self.draw_shirt(cx, top, p["short"], faded=out)
        if p["captain"] or p["vice"]:
            self.draw_badge(cx + 26, top + 8, "C" if p["captain"] else "V",
                            (20, 20, 20) if p["captain"] else (60, 66, 78))
        if p["id"] in flash:
            self.draw_badge(cx - 26, top + 8, f"+{flash[p['id']]}", GOLD, (20, 20, 20))
        elif p["sub"]:
            self.draw_badge(cx - 26, top + 8, "IN" if p["sub"] == "in" else "OUT",
                            ACCENT if p["sub"] == "in" else BAD_RED, (20, 20, 20) if p["sub"] == "in" else TEXT_PRI)

        name = pygame.Rect(cx - TOKEN_W // 2, top + 40, TOKEN_W, 20)
        pygame.draw.rect(self.screen, (238, 242, 248) if not out else (110, 120, 134), name,
                         border_top_left_radius=6, border_top_right_radius=6)
        self.text(truncate(p["name"], self.f_name, TOKEN_W - 6), self.f_name, (20, 24, 32), name.center, "center")

        plate = pygame.Rect(name.x, name.bottom, TOKEN_W, 22)
        playing = p["state"] == "playing"
        pygame.draw.rect(self.screen, ACCENT if playing else PURPLE, plate,
                         border_bottom_left_radius=6, border_bottom_right_radius=6)
        if p["state"] == "to play" and p["minutes"] == 0 and not p["pts"]:
            pts, col = "–", TEXT_SEC
        else:
            pts, col = str(p["shown"] if p["mult"] else p["pts"] + p["bonus"]), (20, 20, 20) if playing else TEXT_PRI
        r = self.text(pts, self.f_pts, col, plate.center, "center")
        if p["bonus"]:
            self.text(f"+{p['bonus']}b" if not p["mult"] else f"({p['bonus']}b)", self.f_tiny,
                      (60, 40, 0) if playing else GOLD, (r.right + 3, plate.centery), "midleft")
        self.text(truncate(p["label"], self.f_tiny, TOKEN_W + 8), self.f_tiny,
                  ACCENT if playing else TEXT_SEC, (cx, plate.bottom + 2), "midtop")

    def draw_pitch(self, rect):
        stripes = 8
        for i in range(stripes):
            y0 = rect.y + rect.h * i // stripes
            y1 = rect.y + rect.h * (i + 1) // stripes
            pygame.draw.rect(self.screen, PITCH[i % 2], (rect.x, y0, rect.w, y1 - y0))
        inner = rect.inflate(-16, -12)
        pygame.draw.rect(self.screen, PITCH_LINE, inner, 2)
        box = pygame.Rect(0, inner.y, 220, 70)
        box.centerx = inner.centerx
        pygame.draw.rect(self.screen, PITCH_LINE, box, 2)
        six = pygame.Rect(0, inner.y, 100, 26)
        six.centerx = inner.centerx
        pygame.draw.rect(self.screen, PITCH_LINE, six, 2)
        pygame.draw.arc(self.screen, PITCH_LINE, (inner.centerx - 60, inner.bottom - 60, 120, 120), 0, 3.1416, 2)

    def draw_live(self, now):
        t = self.team(now)
        if not t:
            return self.draw_waiting()
        squad, tot, ph, gw = t
        static = self.store.get("static")
        ev = fd.event(static, gw) or {}
        entry = self.store.get("entry") or {}

        # Header: gameweek, status, points, average, ranks
        r = pygame.Rect(PAD, BODY_Y, SCREEN_W - 2 * PAD, 104)
        self.card(r)
        self.text(f"Gameweek {gw}", self.f_medb, TEXT_PRI, (r.x + 14, r.y + 10))
        live = self.store.get("live")
        if ph == "live":
            label = "LIVE"
        elif ph == "bonus":
            label = "Bonus due"
        elif ph == "waiting":
            k = fd.next_kickoff(live["fixtures"], now)
            label = f"Next game {k.astimezone(fd.LOCAL):%a %H:%M}" if k else "Waiting"
        elif ph == "done":
            label = "Final" if ev.get("checked") else "Provisional"
        else:
            label = ""
        if label:
            col = STATUS_COLOURS.get(ph, TEXT_SEC)
            if ph == "live":
                lr = self.text(label, self.f_smallb, col, (r.right - 14, r.y + 12), "topright")
                pygame.draw.circle(self.screen, col, (lr.x - 10, lr.centery), 5)
            else:
                self.text(label, self.f_small, col, (r.right - 14, r.y + 12), "topright")
        nr = self.text(str(tot["points"]), self.f_huge, ACCENT, (r.x + 10, r.y + 32))
        self.text("pts", self.f_medb, TEXT_SEC, (nr.right + 6, nr.y + 12))
        playing = f"{tot['to_play']} to play" if tot["to_play"] else "all played"
        self.text(f"−{tot['hit']} hit · {playing}" if tot["hit"] else playing, self.f_small, TEXT_SEC,
                  (nr.right + 6, nr.y + 40))
        x = r.x + 236
        rows = [("Average", str(ev.get("average") or "–")), ("Highest", str(ev.get("highest") or "–")),
                ("Overall", rank_text(entry.get("rank")))]
        for i, (k, v) in enumerate(rows):
            y = r.y + 38 + i * 21
            self.text(k, self.f_small, TEXT_SEC, (x, y))
            vr = self.text(v, self.f_smallb, TEXT_PRI, (r.right - 14, y), "topright")
            if k == "Overall":
                self.draw_rank_arrow(vr.x - 12, vr.centery, self.rank_move())

        # The pitch: starting XI (with auto-subs in place) in rows by position
        pitch = pygame.Rect(PAD, r.bottom + 8, SCREEN_W - 2 * PAD, TABS_Y - r.bottom - 8 - BENCH_H - 12)
        self.draw_pitch(pitch)
        flash = self.flashing()
        on = [p for p in squad if (p["slot"] <= 11 and p["sub"] != "out") or p["sub"] == "in"]
        if (self.store.get("picks") or {}).get("chip") == "bboost":
            on = [p for p in squad if p["slot"] <= 11]
        row_h = pitch.h // 4
        for pos in (1, 2, 3, 4):
            line = [p for p in on if p["pos"] == pos]
            if not line:
                continue
            step = min(TOKEN_W + 4, (pitch.w - 8) // len(line))
            x0 = pitch.centerx - step * (len(line) - 1) / 2
            for i, p in enumerate(line):
                self.draw_token(p, round(x0 + i * step), pitch.y + (pos - 1) * row_h + (row_h - 98) // 2, flash)

        # The bench, in order (players auto-subbed off join it, faded)
        b = pygame.Rect(PAD, pitch.bottom + 6, SCREEN_W - 2 * PAD, BENCH_H)
        self.card(b, BG_CARD, radius=10)
        chip = (self.store.get("picks") or {}).get("chip")
        bench = [p for p in squad if (p["slot"] > 11 and p["sub"] != "in") or p["sub"] == "out"]
        title = {"bboost": "Bench Boost", "3xc": "Triple Captain", "freehit": "Free Hit",
                 "wildcard": "Wildcard"}.get(chip, "Bench")
        self.text(title, self.f_tinyb, GOLD if chip else TEXT_SEC, (b.x + 10, b.y + 6))
        self.text(f"{tot['bench']} pts", self.f_tiny, TEXT_DIM, (b.x + 10, b.y + 24))
        x0 = b.x + 96
        step = (b.right - 6 - x0) / max(1, len(bench))
        for i, p in enumerate(bench):
            x, right = round(x0 + i * step), round(x0 + (i + 1) * step) - 10
            if i:
                pygame.draw.line(self.screen, BG, (x - 6, b.y + 8), (x - 6, b.bottom - 8), 2)
            col = BAD_RED if p["sub"] == "out" else TEXT_PRI if p["state"] != "to play" else TEXT_SEC
            self.text(truncate(p["name"], self.f_tinyb, right - x), self.f_tinyb, col, (x, b.y + 7))
            pts = "–" if p["state"] == "to play" and not p["pts"] else str(p["pts"] + p["bonus"])
            pr = self.text(pts, self.f_smallb, col, (right, b.y + 25), "topright")
            self.text(truncate(p["label"], self.f_tiny, pr.x - 4 - x), self.f_tiny, TEXT_DIM, (x, b.y + 27))

    def rank_move(self):
        """+1 if the overall rank is better than after last gameweek, -1 if worse, 0 if not known."""
        entry, hist = self.store.get("entry"), self.store.get("history")
        if not (entry and hist and entry.get("rank")):
            return 0
        gw = (self.store.get("picks") or {}).get("event")
        before = [h["rank"] for h in hist if gw and h["event"] < gw and h["rank"]]
        if not before:
            return 0
        return (entry["rank"] < before[-1]) - (entry["rank"] > before[-1])

    def draw_rank_arrow(self, cx, cy, move):
        if move > 0:
            pygame.draw.polygon(self.screen, OK_GREEN, [(cx - 6, cy + 4), (cx + 6, cy + 4), (cx, cy - 5)])
        elif move < 0:
            pygame.draw.polygon(self.screen, BAD_RED, [(cx - 6, cy - 4), (cx + 6, cy - 4), (cx, cy + 5)])

    # ── Page: Leagues ─────────────────────────────────────────────────────────

    def draw_leagues(self, now):
        leagues = self.leagues()
        if not leagues:
            return self.draw_waiting("your leagues") if not self.store.get("entry") else \
                self.draw_message("No mini-leagues", "Join one, or list league ids in fpl.json")
        self.league_i = min(self.league_i, len(leagues) - 1)
        gap = 8
        w = (SCREEN_W - 2 * PAD - gap * (len(leagues) - 1)) / len(leagues)
        for i, lg in enumerate(leagues):
            r = pygame.Rect(round(PAD + i * (w + gap)), BODY_Y, round(w), 46)
            sel = i == self.league_i
            self.card(r, ACCENT if sel else BG_CARD, radius=12)
            label, font = self.short_name(lg["name"], r.w - 12)
            self.text(label, font, (8, 26, 16) if sel else TEXT_SEC, r.center, "center")
            self.buttons[f"league{i}"] = r

        lg = leagues[self.league_i]
        table = self.store.get(f"league:{lg['id']}")
        if not table:
            return self.draw_waiting(lg["name"])
        y = BODY_Y + 62
        me = next((row for row in table["rows"] if row["me"]), None)
        self.text(truncate(table["name"], self.f_medb, 300), self.f_medb, TEXT_PRI, (PAD, y))
        if me:
            self.text(f"{me['rank']} of {lg.get('size') or len(table['rows'])}", self.f_medb, ACCENT,
                      (SCREEN_W - PAD, y), "topright")
        upd = fd.api_time(table["updated"]) if table.get("updated") else None
        if upd:
            self.text(f"FPL table, updated {upd.astimezone(fd.LOCAL):%a %H:%M}", self.f_tiny, TEXT_DIM,
                      (PAD, y + 28))
        self.text("GW", self.f_tinyb, TEXT_DIM, (SCREEN_W - PAD - 76, y + 28), "topright")
        self.text("Total", self.f_tinyb, TEXT_DIM, (SCREEN_W - PAD - 8, y + 28), "topright")

        row_h = 46
        top = y + 50
        fits = (TABS_Y - 6 - top) // row_h
        for i, row in enumerate(self.table_rows(table["rows"], fits)):
            ry = top + i * row_h
            if row is None:
                self.text("⋯", self.f_med, TEXT_DIM, (SCREEN_W // 2, ry + row_h // 2), "center")
                continue
            rr = pygame.Rect(PAD - 6, ry, SCREEN_W - 2 * PAD + 12, row_h - 4)
            if row["me"]:
                self.card(rr, BG_CARD_HI, radius=10)
                pygame.draw.rect(self.screen, ACCENT, (rr.x, rr.y + 6, 4, rr.h - 12), border_radius=2)
            elif i % 2 == 0:
                self.card(rr, BG_CARD, radius=10)
            self.text(str(row["rank"]), self.f_medb, TEXT_PRI, (PAD + 34, rr.centery), "midright")
            move = (row["last"] - row["rank"]) if row.get("last") else 0
            self.draw_rank_arrow(PAD + 48, rr.centery, move)
            nx = PAD + 62
            self.text(truncate(row["team"], self.f_smallb, 250), self.f_smallb, TEXT_PRI, (nx, rr.y + 4))
            self.text(truncate(row["manager"], self.f_tiny, 250), self.f_tiny, TEXT_SEC, (nx, rr.y + 23))
            self.text(str(row["gw"] if row["gw"] is not None else "–"), self.f_med, TEXT_SEC,
                      (SCREEN_W - PAD - 76, rr.centery), "midright")
            self.text(str(row["total"]), self.f_medb, TEXT_PRI, (SCREEN_W - PAD - 8, rr.centery), "midright")

    def short_name(self, name, width):
        """(label, font) for a league button: the name, else its first words, else smaller."""
        words = name.split()
        for n in range(len(words), 0, -1):
            label = " ".join(words[:n])
            if self.f_smallb.size(label)[0] <= width:
                return label, self.f_smallb
        return truncate(name, self.f_tinyb, width), self.f_tinyb

    @staticmethod
    def table_rows(rows, fits):
        """The rows to show when they don't all fit: the top three, a gap (None), then the
        team's own neighbourhood."""
        if len(rows) <= fits:
            return rows
        me = next((i for i, r in enumerate(rows) if r["me"]), None)
        if me is None or me < fits - 2:
            return rows[:fits]
        around = fits - 4                    # top three and the gap
        start = max(3, min(me - around // 2, len(rows) - around))
        return rows[:3] + [None] + rows[start:start + around]

    # ── Page: Next ────────────────────────────────────────────────────────────

    def draw_next(self, now):
        static, picks = self.store.get("static"), self.store.get("picks")
        if not static:
            return self.draw_waiting()
        _, nxt = fd.gameweeks(static, now)
        y = BODY_Y

        # Deadline countdown
        r = pygame.Rect(PAD, y, SCREEN_W - 2 * PAD, 96)
        self.card(r)
        if nxt:
            dl = fd.api_time(nxt["deadline"]).astimezone(fd.LOCAL)
            left = dl - now
            self.text(f"{nxt['name']} deadline", self.f_small, TEXT_SEC, (r.x + 14, r.y + 12))
            self.text(f"{dl:%a %d %b, %H:%M}", self.f_large, TEXT_PRI, (r.x + 14, r.y + 40))
            col = BAD_RED if left < timedelta(hours=6) else GOLD if left < timedelta(hours=24) else ACCENT
            self.text(until(left), self.f_big, col, (r.right - 14, r.y + 36), "topright")
            if picks:
                self.text(f"bank {money(picks.get('bank'))} · value {money(picks.get('value'))}", self.f_tiny,
                          TEXT_DIM, (r.right - 14, r.y + 12), "topright")
        else:
            self.text("No more deadlines this season", self.f_med, TEXT_SEC, r.center, "center")
        y = r.bottom + 10
        if not picks:
            return

        # Squad news: flags and price moves
        flags = fd.flagged(static, picks)
        moves = [static["players"][str(p["id"])] for p in picks["picks"]
                 if static["players"].get(str(p["id"]), {}).get("change")]
        lines = len(flags) + (1 if moves else 0)
        r = pygame.Rect(PAD, y, SCREEN_W - 2 * PAD, 40 + max(1, min(lines, 4)) * 26)
        self.card(r)
        self.text("Squad news", self.f_small, TEXT_SEC, (r.x + 14, r.y + 10))
        ly = r.y + 38
        if not lines:
            self.text("All fifteen fit, no price changes", self.f_small, TEXT_PRI, (r.x + 14, ly))
        for p, chance in flags[:4 if not moves else 3]:
            col = BAD_RED if (chance or 0) <= 25 else STALE if chance <= 50 else GOLD
            self.draw_badge(r.x + 34, ly + 9, f"{chance}%" if chance is not None else "?", col, (20, 20, 20))
            nr = self.text(p["name"], self.f_smallb, TEXT_PRI, (r.x + 62, ly))
            news = p["news"].split(" - ")[0] if p["news"] else ""
            self.text(truncate(news, self.f_tiny, r.right - 14 - nr.right - 10), self.f_tiny, TEXT_SEC,
                      (nr.right + 10, ly + 3))
            ly += 26
        if moves:
            words = ", ".join(f"{p['name']} {'▲' if p['change'] > 0 else '▼'}{money(p['cost'])}" for p in moves)
            self.text(truncate(f"Price: {words}", self.f_small, r.w - 28), self.f_small, TEXT_PRI, (r.x + 14, ly))
        y = r.bottom + 10

        # Fixtures for the next five gameweeks, by player
        future = self.store.get("future")
        if not (future and nxt):
            return
        r = pygame.Rect(PAD, y, SCREEN_W - 2 * PAD, TABS_Y - 8 - y)
        self.card(r)
        weeks = 5
        x0, cw = r.x + 108, (r.w - 108 - 10) / weeks
        for i in range(weeks):
            self.text(f"GW{nxt['id'] + i}", self.f_tinyb, TEXT_DIM, (x0 + i * cw + cw / 2, r.y + 8), "midtop")
        players = sorted(picks["picks"], key=lambda pk: (pk["slot"] > 11, static["players"].get(
            str(pk["id"]), {}).get("pos", 3), pk["slot"]))
        row_h = min(26, (r.h - 34) // len(players))
        for j, pk in enumerate(players):
            p = static["players"].get(str(pk["id"]))
            if not p:
                continue
            ry = r.y + 30 + j * row_h
            self.text(truncate(p["name"], self.f_tinyb, 94), self.f_tinyb,
                      TEXT_PRI if pk["slot"] <= 11 else TEXT_SEC, (r.x + 10, ry + row_h // 2), "midleft")
            for i, (gw, games) in enumerate(fd.difficulty(future, p["team"], nxt["id"], weeks)):
                cell = pygame.Rect(round(x0 + i * cw + 1), ry + 1, round(cw) - 2, row_h - 2)
                if not games:
                    pygame.draw.rect(self.screen, BG, cell, border_radius=4)
                    continue
                part = cell.w / len(games)
                for k, (opp, home, diff) in enumerate(games):
                    fill, ink = FDR.get(diff, FDR[3])
                    c = pygame.Rect(round(cell.x + k * part), cell.y, round(part) - (1 if k < len(games) - 1 else 0),
                                    cell.h)
                    pygame.draw.rect(self.screen, fill, c, border_radius=4)
                    name = static["teams"].get(str(opp), {}).get("short", "?")
                    name = name if home else name.lower()
                    if len(games) > 1:
                        name = name[:2]
                    self.text(name, self.f_tinyb, ink, c.center, "center")

    # ── Drawing a frame ───────────────────────────────────────────────────────

    def draw(self, now=None):
        now = now or datetime.now(fd.LOCAL)
        self.buttons = {}
        self.screen.fill(BG)
        self.draw_status_bar(now)
        {"Live": self.draw_live, "Leagues": self.draw_leagues, "Next": self.draw_next}[self.page](now)
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
        elif hit and hit.startswith("league"):
            self.league_i = int(hit[6:])
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
        """Everything on screen depends on: what's selected, dimming, the minute, the data and
        which players are flashing."""
        return (self.page, self.league_i, self.dimmed, self.sleeping, now.strftime("%Y-%m-%d %H:%M"),
                self.store.stamp(), self.store.error(), tuple(sorted(self.flashing())))

    def frame(self, now=None):
        """Redraw only if something on screen would change (a Pi 3 at 10 FPS runs warm).
        Returns True if it drew."""
        now = now or datetime.now(fd.LOCAL)
        self.tick()
        self.team(now)                      # notices new points even while another page shows
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
    cfg = fd.load_config()
    store = fd.Store(cfg).start()
    FplDisplay(cfg, store).run()
