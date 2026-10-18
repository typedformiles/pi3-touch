#!/usr/bin/env python3
"""
Trivia Display
A quiz, fullscreen on the HyperPixel 4.0 (480x800 portrait).

Rounds of ten questions from the Open Trivia Database. Tap an answer: it turns green if
it's right, red if not (and the right one turns green). Next moves on; after the last
question comes the score. Topics picks the subject and difficulty. Questions are stocked
in the background (trivia_data.Store), so a round starts straight away.
"""

import os
import sys
import time
from datetime import datetime

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "..", "common"))
import trivia_data as td  # noqa: E402

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
HOME_REQUEST = "/run/pi3-touch/request-home"   # Pi3 Touch launcher picks this up
FONT_DIR = os.environ.get("PI3_FONT_DIR", "/usr/share/fonts/truetype/dejavu")

# ── Colours ───────────────────────────────────────────────────────────────────
BG          = (10,  16,  26)
BG_CARD     = (22,  32,  48)
BG_CARD_HI  = (36,  52,  76)
TEXT_PRI    = (238, 242, 248)
TEXT_SEC    = (150, 165, 185)
TEXT_DIM    = (88,  102, 122)
ACCENT      = (255, 196, 60)    # quiz gold
INK         = (30,  22,  6)     # text on gold
RIGHT       = (52,  180, 100)
WRONG       = (215, 70,  80)
OK_GREEN    = (80,  200, 120)
STALE       = (230, 170, 60)
BAD_RED     = (225, 70,  80)
LEVEL_COLOURS = {"easy": (110, 200, 120), "medium": (240, 190, 70), "hard": (235, 100, 90)}

# ── Layout constants (portrait 480x800) ───────────────────────────────────────
PAD     = 16
Q_Y     = 100           # question card
Q_H     = 236
ANS_Y   = 350           # answers
ANS_GAP = 10
BAR_Y   = 714           # bottom buttons
BAR_H   = 70


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
    """Split text into lines no wider than width (a word too long for a line is cut)."""
    lines, line = [], ""
    for word in text.split():
        trial = f"{line} {word}".strip()
        if line and font.size(trial)[0] > width:
            lines.append(line)
            line = word
        else:
            line = trial
    lines += [line] if line else []
    return [truncate(ln, font, width) for ln in lines]


class TriviaDisplay:
    def __init__(self, config, store, screen=None):
        self.round_len = config.get("round", 10)
        self.store = store

        if screen is None:
            self.display = pgscreen.Display(SCREEN_W, SCREEN_H, "Trivia")
            screen = self.display.surface
        else:
            self.display = None
            pygame.font.init()
        self.screen = screen

        self.fonts = {}
        self.f_huge  = load_font(96, bold=True)
        self.f_big   = load_font(32, bold=True)
        self.f_med   = load_font(20)
        self.f_medb  = load_font(20, bold=True)
        self.f_small = load_font(16)
        self.f_smallb = load_font(16, bold=True)
        self.f_tiny  = load_font(14)
        self.f_tinyb = load_font(14, bold=True)
        self.f_clock = load_font(28, bold=True)

        choice = store.choice or {}
        self.category = choice.get("category", config.get("category", 0))
        self.difficulty = choice.get("difficulty", config.get("difficulty", "any"))
        self.store.want = td.key(self.category, self.difficulty)
        self.mode = "pick" if not store.choice else "question"   # "pick", "question" or "done"
        self.questions, self.i, self.picked, self.results = [], 0, None, []
        self.last_touch = time.time()
        self.dimmed = False
        self.sleeping = False
        self.buttons = {}           # name -> Rect, rebuilt every frame
        self.drawn = None           # state() of the frame on screen

    # ── Helpers ───────────────────────────────────────────────────────────────

    def font(self, size, bold=False):
        if (size, bold) not in self.fonts:
            self.fonts[(size, bold)] = load_font(size, bold)
        return self.fonts[(size, bold)]

    def text(self, s, font, colour, pos, anchor="topleft"):
        surf = font.render(s, True, colour)
        rect = surf.get_rect(**{anchor: pos})
        self.screen.blit(surf, rect)
        return rect

    def card(self, rect, colour=BG_CARD, radius=14):
        pygame.draw.rect(self.screen, colour, rect, border_radius=radius)

    def fit(self, text, width, height, sizes, bold=True):
        """(font, lines) - the biggest of sizes whose wrapped text fits the box."""
        for size in sizes:
            f = self.font(size, bold)
            lines = wrap(text, f, width)
            if len(lines) * f.get_linesize() <= height:
                return f, lines
        f = self.font(sizes[-1], bold)
        lines = wrap(text, f, width)
        return f, lines[:max(1, height // f.get_linesize())]

    def block(self, lines, font, colour, rect):
        """Lines centred in a rect."""
        lh = font.get_linesize()
        y = rect.centery - lh * len(lines) / 2
        for i, line in enumerate(lines):
            self.text(line, font, colour, (rect.centerx, round(y + i * lh + lh / 2)), "center")

    def button(self, name, rect, label, fill, ink, font=None):
        self.card(rect, fill, radius=14)
        self.text(label, font or self.f_medb, ink, rect.center, "center")
        self.buttons[name] = rect

    @property
    def topic(self):
        return td.key(self.category, self.difficulty)

    @property
    def question(self):
        return self.questions[self.i] if self.i < len(self.questions) else None

    def start_round(self):
        """Ten new questions from the stock (fewer if that's all there is), or wait for some."""
        self.store.want = self.topic
        self.questions = self.store.take(self.topic, self.round_len)
        self.i, self.picked, self.results = 0, None, []
        self.mode = "question"

    # ── Chrome ────────────────────────────────────────────────────────────────

    def draw_status_bar(self, now):
        self.text(now.strftime("%H:%M"), self.f_clock, TEXT_PRI, (PAD, 12))
        err = self.store.error()
        stocked = self.store.count(self.topic)
        if err:
            self.text(f"({err})", self.f_tiny, TEXT_DIM, (PAD + 92, 24))
        dot = OK_GREEN if stocked and not err else STALE if stocked or self.questions else BAD_RED
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

    def draw_bar(self, right_label=None):
        """Topics on the left; the main action on the right."""
        left = pygame.Rect(PAD, BAR_Y, 150, BAR_H)
        self.button("topics", left, "Topics", BG_CARD_HI, TEXT_SEC)
        if right_label:
            r = pygame.Rect(left.right + 12, BAR_Y, SCREEN_W - PAD - left.right - 12, BAR_H)
            self.button("next", r, right_label, ACCENT, INK, self.f_big)

    # ── Question ──────────────────────────────────────────────────────────────

    def draw_question(self, now):
        q = self.question
        if not q:                               # waiting for the stock (tick() starts the round)
            err = self.store.error()
            if self.topic in self.store.exhausted:
                self.text("You've seen every question", self.f_medb, TEXT_PRI, (SCREEN_W // 2, 330), "center")
                self.text(f"in {td.topic_name(self.category)} ({self.difficulty})", self.f_med, TEXT_SEC,
                          (SCREEN_W // 2, 362), "center")
                self.button("again", pygame.Rect(PAD + 60, 420, SCREEN_W - 2 * PAD - 120, 64),
                            "Start them again", ACCENT, INK)
            else:
                self.text(f"Fetching {td.topic_name(self.category)} questions…", self.f_med, TEXT_SEC,
                          (SCREEN_W // 2, 360), "center")
                if err:
                    self.text(f"({err} - retrying)", self.f_small, TEXT_DIM, (SCREEN_W // 2, 392), "center")
            return self.draw_bar()

        # Header: question number, topic and difficulty, score; a dot per question
        y = 58
        self.text(f"Question {self.i + 1} of {len(self.questions)}", self.f_medb, TEXT_PRI, (PAD, y))
        score = sum(self.results)
        self.text(f"Score {score}", self.f_medb, ACCENT, (SCREEN_W - PAD, y), "topright")
        n = len(self.questions)
        w = (SCREEN_W - 2 * PAD - (n - 1) * 4) / n
        for k in range(n):
            col = (RIGHT if self.results[k] else WRONG) if k < len(self.results) else \
                TEXT_PRI if k == self.i else BG_CARD_HI
            pygame.draw.rect(self.screen, col, (round(PAD + k * (w + 4)), y + 30, round(w), 5), border_radius=2)

        r = pygame.Rect(PAD, Q_Y, SCREEN_W - 2 * PAD, Q_H)
        self.card(r)
        lv = LEVEL_COLOURS.get(q["difficulty"], TEXT_SEC)
        tr = self.text(q["topic"], self.f_tinyb, TEXT_SEC, (r.x + 14, r.y + 12))
        self.text(q["difficulty"], self.f_tinyb, lv, (tr.right + 10, r.y + 12))
        f, lines = self.fit(q["q"], r.w - 32, r.h - 50, [28, 25, 22, 20, 18])
        self.block(lines, f, TEXT_PRI, pygame.Rect(r.x, r.y + 34, r.w, r.h - 44))

        # Answers: four buttons, or two big ones for true/false
        n = len(q["answers"])
        h = (BAR_Y - 14 - ANS_Y - (n - 1) * ANS_GAP) // n if n > 2 else 140
        for k, ans in enumerate(q["answers"]):
            br = pygame.Rect(PAD, ANS_Y + k * (h + ANS_GAP), SCREEN_W - 2 * PAD, h)
            fill, ink = BG_CARD_HI, TEXT_PRI
            if self.picked is not None:
                if ans == q["correct"]:
                    fill, ink = RIGHT, TEXT_PRI
                elif k == self.picked:
                    fill, ink = WRONG, TEXT_PRI
                else:
                    fill, ink = BG_CARD, TEXT_DIM
            self.card(br, fill, radius=14)
            letter = "ABCD"[k] if n > 2 else ""
            x0 = br.x + (52 if letter else 16)
            if letter:
                pygame.draw.circle(self.screen, BG if self.picked is None else fill, (br.x + 28, br.centery), 16)
                self.text(letter, self.f_smallb, ACCENT if self.picked is None else ink, (br.x + 28, br.centery), "center")
            f, lines = self.fit(ans, br.right - 16 - x0, br.h - 12, [24, 21, 18, 16] if n > 2 else [40, 32])
            area = pygame.Rect(x0, br.y, br.right - 16 - x0, br.h)
            if letter:
                lh = f.get_linesize()
                yy = area.centery - lh * len(lines) / 2
                for j, line in enumerate(lines):
                    self.text(line, f, ink, (x0, round(yy + j * lh)))
            else:
                self.block(lines, f, ink, area)
            if self.picked is None:
                self.buttons[f"answer{k}"] = br
        last = self.i == len(self.questions) - 1
        self.draw_bar(None if self.picked is None else "Results ›" if last else "Next ›")

    # ── Results ───────────────────────────────────────────────────────────────

    def draw_done(self, now):
        score, total = sum(self.results), len(self.results)
        level = "all levels" if self.difficulty == "any" else self.difficulty
        self.text(f"{td.topic_name(self.category)} · {level}", self.f_medb, TEXT_SEC,
                  (SCREEN_W // 2, 74), "midtop")
        r = self.text(f"{score}", self.f_huge, ACCENT, (SCREEN_W // 2 - 10, 108), "topright")
        self.text(f"/ {total}", self.f_big, TEXT_SEC, (SCREEN_W // 2 + 2, r.bottom - 50))
        self.text(td.verdict(score, total), self.f_big, TEXT_PRI, (SCREEN_W // 2, r.bottom + 10), "midtop")
        y = r.bottom + 66
        row_h = min(46, (BAR_Y - 12 - y) // max(1, total))
        for k, (q, ok) in enumerate(zip(self.questions, self.results)):
            ry = y + k * row_h
            pygame.draw.circle(self.screen, RIGHT if ok else WRONG, (PAD + 10, ry + row_h // 2), 9)
            self.text("✓" if ok else "✗", self.f_tinyb, TEXT_PRI, (PAD + 10, ry + row_h // 2), "center")
            self.text(truncate(q["q"], self.f_small, SCREEN_W - 2 * PAD - 30), self.f_small, TEXT_SEC,
                      (PAD + 28, ry + 2))
            self.text(truncate(q["correct"], self.f_tinyb, SCREEN_W - 2 * PAD - 30), self.f_tinyb,
                      RIGHT if ok else TEXT_PRI, (PAD + 28, ry + 22))
        self.draw_bar("Play again")

    # ── Topics ────────────────────────────────────────────────────────────────

    def draw_pick(self, now):
        self.text("Pick a topic", self.f_big, TEXT_PRI, (PAD, 58))
        cols, gap = 2, 8
        cw = (SCREEN_W - 2 * PAD - gap) / cols
        ch, top = 36, 104
        for k, (cat, name) in enumerate(td.TOPICS):
            row, col = divmod(k, cols)
            r = pygame.Rect(round(PAD + col * (cw + gap)), top + row * (ch + 6), round(cw), ch)
            sel = cat == self.category
            self.card(r, ACCENT if sel else BG_CARD, radius=10)
            self.text(name, self.f_smallb, INK if sel else TEXT_SEC, r.center, "center")
            self.buttons[f"cat:{cat}"] = r
        y = top + ((len(td.TOPICS) + 1) // 2) * (ch + 6) + 8
        w = (SCREEN_W - 2 * PAD - 3 * gap) / 4
        for k, lv in enumerate(td.DIFFICULTIES):
            r = pygame.Rect(round(PAD + k * (w + gap)), y, round(w), 42)
            sel = lv == self.difficulty
            col = LEVEL_COLOURS.get(lv, ACCENT)
            self.card(r, col if sel else BG_CARD, radius=10)
            self.text(lv.capitalize(), self.f_smallb, INK if sel else col, r.center, "center")
            self.buttons[f"level:{lv}"] = r
        r = pygame.Rect(PAD, BAR_Y, SCREEN_W - 2 * PAD, BAR_H)
        stocked = self.store.count(self.topic)
        self.button("start", r, "Start" if stocked else "Start (fetching…)", ACCENT, INK, self.f_big)

    # ── Drawing a frame ───────────────────────────────────────────────────────

    def draw(self, now=None):
        now = now or datetime.now()
        self.buttons = {}
        self.screen.fill(BG)
        self.draw_status_bar(now)
        {"pick": self.draw_pick, "question": self.draw_question, "done": self.draw_done}[self.mode](now)
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
        elif hit == "topics":
            self.mode = "pick"
        elif hit and hit.startswith("cat:"):
            self.category = int(hit[4:])
            self.store.want = self.topic
        elif hit and hit.startswith("level:"):
            self.difficulty = hit[6:]
            self.store.want = self.topic
        elif hit == "start":
            self.store.remember(self.category, self.difficulty)
            self.start_round()
        elif hit == "again":
            self.store.reset_topic(self.topic)
        elif hit and hit.startswith("answer"):
            self.picked = int(hit[6:])
            q = self.question
            self.results.append(q["answers"][self.picked] == q["correct"])
        elif hit == "next":
            if self.mode == "done":
                self.start_round()
            elif self.i + 1 < len(self.questions):
                self.i, self.picked = self.i + 1, None
            else:
                self.mode = "done"

    def tick(self):
        """Idle bookkeeping: dimming, and starting a round once questions arrive."""
        self.dimmed = time.time() - self.last_touch > DIM_AFTER
        if self.mode == "question" and not self.questions and self.store.count(self.topic):
            self.start_round()

    # ── Main loop ─────────────────────────────────────────────────────────────

    def state(self, now):
        """Everything on screen depends on: where we are in the round, dimming, the minute, the stock."""
        return (self.mode, self.category, self.difficulty, self.i, self.picked, len(self.questions),
                self.dimmed, self.sleeping, now.strftime("%H:%M"), self.store.stamp())

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
        # SDL also turns touches into mouse events; take the finger ones only. A tap counts
        # where the finger lands (no swiping here).
        if event.type == pygame.FINGERDOWN:
            self.tap(self.display.point(event))
        elif event.type == pygame.MOUSEBUTTONDOWN and not getattr(event, "touch", False):
            self.tap(self.display.point(event))
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
    cfg = td.load_config()
    store = td.Store().start()
    TriviaDisplay(cfg, store).run()
