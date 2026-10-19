"""Swiftle in the Swifty app: guess the day's song title letter by letter (pygame).

Views along the top: Today (everyone's puzzle for the day), Practice (as many as you like,
from one era or all) and Stats (the streak, and how many misses the wins took).
"""
import random
from datetime import datetime, timedelta

import pygame

import swifty_data as sd
from swifty_ui import (ACCENT, BEADS, BG, BG_CARD, BG_CARD_HI, GOLD, PAD, RIGHT, SCREEN_W, TEXT_DIM, TEXT_PRI,
                       TEXT_SEC, WRONG, ink_on, mix, truncate)

VIEWS = ["Today", "Practice", "Stats"]
VIEW_Y, VIEW_H = 60, 44
HEAD_Y = 116                 # puzzle number / beads
CLUE_Y, CLUE_H = 166, 56
TILES_Y, TILES_H = 232, 210
KEYS_Y = 454
KEY_H, KEY_GAP, KEY_X = 82, 6, 6
ROWS = ["QWERTYUIOP", "ASDFGHJKL", "ZXCVBNM"]
CLUE_NAMES = {"colour": "Era colour", "year": "Year", "album": "Album", "track": "Track"}
TILE_SIZES = (48, 44, 40, 36, 33, 30, 27, 24)


def cell(ch, s):
    """Width of one character's cell: letters and digits get a tile, punctuation less."""
    return s if ch.isalnum() else round(s * 0.45)


def word_width(word, s, gap):
    return sum(cell(c, s) for c in word) + gap * (len(word) - 1)


def tile_layout(title, width, height, sizes=TILE_SIZES):
    """(tile size, gap, space between words, row height, lines of words): the biggest tiles
    that fit the title in the box, breaking lines between words."""
    words = title.upper().split()
    for s in sizes:
        gap, space, row_h = max(2, s // 10), max(10, s // 2), s + s // 4
        lines, line, w = [], [], 0
        for word in words:
            ww = word_width(word, s, gap)
            if line and w + space + ww > width:
                lines.append(line)
                line, w = [word], ww
            else:
                w = ww if not line else w + space + ww
                line.append(word)
        lines += [line] if line else []
        widest = max(sum(word_width(x, s, gap) for x in ln) + space * (len(ln) - 1) for ln in lines)
        if widest <= width and len(lines) * row_h - s // 4 <= height:
            return s, gap, space, row_h, lines
    return s, gap, space, row_h, lines


class SwiftleScreen:
    def __init__(self, ui, store):
        self.ui, self.store = ui, store
        self.view = "Today"
        self.picking = store.practice() is None      # Practice shows the eras until a game starts

    # ── Which game ────────────────────────────────────────────────────────────

    def game(self, now):
        if self.view == "Today":
            return self.store.today(now.date())
        if self.view == "Practice" and not self.picking:
            return self.store.practice()
        return None

    def theme(self, game):
        """(background, accent): the era's colours once its colour clue is open."""
        if game and game.clue("colour"):
            c = game.song["colour"]
            return mix(BG, c, 0.16), c
        return BG, ACCENT

    def background(self, now):
        return self.theme(self.game(now))[0]

    # ── Drawing ───────────────────────────────────────────────────────────────

    def draw(self, now):
        ui = self.ui
        game = self.game(now)
        bg, accent = self.theme(game)
        w = (SCREEN_W - 2 * PAD) / len(VIEWS)
        ui.card(pygame.Rect(PAD, VIEW_Y, SCREEN_W - 2 * PAD, VIEW_H), mix(bg, BG_CARD, 0.8), radius=22)
        for i, v in enumerate(VIEWS):
            r = pygame.Rect(round(PAD + i * w) + 3, VIEW_Y + 3, round(w) - 6, VIEW_H - 6)
            sel = v == self.view
            if sel:
                ui.card(r, accent, radius=19)
            ui.text(v, ui.font(18, True), ink_on(accent) if sel else TEXT_SEC, r.center, "center")
            ui.buttons[f"view:{v}"] = r
        if self.view == "Stats":
            self.draw_stats(now)
        elif game is None:
            self.draw_eras()
        else:
            self.draw_game(game, now, bg, accent)

    def draw_game(self, game, now, bg, accent):
        ui = self.ui
        if game.day:
            ui.text(f"Swiftle #{sd.puzzle_number(game.day)}", ui.serif(30, True, True), TEXT_PRI, (PAD, HEAD_Y - 6))
            ui.text(f"{game.day:%a} {game.day.day} {game.day:%b}", ui.font(14), TEXT_SEC, (PAD + 2, HEAD_Y + 26))
        else:
            ui.text("Practice", ui.serif(30, True, True), TEXT_PRI, (PAD, HEAD_Y - 6))
            era = self.store.era
            r = ui.text(f"{self.short(era) if era else 'Any era'}  ›", ui.font(14, True), accent,
                        (PAD + 2, HEAD_Y + 26))
            ui.buttons["eras"] = r.inflate(24, 24)
        self.draw_beads(game, accent)
        self.draw_clues(game, bg, accent)
        if game.solved:
            self.draw_sparkles(game, accent)
        self.draw_tiles(game, bg, accent)
        if game.over:
            self.draw_result(game, now, accent)
        else:
            self.draw_keys(game, accent)

    def draw_beads(self, game, accent):
        """Lives as a bracelet of beads: a miss takes one off from the right."""
        r, step = 11, 28
        x0 = SCREEN_W - PAD - r - step * (sd.LIVES - 1)
        y = HEAD_Y + 18
        pygame.draw.line(self.ui.screen, TEXT_DIM, (x0 - r - 4, y), (x0 + step * (sd.LIVES - 1) + r + 4, y), 2)
        for k in range(sd.LIVES):
            c = (x0 + k * step, y)
            if k < game.lives:
                pygame.draw.circle(self.ui.screen, BEADS[k % len(BEADS)], c, r)
                pygame.draw.circle(self.ui.screen, (255, 255, 255), (c[0] - 4, c[1] - 4), 3)
            else:
                pygame.draw.circle(self.ui.screen, BG, c, r)
                pygame.draw.circle(self.ui.screen, TEXT_DIM, c, r, 2)

    def draw_clues(self, game, bg, accent):
        ui = self.ui
        n, gap = len(sd.CLUES), 8
        w = (SCREEN_W - 2 * PAD - (n - 1) * gap) / n
        for i, (name, after) in enumerate(sd.CLUES):
            r = pygame.Rect(round(PAD + i * (w + gap)), CLUE_Y, round(w), CLUE_H)
            open_ = game.clue(name)
            ui.card(r, mix(bg, BG_CARD, 0.85) if open_ else mix(bg, BG_CARD, 0.4), radius=12)
            ui.text(CLUE_NAMES[name], ui.font(12, True), TEXT_SEC if open_ else TEXT_DIM, (r.centerx, r.y + 7),
                    "midtop")
            mid = (r.centerx, r.y + 36)
            if not open_:
                ui.text(f"after {after} ✗", ui.font(13), TEXT_DIM, mid, "center")
            elif name == "colour":                  # a swatch, named so it reads as a clue
                c = game.song["colour"]
                pill = pygame.Rect(r.x + 8, r.y + 25, r.w - 16, 24)
                pygame.draw.rect(ui.screen, c, pill, border_radius=12)
                f, s = ui.shrink(game.song.get("colour_name", ""), pill.w - 10, [15, 13, 12])
                ui.text(s, f, ink_on(c), pill.center, "center")
            else:
                value = {"year": str(game.song["year"]), "album": game.song["short"],
                         "track": str(game.song["track"])}[name]
                f, s = ui.shrink(value, r.w - 12, [22, 19, 17, 15, 13])
                ui.text(s, f, TEXT_PRI, mid, "center")

    def draw_tiles(self, game, bg, accent):
        ui = self.ui
        width = SCREEN_W - 2 * PAD
        s, gap, space, row_h, lines = tile_layout(game.song["title"], width, TILES_H)
        f = ui.font(round(s * 0.6), True)
        top = TILES_Y + (TILES_H - (len(lines) * row_h - s // 4)) // 2
        hidden_fill = mix(bg, BG_CARD_HI, 0.9)
        shown_fill = mix(accent, bg, 0.45)
        for li, line in enumerate(lines):
            lw = sum(word_width(x, s, gap) for x in line) + space * (len(line) - 1)
            x, y = PAD + (width - lw) // 2, top + li * row_h
            for word in line:
                for ch in word:
                    cw = cell(ch, s)
                    r = pygame.Rect(x, y, cw, s)
                    if ch.isalpha() and "A" <= ch <= "Z":
                        shown = game.shows(ch)
                        if shown:
                            ui.card(r, shown_fill, radius=max(4, s // 7))
                            ui.text(ch, f, ink_on(shown_fill), r.center, "center")
                        elif game.lost:                     # the letters that weren't found, greyed
                            ui.card(r, hidden_fill, radius=max(4, s // 7))
                            ui.text(ch, f, TEXT_DIM, r.center, "center")
                        else:
                            ui.card(r, hidden_fill, radius=max(4, s // 7))
                    else:
                        ui.text(ch, f, TEXT_PRI, r.center, "center")
                    x += cw + gap
                x += space - gap

    def draw_sparkles(self, game, accent):
        """Stars around the tiles for a win (the same ones every redraw)."""
        rnd = random.Random(game.song["title"])
        for _ in range(16):
            x = rnd.randrange(PAD, SCREEN_W - PAD)
            y = rnd.randrange(TILES_Y - 4, TILES_Y + TILES_H + 4)
            self.ui.star(rnd.choice([GOLD, accent, TEXT_PRI, BEADS[0]]), (x, y), rnd.randrange(4, 11))

    def draw_keys(self, game, accent):
        ui = self.ui
        kw = (SCREEN_W - 2 * KEY_X - 9 * KEY_GAP) / 10
        f = ui.font(26, True)
        for row, keys in enumerate(ROWS):
            x0 = KEY_X + (10 - len(keys)) * (kw + KEY_GAP) / 2
            y = KEYS_Y + row * (KEY_H + KEY_GAP)
            for k, ch in enumerate(keys):
                r = pygame.Rect(round(x0 + k * (kw + KEY_GAP)), y, round(kw), KEY_H)
                if ch not in game.guesses:
                    ui.card(r, BG_CARD_HI, radius=10)
                    ui.text(ch, f, TEXT_PRI, r.center, "center")
                    ui.buttons[f"key:{ch}"] = r
                elif ch in game.hidden:
                    ui.card(r, accent, radius=10)
                    ui.text(ch, f, ink_on(accent), r.center, "center")
                else:
                    ui.card(r, BG, radius=10)
                    ui.text(ch, f, TEXT_DIM, r.center, "center")

    def draw_result(self, game, now, accent):
        ui = self.ui
        song = game.song
        r = pygame.Rect(PAD, KEYS_Y, SCREEN_W - 2 * PAD, 3 * KEY_H + 2 * KEY_GAP)
        ui.card(r, BG_CARD, radius=18)
        misses = len(game.misses)
        if game.solved:
            head = "Flawless!" if misses == 0 else "You got it!"
            sub = "No wrong letters" if misses == 0 else f"{misses} wrong letter{'s' * (misses != 1)}"
        else:
            head, sub = "So close!", "It was…"
        ui.text(head, ui.serif(40, True, True), GOLD if game.solved else TEXT_PRI, (r.centerx, r.y + 14), "midtop")
        ui.text(sub, ui.font(16), TEXT_SEC, (r.centerx, r.y + 62), "midtop")
        f, s = ui.shrink(song["title"], r.w - 28, [36, 32, 28, 25, 22, 20], serif=True, italic=True)
        ui.text(s, f, accent, (r.centerx, r.y + 104), "center")
        ui.text(f"Track {song['track']} on {song['album']} ({song['year']})"
                if len(song["album"]) < 24 else f"Track {song['track']}, {song['short']} ({song['year']})",
                ui.font(16), TEXT_SEC, (r.centerx, r.y + 136), "midtop")
        by = r.bottom - 76
        if game.day:
            st = self.store.stats(now.date())
            ui.text(f"Streak {st['streak']}", ui.font(18, True), TEXT_PRI, (r.x + 18, by + 4))
            ui.text(f"Next one in {self.until_midnight(now)}", ui.font(14), TEXT_SEC, (r.x + 18, by + 32))
            ui.button("practice", pygame.Rect(r.right - 196, by, 180, 60), "Practice ›", accent, ink_on(accent))
        else:
            ui.button("again", pygame.Rect(r.x + 16, by, r.w - 32, 60), "Next song ›", accent, ink_on(accent),
                      ui.font(22, True))

    def draw_eras(self):
        """Practice: which era the songs come from."""
        ui = self.ui
        ui.text("Pick an era", ui.serif(30, True, True), TEXT_PRI, (PAD, HEAD_Y - 6))
        ui.text("Practice games don't count towards the streak", ui.font(14), TEXT_SEC, (PAD + 2, HEAD_Y + 30))
        albums = self.store.albums
        options = [None] + [a["title"] for a in albums]
        cols, gap, h, top = 2, 8, 50, 172
        w = (SCREEN_W - 2 * PAD - gap) / cols
        for k, era in enumerate(options):
            row, col = divmod(k, cols)
            r = pygame.Rect(round(PAD + col * (w + gap)), top + row * (h + gap), round(w), h)
            if era is None:
                ui.card(r, BG_CARD_HI, radius=12)
                for j, c in enumerate(BEADS):
                    pygame.draw.circle(ui.screen, c, (r.x + 18 + j * 9, r.centery), 5)
                ui.text("Any era", ui.font(18, True), TEXT_PRI, (r.x + 80, r.centery), "midleft")
            else:
                a = next(x for x in albums if x["title"] == era)
                ui.card(r, mix(BG, a["colour"], 0.22), radius=12)
                pygame.draw.rect(ui.screen, a["colour"], (r.x, r.y, 10, r.h), border_top_left_radius=12,
                                 border_bottom_left_radius=12)
                f, s = ui.shrink(a["short"], r.w - 80, [18, 16, 14])
                ui.text(s, f, TEXT_PRI, (r.x + 22, r.centery), "midleft")
                ui.text(str(a["year"]), ui.font(13), TEXT_SEC, (r.right - 12, r.centery), "midright")
            if era == self.store.era and self.store.practice():
                ui.card(r, TEXT_PRI, radius=12, width=2)
            ui.buttons[f"era:{era or ''}"] = r

    def draw_stats(self, now):
        ui = self.ui
        st = self.store.stats(now.date())
        y = HEAD_Y
        boxes = [("Played", st["played"]), ("Won", f"{round(100 * st['won'] / st['played'])}%" if st["played"]
                                            else "-"), ("Streak", st["streak"]), ("Best", st["best"])]
        w = (SCREEN_W - 2 * PAD - 3 * 8) / 4
        for i, (label, value) in enumerate(boxes):
            r = pygame.Rect(round(PAD + i * (w + 8)), y, round(w), 84)
            ui.card(r, BG_CARD, radius=12)
            ui.text(str(value), ui.font(32, True), GOLD if label == "Streak" else TEXT_PRI, (r.centerx, r.y + 10),
                    "midtop")
            ui.text(label, ui.font(14), TEXT_SEC, (r.centerx, r.bottom - 10), "midbottom")

        y += 104
        ui.text("Wrong letters in a win", ui.font(16, True), TEXT_SEC, (PAD, y))
        today = st["recent"][0] if st["recent"] and st["recent"][0][0] == now.date().isoformat() else None
        most = max(st["dist"]) or 1
        for k, count in enumerate(st["dist"]):
            ry = y + 28 + k * 34
            ui.text(str(k), ui.font(18, True), TEXT_PRI, (PAD + 6, ry + 13), "midleft")
            full = SCREEN_W - 2 * PAD - 34
            bw = max(30, round(full * count / most))
            hi = today and today[2] and today[3] == k
            ui.card(pygame.Rect(PAD + 30, ry, bw, 26), ACCENT if hi else BG_CARD_HI, radius=6)
            ui.text(str(count), ui.font(15, True), BG if hi else TEXT_PRI, (PAD + 30 + bw - 8, ry + 13), "midright")

        y += 28 + 6 * 34 + 14
        ui.text("Recent days", ui.font(16, True), TEXT_SEC, (PAD, y))
        if not st["recent"]:
            ui.text("Play today's Swiftle to start a streak!", ui.font(18), TEXT_PRI, (SCREEN_W // 2, y + 80),
                    "center")
            return
        for k, (d, title, won, misses) in enumerate(st["recent"][:5]):
            ry = y + 28 + k * 42
            day = datetime.fromisoformat(d).date()
            pygame.draw.circle(ui.screen, RIGHT if won else WRONG, (PAD + 12, ry + 14), 11)
            ui.text("✓" if won else "✗", ui.font(14, True), TEXT_PRI, (PAD + 12, ry + 14), "center")
            ui.text(f"#{sd.puzzle_number(day)}", ui.font(15, True), TEXT_SEC, (PAD + 32, ry + 14), "midleft")
            ui.text(f"{day:%a} {day.day} {day:%b}", ui.font(14), TEXT_DIM, (PAD + 80, ry + 14), "midleft")
            ui.text(truncate(title, ui.serif(22, False, True), SCREEN_W - PAD - 190), ui.serif(22, False, True),
                    TEXT_PRI, (PAD + 176, ry + 14), "midleft")

    # ── Helpers ───────────────────────────────────────────────────────────────

    def short(self, era):
        return next((a["short"] for a in self.store.albums if a["title"] == era), era)

    @staticmethod
    def until_midnight(now):
        left = datetime.combine(now.date() + timedelta(days=1), datetime.min.time()) - now
        h, m = divmod(int(left.total_seconds()) // 60, 60)
        return f"{h}h {m:02d}m" if h else f"{m}m"

    # ── Touch ─────────────────────────────────────────────────────────────────

    def tap(self, hit, now):
        if hit.startswith("view:"):
            self.view = hit[5:]
        elif hit.startswith("key:"):
            self.key(hit[4:], now)
        elif hit == "practice":
            self.view = "Practice"
            if not self.picking and self.store.practice() and self.store.practice().over:
                self.store.new_practice(self.store.era, now.date())
        elif hit == "again":
            self.store.new_practice(self.store.era, now.date())
        elif hit == "eras":
            self.picking = True
        elif hit.startswith("era:"):
            self.store.new_practice(hit[4:] or None, now.date())
            self.picking = False

    def key(self, ch, now):
        """A letter from the screen's keys (or a keyboard, off the Pi)."""
        game = self.game(now)
        if game:
            self.store.guess(game, ch)

    def state(self, now):
        today = self.store.today(now.date())
        p = self.store.practice()
        return (self.view, self.picking, today.day, today.guesses, p and (p.song["title"], p.guesses),
                self.store.era)
