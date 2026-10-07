#!/usr/bin/env python3
"""
Pi-hole Display
What the network's Pi-hole is blocking, fullscreen on the HyperPixel 4.0 (480x800 portrait).

A strip along the top shows whether blocking is on, with a Pause / Resume button (pause for
30 s, 5 min, 30 min or an hour - the Pi-hole turns blocking back on by itself). Pages run
along the bottom: Now (today's numbers and the last 24 hours), Top (most blocked and allowed
domains, busiest devices) and Live (the latest lookups). Swipe left/right to change page too.
The Pi-hole is polled in the background (pihole_data.Monitor); the dot top right is green
while it answers.
"""

import math
import os
import signal
import sys
import time
from datetime import datetime

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "..", "common"))
import pihole_data as pd  # noqa: E402

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
PAGES = ["Now", "Top", "Live"]
LISTS = [("blocked", "Blocked"), ("allowed", "Allowed"), ("clients", "Devices")]

# ── Colours ───────────────────────────────────────────────────────────────────
BG          = (10,  16,  26)
BG_CARD     = (22,  32,  48)
BG_CARD_HI  = (36,  52,  76)
TEXT_PRI    = (238, 242, 248)
TEXT_SEC    = (150, 165, 185)
TEXT_DIM    = (88,  102, 122)
ACCENT      = (96,  170, 240)   # blue: tabs, cached
GRID        = (36,  50,  70)
OK_GREEN    = (80,  200, 120)
STALE       = (230, 170, 60)
BAD_RED     = (200, 70,  70)
BLOCK_RED   = (232, 82,  96)
ALLOW_GREEN = (80,  196, 120)
KIND_COLOURS = {"blocked": BLOCK_RED, "allowed": ALLOW_GREEN, "cached": ACCENT}

# ── Layout constants (portrait 480x800) ───────────────────────────────────────
PAD     = 16
STRIP_Y = 58            # blocking status + Pause button
STRIP_H = 50
BODY_Y  = 124           # page content
TABS_Y  = 736           # page tabs
TABS_H  = SCREEN_H - TABS_Y


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


def pause_label(seconds):
    """30 -> "30 seconds", 300 -> "5 minutes", 3600 -> "1 hour"."""
    if seconds % 3600 == 0:
        n, unit = seconds // 3600, "hour"
    elif seconds % 60 == 0:
        n, unit = seconds // 60, "minute"
    else:
        n, unit = seconds, "second"
    return f"{n} {unit}{'s' if n != 1 else ''}"


def clock(seconds):
    """Remaining time as "4:32" or "1:02:15"."""
    s = max(0, int(math.ceil(seconds)))
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


class PiholeDisplay:
    def __init__(self, config, monitor, screen=None):
        self.monitor = monitor
        self.cycle_seconds = config.get("cycle_seconds", 0)
        self.pause_choices = config.get("pause_choices", [30, 300, 1800, 3600])

        if screen is None:
            self.display = pgscreen.Display(SCREEN_W, SCREEN_H, "Pi-hole")
            screen = self.display.surface
        else:
            self.display = None
            pygame.font.init()
        self.screen = screen

        self.f_big   = load_font(40, bold=True)
        self.f_large = load_font(30, bold=True)
        self.f_med   = load_font(22)
        self.f_medb  = load_font(22, bold=True)
        self.f_small = load_font(18)
        self.f_smallb = load_font(18, bold=True)
        self.f_tiny  = load_font(14)
        self.f_clock = load_font(28, bold=True)

        self.page = "Now"
        self.top_list = "blocked"
        self.choosing = False        # the Pause choices are open
        self.last_touch = time.time()
        self.last_cycle = time.time()
        self.dimmed = False
        self.sleeping = False
        self.down = None             # where the current touch started
        self.buttons = {}            # name -> Rect, rebuilt every frame
        self.drawn = None            # state() of the frame on screen

    # ── Helpers ───────────────────────────────────────────────────────────────

    def text(self, s, font, colour, pos, anchor="topleft"):
        surf = font.render(s, True, colour)
        rect = surf.get_rect(**{anchor: pos})
        self.screen.blit(surf, rect)
        return rect

    def card(self, rect, colour=BG_CARD, radius=14):
        pygame.draw.rect(self.screen, colour, rect, border_radius=radius)

    def blocking(self):
        """("on" | "paused" | "off" | None, seconds left while paused)."""
        b = self.monitor.data("blocking")
        if not b:
            return None, None
        if b["state"] == "enabled":
            return "on", None
        if b["state"] == "disabled":
            left = b["until"] - time.time() if b["until"] else None
            return ("paused", left) if left and left > 0 else ("off", None)
        return b["state"], None

    def router(self):
        """The router's name if every lookup comes through it (see pihole_data.via_router)."""
        return pd.via_router((self.monitor.data("top") or {}).get("clients"))

    def draw_ring(self, cx, cy, r_out, r_in, parts):
        """[(fraction, colour)] round a ring from 12 o'clock, drawn at 3x and scaled down."""
        k = 3
        size = 2 * r_out * k
        ring = pygame.Surface((size, size), pygame.SRCALPHA)
        c = size / 2
        a = -90.0
        for frac, colour in parts:
            sweep = 360 * max(0.0, min(1.0, frac))
            if sweep <= 0:
                continue
            steps = max(2, int(sweep / 2))
            angles = [math.radians(a + sweep * i / steps) for i in range(steps + 1)]
            pts = [(c + math.cos(t) * r_out * k, c + math.sin(t) * r_out * k) for t in angles]
            pts += [(c + math.cos(t) * r_in * k, c + math.sin(t) * r_in * k) for t in reversed(angles)]
            pygame.draw.polygon(ring, colour, pts)
            a += sweep
        ring = pygame.transform.smoothscale(ring, (2 * r_out, 2 * r_out))
        self.screen.blit(ring, (cx - r_out, cy - r_out))

    # ── Chrome: status bar, blocking strip, page tabs ─────────────────────────

    def draw_status_bar(self, now):
        self.text(now.strftime("%H:%M"), self.f_clock, TEXT_PRI, (PAD, 12))
        err = self.monitor.error
        if err:
            self.text(err, self.f_tiny, STALE, (PAD + 92, 24))
        has_data = self.monitor.data("summary") is not None
        dot = BAD_RED if not has_data else STALE if err else OK_GREEN
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

    def draw_strip(self):
        state, left = self.blocking()
        r = pygame.Rect(PAD, STRIP_Y, SCREEN_W - 2 * PAD, STRIP_H)
        self.card(r, radius=12)
        colour, label = {"on": (ALLOW_GREEN, "Blocking on"),
                         "paused": (STALE, f"Paused · {clock(left or 0)} left"),
                         "off": (BLOCK_RED, "Blocking off")}.get(state, (TEXT_DIM, "Blocking …"))
        pygame.draw.circle(self.screen, colour, (r.x + 22, r.centery), 9)
        self.text(label, self.f_medb, colour if state else TEXT_SEC, (r.x + 42, r.centery), "midleft")
        if state in ("on", "paused", "off"):
            b = pygame.Rect(r.right - 132, r.y + 6, 126, r.h - 12)
            resume = state != "on"
            self.card(b, ALLOW_GREEN if resume else STALE, radius=10)
            self.text("Resume" if resume else "Pause", self.f_medb, (12, 20, 16), b.center, "center")
            self.buttons["resume" if resume else "pause"] = b

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
        err = self.monitor.error
        if err and "password" in err:
            return self.draw_message(f"Pi-hole: {err}", "On the Mac: bash mac/set-pihole-key.sh")
        self.draw_message("Asking the Pi-hole…", f"({err} - retrying)" if err else "")

    def draw_choices(self):
        """Pause for how long? Over the page, with the strip still showing."""
        shade = pygame.Surface((SCREEN_W, SCREEN_H - BODY_Y + 10), pygame.SRCALPHA)
        shade.fill((0, 0, 0, 170))
        self.screen.blit(shade, (0, BODY_Y - 10))
        r = pygame.Rect(PAD + 8, BODY_Y + 40, SCREEN_W - 2 * PAD - 16, 400)
        self.card(r, BG_CARD_HI, radius=18)
        self.text("Pause blocking for…", self.f_large, TEXT_PRI, (r.centerx, r.y + 22), "midtop")
        self.text("Blocking comes back on by itself", self.f_small, TEXT_SEC, (r.centerx, r.y + 64), "midtop")
        gap = 14
        bw, bh = (r.w - 40 - gap) // 2, 96
        for i, secs in enumerate(self.pause_choices[:4]):
            b = pygame.Rect(r.x + 20 + (i % 2) * (bw + gap), r.y + 104 + (i // 2) * (bh + gap), bw, bh)
            self.card(b, BG_CARD, radius=14)
            n, unit = pause_label(secs).split(" ", 1)
            self.text(n, self.f_big, STALE, (b.centerx, b.centery - 12), "center")
            self.text(unit, self.f_small, TEXT_SEC, (b.centerx, b.centery + 26), "center")
            self.buttons[f"pause:{secs}"] = b
        c = pygame.Rect(r.x + 20, r.bottom - 70, r.w - 40, 52)
        self.card(c, BG_CARD, radius=12)
        self.text("Cancel", self.f_medb, TEXT_PRI, c.center, "center")
        self.buttons["cancel"] = c

    # ── Page: Now ─────────────────────────────────────────────────────────────

    def draw_now(self, now):
        s = self.monitor.data("summary")
        if not s:
            return self.draw_waiting()
        q = s["queries"]

        # Today's numbers, with the share blocked as a ring
        r = pygame.Rect(PAD, BODY_Y, SCREEN_W - 2 * PAD, 224)
        self.card(r)
        pct = (q.get("percent_blocked") or 0) / 100
        cx, cy = r.x + 106, r.centery
        self.draw_ring(cx, cy, 88, 62, [(pct, BLOCK_RED), (1 - pct, ALLOW_GREEN)])
        self.text(f"{pct * 100:.1f}%", self.f_large, TEXT_PRI, (cx, cy - 8), "center")
        self.text("blocked", self.f_small, TEXT_SEC, (cx, cy + 20), "center")
        x = r.x + 222
        # Behind a router the Pi-hole sees one device, so count the domains looked up instead
        third = (("Domains", f"{q.get('unique_domains', 0):,}") if self.router()
                 else ("Devices", f"{s['clients']['active']:,}"))
        for i, (label, val, col) in enumerate((("Lookups, 24 h", f"{q['total']:,}", TEXT_PRI),
                                               ("Blocked", f"{q['blocked']:,}", BLOCK_RED),
                                               third + (TEXT_PRI,))):
            y = r.y + 16 + i * 68
            self.text(label, self.f_small, TEXT_SEC, (x, y))
            self.text(val, self.f_large, col, (x, y + 22))

        # The last 24 hours, allowed and blocked, in 10-minute bars
        r = pygame.Rect(PAD, BODY_Y + 238, SCREEN_W - 2 * PAD, 228)
        self.card(r)
        self.text("Last 24 hours", self.f_smallb, TEXT_PRI, (r.x + 16, r.y + 14))
        lx = r.right - 16
        for label, col in (("blocked", BLOCK_RED), ("allowed", ALLOW_GREEN)):
            lr = self.text(label, self.f_tiny, TEXT_SEC, (lx, r.y + 17), "topright")
            pygame.draw.rect(self.screen, col, (lr.x - 16, lr.y + 2, 10, 10), border_radius=2)
            lx = lr.x - 28
        bars = self.monitor.data("history") or []
        chart = pygame.Rect(r.x + 16, r.y + 50, r.w - 32, 140)
        pygame.draw.line(self.screen, GRID, (chart.x, chart.bottom), (chart.right, chart.bottom), 1)
        if bars:
            top = max(a + b for _, a, b in bars) or 1
            bw = chart.w / len(bars)
            for i, (t, allowed, blocked) in enumerate(bars):
                x = round(chart.x + i * bw)
                w = max(1, round(chart.x + (i + 1) * bw) - x - (1 if bw > 3 else 0))
                ha = round(chart.h * allowed / top)
                hb = round(chart.h * blocked / top)
                if ha:
                    pygame.draw.rect(self.screen, ALLOW_GREEN, (x, chart.bottom - ha - hb, w, ha))
                if hb:
                    pygame.draw.rect(self.screen, BLOCK_RED, (x, chart.bottom - hb, w, hb))
            t0, t1 = bars[0][0], bars[-1][0]
            for i, (t, _, _) in enumerate(bars):            # a time label every 6 hours
                lt = datetime.fromtimestamp(t)
                if lt.minute < 10 and lt.hour % 6 == 0 and t1 > t0:
                    x = chart.x + chart.w * (t - t0) / (t1 - t0)
                    if chart.x + 20 < x < chart.right - 20:          # whole labels only
                        self.text(f"{lt:%H}:00", self.f_tiny, TEXT_DIM, (x, chart.bottom + 8), "midtop")

        # The blocklist, and how busy the network is
        r = pygame.Rect(PAD, BODY_Y + 480, SCREEN_W - 2 * PAD, TABS_Y - BODY_Y - 480 - 14)
        self.card(r)
        g = s.get("gravity", {})
        self.text("Blocklist", self.f_small, TEXT_SEC, (r.x + 16, r.y + 16))
        self.text(f"{g.get('domains_being_blocked', 0):,} domains", self.f_medb, TEXT_PRI, (r.x + 120, r.y + 14))
        if g.get("last_update"):
            self.text(f"updated {pd.ago(time.time() - g['last_update'])} ago", self.f_tiny, TEXT_DIM,
                      (r.x + 120, r.y + 42))
        self.text("Speed", self.f_small, TEXT_SEC, (r.x + 16, r.y + 74))
        self.text(f"{q.get('frequency', 0):.1f} lookups a second", self.f_medb, TEXT_PRI, (r.x + 120, r.y + 72))

    # ── Page: Top ─────────────────────────────────────────────────────────────

    def draw_top(self, now):
        top = self.monitor.data("top")
        # Blocked | Allowed | Devices
        w = (SCREEN_W - 2 * PAD) / len(LISTS)
        for i, (key, label) in enumerate(LISTS):
            r = pygame.Rect(round(PAD + i * w), BODY_Y, round(w) - 6, 46)
            sel = key == self.top_list
            self.card(r, ACCENT if sel else BG_CARD, radius=12)
            self.text(label, self.f_smallb, (8, 20, 34) if sel else TEXT_SEC, r.center, "center")
            self.buttons[f"list:{key}"] = r
        if not top:
            return self.draw_waiting()
        rows = top.get(self.top_list) or []
        if not rows:
            return self.draw_message("Nothing yet")
        most = max(r_["count"] for r_ in rows) or 1
        colour = {"blocked": BLOCK_RED, "allowed": ALLOW_GREEN}.get(self.top_list, ACCENT)
        row_h, y0 = 46, BODY_Y + 62
        router = self.router() if self.top_list == "clients" else None
        if router:
            r = pygame.Rect(PAD, y0, SCREEN_W - 2 * PAD, 104)
            self.card(r, BG_CARD)
            for j, line in enumerate(("Every device's lookups reach the Pi-hole",
                                      f"through the router ({router}), so it",
                                      "can't tell the devices apart.")):
                self.text(line, self.f_small, TEXT_SEC, (r.x + 16, r.y + 14 + j * 26))
            y0 = r.bottom + 16
        for i, row in enumerate(rows[:pd.TOP_ROWS]):
            y = y0 + i * row_h
            if y + row_h > TABS_Y - 4:
                break
            name = pd.client_name(row) if self.top_list == "clients" else row["domain"]
            cr = self.text(f"{row['count']:,}", self.f_smallb, TEXT_PRI, (SCREEN_W - PAD, y + 4), "topright")
            self.text(truncate(name, self.f_small, cr.x - PAD - 16), self.f_small, TEXT_PRI, (PAD, y + 4))
            bar = SCREEN_W - 2 * PAD
            pygame.draw.rect(self.screen, GRID, (PAD, y + 30, bar, 6), border_radius=3)
            pygame.draw.rect(self.screen, colour, (PAD, y + 30, max(6, round(bar * row["count"] / most)), 6),
                             border_radius=3)

    # ── Page: Live ────────────────────────────────────────────────────────────

    def draw_live(self, now):
        qs = self.monitor.data("queries")
        self.text("Latest lookups", self.f_medb, TEXT_PRI, (PAD, BODY_Y))
        self.text("every 3 s", self.f_tiny, TEXT_DIM, (SCREEN_W - PAD, BODY_Y + 6), "topright")
        if qs is None:
            return self.draw_waiting()
        row_h = 42
        for i, q in enumerate(qs[:pd.LIVE_ROWS]):
            y = BODY_Y + 38 + i * row_h
            if y + row_h > TABS_Y - 2:
                break
            k = pd.kind(q.get("status"))
            col = KIND_COLOURS[k]
            if i % 2 == 0:
                self.card(pygame.Rect(PAD - 6, y, SCREEN_W - 2 * PAD + 12, row_h), BG_CARD, radius=8)
            cx, cy = PAD + 10, y + row_h // 2
            pygame.draw.circle(self.screen, col, (cx, cy), 8)
            if k == "blocked":
                pygame.draw.line(self.screen, BG, (cx - 4, cy - 4), (cx + 4, cy + 4), 2)
                pygame.draw.line(self.screen, BG, (cx - 4, cy + 4), (cx + 4, cy - 4), 2)
            tr = self.text(f"{datetime.fromtimestamp(q['time']):%H:%M:%S}", self.f_tiny, TEXT_DIM,
                           (SCREEN_W - PAD, y + 5), "topright")
            who = pd.client_name(q.get("client"))
            self.text(truncate(who, self.f_tiny, 150), self.f_tiny, TEXT_SEC, (SCREEN_W - PAD, y + 22), "topright")
            self.text(truncate(q.get("domain") or "?", self.f_small, SCREEN_W - PAD - 34 - max(tr.w, 150) - 8),
                      self.f_small, BLOCK_RED if k == "blocked" else TEXT_PRI, (PAD + 26, cy), "midleft")

    # ── Drawing a frame ───────────────────────────────────────────────────────

    def draw(self, now=None):
        now = now or datetime.now()
        self.buttons = {}
        self.screen.fill(BG)
        self.draw_status_bar(now)
        self.draw_strip()
        {"Now": self.draw_now, "Top": self.draw_top, "Live": self.draw_live}[self.page](now)
        self.draw_tabs()
        if self.choosing:
            self.buttons = {k: v for k, v in self.buttons.items() if k in ("sleep", "home")}
            self.draw_choices()
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
        if abs(dx) > SWIPE and abs(dx) > abs(dy) and not (self.sleeping or self.dimmed or self.choosing):
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
        elif hit == "pause":
            self.choosing = True
        elif hit and hit.startswith("pause:"):
            self.choosing = False
            self.monitor.pause(int(hit[6:]))
        elif hit == "resume":
            self.monitor.resume()
        elif hit == "cancel" or self.choosing:           # a tap outside the choices closes them
            self.choosing = False
        elif hit and hit.startswith("page:"):
            self.page = hit[5:]
        elif hit and hit.startswith("list:"):
            self.top_list = hit[5:]

    def tick(self):
        """Idle bookkeeping: dimming, cycling pages if cycle_seconds is set, and the Live feed."""
        idle = time.time() - self.last_touch
        self.dimmed = idle > DIM_AFTER
        if idle > CYCLE_IDLE:
            self.choosing = False
            if self.cycle_seconds and time.time() - self.last_cycle > self.cycle_seconds:
                self.last_cycle = time.time()
                self.turn_page(1)
        self.monitor.live = self.page == "Live" and not self.sleeping

    # ── Main loop ─────────────────────────────────────────────────────────────

    def state(self, now):
        """Everything on screen depends on: what's selected, dimming, the time and the data.
        While paused the countdown ticks every second; otherwise the minute will do."""
        state, left = self.blocking()
        tick = now.strftime("%H:%M") + (f":{int(left)}" if state == "paused" else "")
        return (self.page, self.top_list, self.choosing, self.dimmed, self.sleeping, tick,
                self.monitor.version, self.monitor.error, state)

    def frame(self, now=None):
        """Redraw only if something on screen would change (a Pi 3 at 10 FPS runs warm).
        Returns True if it drew."""
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
    mon = pd.Monitor(pd.Api(cfg["url"])).start()
    # systemd stops apps with SIGTERM: exit through `finally` so the Pi-hole session is freed
    signal.signal(signal.SIGTERM, lambda *a: sys.exit(0))
    try:
        PiholeDisplay(cfg, mon).run()
    finally:
        mon.stop()
