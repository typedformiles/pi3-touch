#!/usr/bin/env python3
"""
Space Picture Display
NASA's Astronomy Picture of the Day, fullscreen on the HyperPixel 4.0 (480x800 portrait).

Pages run along the bottom: Picture (the day's picture as big as fits, over a soft blur
of itself, with its title and credit; tap it to fill the screen instead), About (the
astronomer's explanation) and Days (the last few weeks - tap one to see it). On Picture
and About, swipe left for the day before and right for the day after. Pictures are
fetched in the background (apod_data.Store).
"""

import os
import sys
import time
from datetime import date, datetime

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "..", "common"))
import apod_data as ad  # noqa: E402

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
PICTURES_KEPT = 4           # decoded pictures kept in memory (the one on show and its neighbours)
HOME_REQUEST = "/run/pi3-touch/request-home"   # Pi3 Touch launcher picks this up
FONT_DIR = os.environ.get("PI3_FONT_DIR", "/usr/share/fonts/truetype/dejavu")
PAGES = ["Picture", "About", "Days"]

# ── Colours ───────────────────────────────────────────────────────────────────
BG          = (6,   10,  20)
BG_CARD     = (22,  32,  48)
BG_CARD_HI  = (36,  52,  76)
TEXT_PRI    = (238, 242, 248)
TEXT_SEC    = (165, 178, 198)
TEXT_DIM    = (95,  108, 128)
ACCENT      = (120, 170, 255)   # deep-sky blue
OK_GREEN    = (80,  200, 120)
STALE       = (230, 170, 60)
BAD_RED     = (225, 70,  80)

# ── Layout constants (portrait 480x800) ───────────────────────────────────────
PAD     = 16
TABS_Y  = 736
TABS_H  = SCREEN_H - TABS_Y
VIEW    = pygame.Rect(0, 0, SCREEN_W, TABS_Y)      # where the picture goes


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
    """Split text into lines no wider than width; newlines start new paragraphs ("" between)."""
    out = []
    for k, para in enumerate(text.split("\n")):
        if k:
            out.append("")
        line = ""
        for word in para.split():
            trial = f"{line} {word}".strip()
            if line and font.size(trial)[0] > width:
                out.append(line)
                line = word
            else:
                line = trial
        if line:
            out.append(line)
    return out


def gradient(w, h, top_alpha, bottom_alpha):
    """A black band fading from top_alpha to bottom_alpha (0-255)."""
    s = pygame.Surface((w, h), pygame.SRCALPHA)
    for y in range(h):
        a = round(top_alpha + (bottom_alpha - top_alpha) * y / max(1, h - 1))
        pygame.draw.line(s, (0, 0, 0, a), (0, y), (w, y))
    return s


class ApodDisplay:
    def __init__(self, store, screen=None):
        self.store = store

        if screen is None:
            self.display = pgscreen.Display(SCREEN_W, SCREEN_H, "Space Picture")
            screen = self.display.surface
        else:
            self.display = None
            pygame.font.init()
        self.screen = screen

        self.fonts = {}
        self.f_clock = load_font(28, bold=True)
        self.shade_top = gradient(SCREEN_W, 70, 170, 0)
        self.shade_bottom = gradient(SCREEN_W, 190, 0, 215)

        self.page = "Picture"
        self.date = None            # the day on show (None: the newest)
        self.fill = False           # Picture page: fill the screen (crop) instead of fit
        self.days_from = 0          # first row on the Days page
        self.days_page = 8          # rows that fit there (set as it's drawn)
        self.pictures = {}          # (date, mtime) -> {"fit", "fill", "backdrop"} Surfaces, or None
        self.thumbs = {}            # date -> Surface, or None
        self.last_touch = time.time()
        self.dimmed = False
        self.sleeping = False
        self.down = None            # where the current touch started
        self.buttons = {}           # name -> Rect, rebuilt every frame
        self.drawn = None           # state() of the frame on screen

    # ── Helpers ───────────────────────────────────────────────────────────────

    def font(self, size, bold=False):
        if (size, bold) not in self.fonts:
            self.fonts[(size, bold)] = load_font(size, bold)
        return self.fonts[(size, bold)]

    def text(self, s, font, colour, pos, anchor="topleft", shadow=False):
        surf = font.render(s, True, colour)
        rect = surf.get_rect(**{anchor: pos})
        if shadow:
            self.screen.blit(font.render(s, True, (0, 0, 0)), rect.move(1, 1))
        self.screen.blit(surf, rect)
        return rect

    def card(self, rect, colour=BG_CARD, radius=14):
        pygame.draw.rect(self.screen, colour, rect, border_radius=radius)

    def days(self):
        return self.store.days()

    def current(self):
        ds = self.days()
        return next((d for d in ds if d["date"] == self.date), ds[0] if ds else None)

    def step(self, older):
        ds, cur = self.days(), self.current()
        if not cur:
            return
        i = ds.index(cur) + (1 if older else -1)
        if 0 <= i < len(ds):
            self.date = ds[i]["date"]

    def prepared(self, day):
        """The day's picture decoded and scaled for the screen - fit, fill and a blurred
        backdrop - or None (no picture yet, or one that won't load)."""
        path = self.store.picture(day["date"])
        if not path:
            return None
        k = (day["date"], os.path.getmtime(path))
        if k not in self.pictures:
            try:
                img = pygame.image.load(path).convert()
            except (pygame.error, OSError) as e:
                print(f"[apod] can't show {path}: {e}", flush=True)
                self.pictures[k] = None
                return None
            w, h = img.get_size()
            fit = min(VIEW.w / w, VIEW.h / h)
            cover = max(VIEW.w / w, VIEW.h / h)
            fill = pygame.transform.smoothscale(img, (round(w * cover), round(h * cover)))
            small = pygame.transform.smoothscale(img, (max(1, VIEW.w // 16), max(1, VIEW.h // 16)))
            back = pygame.transform.smoothscale(pygame.transform.smoothscale(small, (VIEW.w // 4, VIEW.h // 4)),
                                                VIEW.size)
            back.fill((90, 90, 90), special_flags=pygame.BLEND_MULT)      # darker, so the picture stands out
            self.pictures[k] = {"fit": pygame.transform.smoothscale(img, (round(w * fit), round(h * fit))),
                                "fill": fill, "backdrop": back}
            while len(self.pictures) > PICTURES_KEPT:
                self.pictures.pop(next(iter(self.pictures)))
        return self.pictures[k]

    def thumb(self, day):
        if day["date"] not in self.thumbs:
            path = self.store.thumb(day["date"])
            if not path:
                return None
            try:
                img = pygame.image.load(path).convert()
                self.thumbs[day["date"]] = pygame.transform.smoothscale(img, (64, 64))
            except (pygame.error, OSError):
                self.thumbs[day["date"]] = None
        return self.thumbs[day["date"]]

    @staticmethod
    def when(day, today):
        d = date.fromisoformat(day)
        n = (today - d).days
        return f"{d:%a} {d.day} {d:%b}", "Today" if n == 0 else "Yesterday" if n == 1 else f"{n} days ago" if n > 1 else ""

    # ── Chrome ────────────────────────────────────────────────────────────────

    def draw_status_bar(self, now, over_picture=False):
        if over_picture:
            self.screen.blit(self.shade_top, (0, 0))
        self.text(now.strftime("%H:%M"), self.f_clock, TEXT_PRI, (PAD, 12), shadow=over_picture)
        err = self.store.error()
        dot = BAD_RED if not self.days() else STALE if err else OK_GREEN
        pygame.draw.circle(self.screen, dot, (SCREEN_W - PAD - 8, 28), 8)

        # Sleep button (moon crescent) - left of the status dot
        mx, my, mr = SCREEN_W - PAD - 40, 26, 10
        pygame.draw.circle(self.screen, TEXT_SEC, (mx, my), mr)
        pygame.draw.circle(self.screen, (0, 0, 0) if over_picture else BG, (mx + 5, my - 4), mr - 2)
        self.buttons["sleep"] = pygame.Rect(mx - 18, my - 18, 36, 36)

        # Home button (house) - back to the Pi3 Touch menu; only when the launcher is installed
        if os.path.isdir(os.path.dirname(HOME_REQUEST)):
            hx, hy = SCREEN_W - PAD - 82, 27
            pygame.draw.polygon(self.screen, TEXT_SEC, [(hx - 13, hy - 1), (hx, hy - 12), (hx + 13, hy - 1)])
            pygame.draw.rect(self.screen, TEXT_SEC, (hx - 9, hy - 1, 18, 12))
            pygame.draw.rect(self.screen, (0, 0, 0) if over_picture else BG, (hx - 2, hy + 4, 5, 7))   # door
            self.buttons["home"] = pygame.Rect(hx - 18, hy - 18, 36, 36)

    def draw_tabs(self):
        pygame.draw.rect(self.screen, BG_CARD, (0, TABS_Y, SCREEN_W, TABS_H))
        w = SCREEN_W / len(PAGES)
        for i, p in enumerate(PAGES):
            r = pygame.Rect(round(i * w), TABS_Y, round(w), TABS_H)
            sel = p == self.page
            if sel:
                pygame.draw.rect(self.screen, ACCENT, (r.x + 14, TABS_Y, r.w - 28, 4), border_radius=2)
            self.text(p, self.font(20, sel), ACCENT if sel else TEXT_SEC, r.center, "center")
            self.buttons[f"page:{p}"] = r

    def draw_waiting(self):
        err = self.store.error()
        self.text("Fetching the space picture…", self.font(20), TEXT_SEC, (SCREEN_W // 2, 360), "center")
        if err:
            self.text(f"({err} - retrying)", self.font(16), TEXT_DIM, (SCREEN_W // 2, 392), "center")

    # ── Page: Picture ─────────────────────────────────────────────────────────

    def draw_picture(self, now):
        d = self.current()
        if not d:
            self.draw_status_bar(now)
            return self.draw_waiting()
        pic = self.prepared(d)
        if pic:
            if self.fill:
                img = pic["fill"]
                self.screen.blit(img, img.get_rect(center=VIEW.center))
            else:
                self.screen.blit(pic["backdrop"], VIEW)
                self.screen.blit(pic["fit"], pic["fit"].get_rect(center=VIEW.center))
            self.buttons["picture"] = VIEW.inflate(0, -140)        # clear of the status bar and caption
        else:
            r = pygame.Rect(PAD, 200, SCREEN_W - 2 * PAD, 220)
            self.card(r)
            if d["video"] and not d["picture"]:
                self.text("▶", self.font(56, True), ACCENT, (r.centerx, r.y + 70), "center")
                self.text("Today's is a video" if d["date"] == self.days()[0]["date"] else "A video", self.font(20, True),
                          TEXT_PRI, (r.centerx, r.y + 128), "center")
                self.text("Watch it at science.nasa.gov/apod", self.font(16), TEXT_SEC, (r.centerx, r.y + 160),
                          "center")
            else:
                self.text("Fetching the picture…", self.font(20), TEXT_SEC, r.center, "center")
        self.draw_status_bar(now, over_picture=bool(pic))

        # Caption: title, day and credit, over a shade at the foot of the picture
        self.screen.blit(self.shade_bottom, (0, TABS_Y - self.shade_bottom.get_height()))
        f = self.font(24, True)
        lines = wrap(d["title"], f, SCREEN_W - 2 * PAD)[:2]
        name, ago = self.when(d["date"], now.date())
        meta = f"{ago or name} · {ad.credit_line(d['credit'])}" if d["credit"] else (ago or name)
        if d["video"] and d["picture"]:
            meta = "▶ Still from a video · " + meta
        y = TABS_Y - 16 - 22 - len(lines) * f.get_linesize()
        for line in lines:
            self.text(line, f, TEXT_PRI, (PAD, y), shadow=True)
            y += f.get_linesize()
        self.text(truncate(meta, self.font(15), SCREEN_W - 2 * PAD), self.font(15), TEXT_SEC, (PAD, y + 2),
                  shadow=True)

    # ── Page: About ───────────────────────────────────────────────────────────

    def draw_about(self, now):
        self.draw_status_bar(now)
        d = self.current()
        if not d:
            return self.draw_waiting()
        ds = self.days()
        i = ds.index(d)
        name, ago = self.when(d["date"], now.date())
        self.text(name, self.font(17, True), TEXT_SEC, (PAD, 60))
        if ago:
            self.text(ago, self.font(15), ACCENT if ago == "Today" else TEXT_DIM, (PAD, 84))
        for older, label, x in ((True, "‹", SCREEN_W - PAD - 92), (False, "›", SCREEN_W - PAD - 40)):
            ok = (i + 1 < len(ds)) if older else i > 0
            r = pygame.Rect(x, 56, 40, 44)
            self.card(r, BG_CARD_HI if ok else BG_CARD, radius=10)
            self.text(label, self.font(26, True), TEXT_PRI if ok else TEXT_DIM, r.center, "center")
            if ok:
                self.buttons["older" if older else "newer"] = r
        y = 114
        f = self.font(26, True)
        for line in wrap(d["title"], f, SCREEN_W - 2 * PAD)[:3]:
            self.text(line, f, TEXT_PRI, (PAD, y))
            y += f.get_linesize()
        if d["credit"]:
            for line in wrap(d["credit"], self.font(14), SCREEN_W - 2 * PAD)[:3]:
                self.text(line, self.font(14), ACCENT, (PAD, y + 2))
                y += 18
        y += 14
        # The explanation, as big as fits
        for size in (21, 20, 19, 18, 17, 16, 15, 14, 13):
            f = self.font(size)
            lines = wrap(d["explanation"], f, SCREEN_W - 2 * PAD)
            lh = f.get_linesize()
            if len(lines) * lh <= TABS_Y - 12 - y:
                break
        for line in lines:
            if y + lh > TABS_Y - 8:
                break
            if line:
                self.text(line, f, TEXT_PRI, (PAD, y))
            y += lh if line else lh // 2

    # ── Page: Days ────────────────────────────────────────────────────────────

    def draw_days(self, now):
        self.draw_status_bar(now)
        ds = self.days()
        if not ds:
            return self.draw_waiting()
        self.text(f"{len(ds)} days", self.font(22, True), TEXT_PRI, (PAD, 60))
        row_h, top = 76, 98
        fits = (TABS_Y - 70 - top) // row_h
        self.days_page = fits
        self.days_from = max(0, min(self.days_from, len(ds) - fits))
        cur = self.current()
        for k, d in enumerate(ds[self.days_from:self.days_from + fits]):
            r = pygame.Rect(PAD - 6, top + k * row_h, SCREEN_W - 2 * PAD + 12, row_h - 6)
            self.card(r, BG_CARD_HI if cur and d["date"] == cur["date"] else BG_CARD, radius=10)
            th = self.thumb(d)
            box = pygame.Rect(r.x + 3, r.y + 3, 64, 64)
            if th:
                self.screen.blit(th, box)
            else:
                pygame.draw.rect(self.screen, BG, box, border_radius=6)
                if d["video"]:
                    self.text("▶", self.font(22, True), ACCENT, box.center, "center")
            x = box.right + 12
            name, _ = self.when(d["date"], now.date())
            self.text(name + ("  ▶ video" if d["video"] else ""), self.font(13, True), TEXT_DIM, (x, r.y + 8))
            lines = wrap(d["title"], self.font(18, True), r.right - 10 - x)[:2]
            for j, line in enumerate(lines):
                self.text(line, self.font(18, True), TEXT_PRI, (x, r.y + 26 + j * 21))
            self.buttons[f"day:{d['date']}"] = r
        by = TABS_Y - 62
        if self.days_from > 0:
            r = pygame.Rect(PAD, by, 140, 50)
            self.card(r, BG_CARD_HI, radius=10)
            self.text("‹ Newer", self.font(18, True), TEXT_PRI, r.center, "center")
            self.buttons["up"] = r
        if self.days_from + fits < len(ds):
            r = pygame.Rect(SCREEN_W - PAD - 140, by, 140, 50)
            self.card(r, BG_CARD_HI, radius=10)
            self.text("Older ›", self.font(18, True), TEXT_PRI, r.center, "center")
            self.buttons["down"] = r

    # ── Drawing a frame ───────────────────────────────────────────────────────

    def draw(self, now=None):
        now = now or datetime.now()
        self.buttons = {}
        self.screen.fill(BG)
        {"Picture": self.draw_picture, "About": self.draw_about, "Days": self.draw_days}[self.page](now)
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

    def touch_down(self, pos):
        self.down = pos

    def touch_up(self, pos):
        """A touch ended: a sideways swipe changes day (Picture, About), anything else is a tap."""
        start, self.down = self.down or pos, None
        dx, dy = pos[0] - start[0], pos[1] - start[1]
        if abs(dx) > SWIPE and abs(dx) > abs(dy) and not (self.sleeping or self.dimmed):
            self.last_touch = time.time()
            if self.page != "Days":
                self.step(older=dx < 0)
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

        hit = next((name for name, r in self.buttons.items() if r.collidepoint(pos) and name != "picture"), None)
        if hit is None and "picture" in self.buttons and self.buttons["picture"].collidepoint(pos):
            hit = "picture"
        if hit == "sleep":
            self.sleeping = True
        elif hit == "home":
            try:
                open(HOME_REQUEST, "w").close()
            except OSError as e:
                print(f"[home] can't request menu: {e}", flush=True)
        elif hit == "picture":
            self.fill = not self.fill
        elif hit in ("older", "newer"):
            self.step(older=hit == "older")
        elif hit and hit.startswith("page:"):
            self.page = hit[5:]
        elif hit and hit.startswith("day:"):
            self.date, self.page = hit[4:], "Picture"
        elif hit == "up":
            self.days_from -= self.days_page
        elif hit == "down":
            self.days_from += self.days_page

    def tick(self):
        """Idle bookkeeping: dimming; back to today's picture once left alone."""
        self.dimmed = time.time() - self.last_touch > DIM_AFTER
        if self.dimmed:
            self.date, self.page, self.days_from, self.fill = None, "Picture", 0, False

    # ── Main loop ─────────────────────────────────────────────────────────────

    def state(self, now):
        """Everything on screen depends on: page, day, fill, dimming, the minute and the data."""
        return (self.page, self.date, self.fill, self.days_from, self.dimmed, self.sleeping,
                now.strftime("%Y-%m-%d %H:%M"), self.store.stamp())

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
    store = ad.Store().start()
    ApodDisplay(store).run()
