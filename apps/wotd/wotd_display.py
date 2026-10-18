#!/usr/bin/env python3
"""
Word of the Day Display
Merriam-Webster's word of the day, fullscreen on the HyperPixel 4.0 (480x800 portrait).

Pages run along the bottom: Word (the word, how to say it, what it means and an example),
Story (where it comes from, and a quote from the press) and Past (every word kept, newest
first - tap one to read it). On Word and Story, swipe left for the day before and right
for the day after. Words are fetched in the background (wotd_data.Store).
"""

import os
import sys
import time
from datetime import date, datetime

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "..", "common"))
import wotd_data as wd  # noqa: E402

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
HOME_REQUEST = "/run/pi3-touch/request-home"   # Pi3 Touch launcher picks this up
FONT_DIR = os.environ.get("PI3_FONT_DIR", "/usr/share/fonts/truetype/dejavu")
SERIF_DIR = os.environ.get("PI3_SERIF_DIR", "/usr/share/fonts/truetype/freefont")
PAGES = ["Word", "Story", "Past"]

# ── Colours ───────────────────────────────────────────────────────────────────
BG          = (10,  16,  26)
BG_CARD     = (22,  32,  48)
BG_CARD_HI  = (36,  52,  76)
TEXT_PRI    = (238, 242, 248)
TEXT_SEC    = (150, 165, 185)
TEXT_DIM    = (88,  102, 122)
ACCENT      = (190, 160, 255)   # violet
WORD        = (250, 244, 232)   # warm white, like paper
OK_GREEN    = (80,  200, 120)
STALE       = (230, 170, 60)
BAD_RED     = (225, 70,  80)

# ── Layout constants (portrait 480x800) ───────────────────────────────────────
PAD     = 18
TABS_Y  = 736
TABS_H  = SCREEN_H - TABS_Y
BODY_SIZES = [26, 24, 22, 21, 20, 19, 18, 17, 16, 15, 14]


def load_font(size, bold=False):
    path = os.path.join(FONT_DIR, "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf")
    try:
        return pygame.font.Font(path, size)
    except (OSError, FileNotFoundError):
        return pygame.font.SysFont("dejavusans,freesans,arial", size, bold=bold)


def load_serif(size, bold=False, italic=False):
    """GNU FreeSerif (it has italics), else DejaVu Serif, else whatever pygame has."""
    name = "FreeSerif" + ("Bold" if bold else "") + ("Italic" if italic else "") + ".ttf"
    for path in (os.path.join(SERIF_DIR, name),
                 os.path.join(FONT_DIR, "DejaVuSerif-Bold.ttf" if bold else "DejaVuSerif.ttf")):
        try:
            return pygame.font.Font(path, size)
        except (OSError, FileNotFoundError):
            continue
    return pygame.font.SysFont("serif", size, bold=bold, italic=italic)


def truncate(text, font, max_width):
    if font.size(text)[0] <= max_width:
        return text
    while text and font.size(text + "…")[0] > max_width:
        text = text[:-1]
    return text + "…"


def split(text, font, width):
    """Plain text wrapped to width, as lines."""
    lines, line = [], ""
    for word in text.split():
        trial = f"{line} {word}".strip()
        if line and font.size(trial)[0] > width:
            lines.append(line)
            line = word
        else:
            line = trial
    return lines + ([line] if line else [])


def words_of(runs):
    """Runs of (text, italic) -> words, each a list of (text, italic) pieces (a word can
    change style part way, e.g. “bon vivant”)."""
    out, cur = [], []
    for text, it in runs:
        for k, part in enumerate(text.split(" ")):
            if k > 0 and cur:
                out.append(cur)
                cur = []
            if part:
                cur.append((part, it))
    return out + ([cur] if cur else [])


class WotdDisplay:
    def __init__(self, store, screen=None):
        self.store = store

        if screen is None:
            self.display = pgscreen.Display(SCREEN_W, SCREEN_H, "Word of the Day")
            screen = self.display.surface
        else:
            self.display = None
            pygame.font.init()
        self.screen = screen

        self.serifs = {}
        self.sans = {}
        self.f_clock = load_font(28, bold=True)

        self.page = "Word"
        self.date = None            # the word on show (None: the newest)
        self.past_from = 0          # first row on the Past page
        self.page_size = 9          # rows that fit on the Past page (set as it's drawn)
        self.last_touch = time.time()
        self.dimmed = False
        self.sleeping = False
        self.down = None            # where the current touch started
        self.buttons = {}           # name -> Rect, rebuilt every frame
        self.drawn = None           # state() of the frame on screen

    # ── Helpers ───────────────────────────────────────────────────────────────

    def serif(self, size, bold=False, italic=False):
        k = (size, bold, italic)
        if k not in self.serifs:
            self.serifs[k] = load_serif(size, bold, italic)
        return self.serifs[k]

    def font(self, size, bold=False):
        k = (size, bold)
        if k not in self.sans:
            self.sans[k] = load_font(size, bold)
        return self.sans[k]

    def text(self, s, font, colour, pos, anchor="topleft"):
        surf = font.render(s, True, colour)
        rect = surf.get_rect(**{anchor: pos})
        self.screen.blit(surf, rect)
        return rect

    def card(self, rect, colour=BG_CARD, radius=14):
        pygame.draw.rect(self.screen, colour, rect, border_radius=radius)

    def flow(self, runs, width, size, italic=False):
        """A paragraph wrapped to width: [[(x, text, font)]] lines. italic=True sets it all
        in italics (and its own italics upright, as books do)."""
        lines, line, x = [], [], 0
        space = self.serif(size).size(" ")[0]
        for word in words_of(runs):
            pieces = [(t, self.serif(size, italic=it != italic)) for t, it in word]
            w = sum(f.size(t)[0] for t, f in pieces)
            if line and x + space + w > width:
                lines.append(line)
                line, x = [], 0
            if line:
                x += space
            for t, f in pieces:
                line.append((x, t, f))
                x += f.size(t)[0]
        return lines + ([line] if line else [])

    def fit(self, blocks, width, height, sizes=BODY_SIZES):
        """(size, [(lines, block)]) - the biggest size at which the blocks fit, one under
        another. A block is {"runs", "italic"?, "gap"?, "indent"?}."""
        for size in sizes:
            laid = [(self.flow(b["runs"], width - b.get("indent", 0), size, b.get("italic", False)), b)
                    for b in blocks]
            lh = self.serif(size).get_linesize()
            total = sum(len(ls) * lh + b.get("gap", round(lh * 0.6)) for ls, b in laid)
            if total <= height:
                return size, laid
        return sizes[-1], laid

    def draw_blocks(self, size, laid, x, y, colour=TEXT_PRI, bottom=None):
        lh = self.serif(size).get_linesize()
        for lines, b in laid:
            col = b.get("colour", colour)
            top = y
            for line in lines:
                if bottom and y + lh > bottom:
                    return y
                for lx, t, f in line:
                    self.screen.blit(f.render(t, True, col), (x + b.get("indent", 0) + lx, y))
                y += lh
            if b.get("bar"):
                pygame.draw.rect(self.screen, ACCENT, (x, top + 4, 3, y - top - 8), border_radius=1)
            y += b.get("gap", round(lh * 0.6))
        return y

    def words(self):
        return self.store.words()

    def current(self):
        """The word on show: the chosen day's, else the newest."""
        ws = self.words()
        return next((w for w in ws if w["date"] == self.date), ws[0] if ws else None)

    def step(self, older):
        ws = self.words()
        cur = self.current()
        if not cur:
            return
        i = ws.index(cur) + (1 if older else -1)
        if 0 <= i < len(ws):
            self.date = ws[i]["date"]

    @staticmethod
    def when(day, today):
        d = date.fromisoformat(day)
        n = (today - d).days
        name = f"{d:%A} {d.day} {d:%B}"
        return name, "Today" if n == 0 else "Yesterday" if n == 1 else f"{n} days ago" if n > 1 else ""

    # ── Chrome ────────────────────────────────────────────────────────────────

    def draw_status_bar(self, now):
        self.text(now.strftime("%H:%M"), self.f_clock, TEXT_PRI, (PAD, 12))
        err = self.store.error()
        have = bool(self.words())
        if err:
            self.text(f"({err})", self.font(13), TEXT_DIM, (PAD + 92, 24))
        dot = BAD_RED if not have else STALE if err else OK_GREEN
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
            self.text(p, self.font(20, sel), ACCENT if sel else TEXT_SEC, r.center, "center")
            self.buttons[f"page:{p}"] = r

    def draw_dateline(self, w, now, y=60):
        """The word's day, and the arrows to the days either side."""
        name, ago = self.when(w["date"], now.date())
        ws = self.words()
        i = ws.index(w)
        self.text(name, self.font(17, True), TEXT_SEC, (PAD, y))
        if ago:
            self.text(ago, self.font(15), ACCENT if ago == "Today" else TEXT_DIM, (PAD, y + 24))
        for older, label, x in ((True, "‹", SCREEN_W - PAD - 92), (False, "›", SCREEN_W - PAD - 40)):
            ok = (i + 1 < len(ws)) if older else i > 0
            r = pygame.Rect(x, y - 4, 40, 44)
            self.card(r, BG_CARD_HI if ok else BG_CARD, radius=10)
            self.text(label, self.font(26, True), TEXT_PRI if ok else TEXT_DIM, r.center, "center")
            if ok:
                self.buttons["older" if older else "newer"] = r

    def draw_waiting(self):
        err = self.store.error()
        self.text("Fetching the word of the day…", self.font(20), TEXT_SEC, (SCREEN_W // 2, 360), "center")
        if err:
            self.text(f"({err} - retrying)", self.font(16), TEXT_DIM, (SCREEN_W // 2, 392), "center")

    # ── Page: Word ────────────────────────────────────────────────────────────

    def draw_word(self, now):
        w = self.current()
        if not w:
            return self.draw_waiting()
        self.draw_dateline(w, now)
        # The word itself, as big as fits on a line (or two)
        y = 116
        for size in (68, 60, 52, 46, 40, 34):
            f = self.serif(size, bold=True)
            lines = split(w["word"], f, SCREEN_W - 2 * PAD)
            if len(lines) == 1 or size == 34:
                break
        for line in lines[:2]:
            r = self.text(line, f, WORD, (PAD - 2, y))
            y = r.bottom - 4
        y += 8
        x = PAD
        if w["say"]:
            x = self.text(f"\\{w['say']}\\", self.font(19), TEXT_SEC, (x, y)).right + 14
        if w["pos"]:
            self.text(w["pos"], self.serif(24, italic=True), ACCENT, (x, y - 4))
        y += 40
        pygame.draw.line(self.screen, BG_CARD_HI, (PAD, y), (SCREEN_W - PAD, y), 2)
        y += 16

        # Meaning, then the example; similar words at the foot
        sim = w.get("similar") or []
        bottom = TABS_Y - (58 if sim else 16)
        blocks = [{"runs": m["runs"], "italic": True, "colour": TEXT_SEC, "indent": 14, "bar": True}
                  if m["example"] else {"runs": m["runs"]} for m in w["meaning"]]
        size, laid = self.fit(blocks, SCREEN_W - 2 * PAD, bottom - y)
        self.draw_blocks(size, laid, PAD, y, bottom=bottom)
        if sim:
            sy = TABS_Y - 46
            lr = self.text("Similar", self.font(14, True), TEXT_DIM, (PAD, sy + 8))
            self.text(truncate(", ".join(sim), self.serif(21, italic=True), SCREEN_W - PAD - lr.right - 12),
                      self.serif(21, italic=True), TEXT_PRI, (lr.right + 12, sy + 2))

    # ── Page: Story ───────────────────────────────────────────────────────────

    def draw_story(self, now):
        w = self.current()
        if not w:
            return self.draw_waiting()
        self.draw_dateline(w, now)
        y = 116
        r = self.text(w["word"], self.serif(34, bold=True), WORD, (PAD, y))
        y = r.bottom + 10
        blocks = []
        if w["story"]:
            blocks.append({"runs": [("Did you know?", False)], "colour": ACCENT, "gap": 6})
            blocks += [{"runs": p} for p in w["story"]]
        if w["quotes"]:
            blocks.append({"runs": [("In the news", False)], "colour": ACCENT, "gap": 6})
            blocks += [{"runs": p, "colour": TEXT_SEC, "indent": 14, "bar": True} for p in w["quotes"]]
        if not blocks:
            blocks = [{"runs": [("No story for this word.", True)], "colour": TEXT_DIM}]
        size, laid = self.fit(blocks, SCREEN_W - 2 * PAD, TABS_Y - 14 - y, [22, 21, 20, 19, 18, 17, 16, 15, 14, 13])
        self.draw_blocks(size, laid, PAD, y, bottom=TABS_Y - 8)

    # ── Page: Past ────────────────────────────────────────────────────────────

    def draw_past(self, now):
        ws = self.words()
        if not ws:
            return self.draw_waiting()
        self.text(f"{len(ws)} words", self.font(22, True), TEXT_PRI, (PAD, 60))
        row_h, top = 60, 100
        fits = (TABS_Y - 70 - top) // row_h
        self.past_from = max(0, min(self.past_from, len(ws) - fits))
        cur = self.current()
        for k, w in enumerate(ws[self.past_from:self.past_from + fits]):
            r = pygame.Rect(PAD - 6, top + k * row_h, SCREEN_W - 2 * PAD + 12, row_h - 6)
            self.card(r, BG_CARD_HI if cur and w["date"] == cur["date"] else BG_CARD, radius=10)
            d = date.fromisoformat(w["date"])
            self.text(f"{d:%a}", self.font(13, True), TEXT_DIM, (r.x + 12, r.y + 9))
            self.text(f"{d.day} {d:%b}", self.font(13), TEXT_DIM, (r.x + 12, r.y + 28))
            wr = self.text(truncate(w["word"], self.serif(25, bold=True), 300), self.serif(25, bold=True), WORD,
                           (r.x + 74, r.y + 3))
            self.text(truncate(w["short"] or w["pos"], self.font(14), r.right - 12 - (r.x + 74)), self.font(14),
                      TEXT_SEC, (r.x + 74, wr.bottom - 2))
            self.buttons[f"word:{w['date']}"] = r
        by = TABS_Y - 62
        if self.past_from > 0:
            r = pygame.Rect(PAD, by, 140, 50)
            self.card(r, BG_CARD_HI, radius=10)
            self.text("‹ Newer", self.font(18, True), TEXT_PRI, r.center, "center")
            self.buttons["up"] = r
        if self.past_from + fits < len(ws):
            r = pygame.Rect(SCREEN_W - PAD - 140, by, 140, 50)
            self.card(r, BG_CARD_HI, radius=10)
            self.text("Older ›", self.font(18, True), TEXT_PRI, r.center, "center")
            self.buttons["down"] = r
        self.page_size = fits

    # ── Drawing a frame ───────────────────────────────────────────────────────

    def draw(self, now=None):
        now = now or datetime.now()
        self.buttons = {}
        self.screen.fill(BG)
        self.draw_status_bar(now)
        {"Word": self.draw_word, "Story": self.draw_story, "Past": self.draw_past}[self.page](now)
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
        """A touch ended: a sideways swipe changes day (Word, Story), anything else is a tap."""
        start, self.down = self.down or pos, None
        dx, dy = pos[0] - start[0], pos[1] - start[1]
        if abs(dx) > SWIPE and abs(dx) > abs(dy) and not (self.sleeping or self.dimmed):
            self.last_touch = time.time()
            if self.page != "Past":
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

        hit = next((name for name, r in self.buttons.items() if r.collidepoint(pos)), None)
        if hit == "sleep":
            self.sleeping = True
        elif hit == "home":
            try:
                open(HOME_REQUEST, "w").close()
            except OSError as e:
                print(f"[home] can't request menu: {e}", flush=True)
        elif hit in ("older", "newer"):
            self.step(older=hit == "older")
        elif hit and hit.startswith("page:"):
            self.page = hit[5:]
        elif hit and hit.startswith("word:"):
            self.date, self.page = hit[5:], "Word"
        elif hit == "up":
            self.past_from -= self.page_size
        elif hit == "down":
            self.past_from += self.page_size

    def tick(self):
        """Idle bookkeeping: dimming; back to the newest word once left alone."""
        idle = time.time() - self.last_touch
        self.dimmed = idle > DIM_AFTER
        if self.dimmed:
            self.date, self.page, self.past_from = None, "Word", 0

    # ── Main loop ─────────────────────────────────────────────────────────────

    def state(self, now):
        """Everything on screen depends on: page, word, dimming, the minute and the data."""
        return (self.page, self.date, self.past_from, self.dimmed, self.sleeping, now.strftime("%Y-%m-%d %H:%M"),
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
    store = wd.Store().start()
    WotdDisplay(store).run()
