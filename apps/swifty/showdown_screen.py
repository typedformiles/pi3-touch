"""Song Showdown in the Swifty app: two songs, tap the one you love more (pygame).

Views along the top: Battle (the pair, from one era or all), Top songs (every song
battled, best first) and Eras (the albums by their songs' average rating).
"""
import random
import time

import pygame

from swifty_ui import (ACCENT, BG, BG_CARD_HI, GOLD, PAD, SCREEN_W, TEXT_DIM, TEXT_PRI, TEXT_SEC,
                       ink_on, mix, truncate, wrap)

VIEWS = ["Battle", "Top songs", "Eras"]
HEAD_Y = 110
CARDS = [pygame.Rect(PAD, 170, SCREEN_W - 2 * PAD, 236), pygame.Rect(PAD, 434, SCREEN_W - 2 * PAD, 236)]
BAR_Y, BAR_H = 680, 48
FLASH = 0.9                  # seconds the winner glows before the next pair
ROW_H, PER_PAGE = 50, 10
MEDALS = [GOLD, (210, 214, 224), (222, 156, 100)]


class ShowdownScreen:
    def __init__(self, ui, store):
        self.ui, self.store = ui, store
        self.view = "Battle"
        self.picking = False         # choosing the era to battle in
        self.flash = None            # (pair on screen, winner, until) just after a pick
        self.first = 0               # first row shown on Top songs

    def background(self, now):
        return BG

    # ── Drawing ───────────────────────────────────────────────────────────────

    def draw(self, now):
        ui = self.ui
        ui.segments(VIEWS, self.view, ACCENT, BG)
        if self.view == "Top songs":
            self.draw_top()
        elif self.view == "Eras":
            self.draw_eras()
        elif self.picking:
            ui.text("Battle songs from…", ui.serif(30, True, True), TEXT_PRI, (PAD, HEAD_Y))
            ui.era_grid(self.store.albums, self.store.era, 172)
        else:
            self.draw_battle()

    def draw_battle(self):
        ui = self.ui
        ui.text("Which do you love more?", ui.serif(30, True, True), TEXT_PRI, (PAD, HEAD_Y - 2))
        era = self.store.era
        r = ui.text(f"{self.short(era) if era else 'All eras'}  ›", ui.font(15, True), ACCENT, (PAD + 2, HEAD_Y + 36))
        ui.buttons["eras"] = r.inflate(24, 24)
        n = self.store.battles
        ui.text(f"{n} battle{'s' * (n != 1)}", ui.font(15), TEXT_SEC, (SCREEN_W - PAD, HEAD_Y + 36), "topright")

        if self.flash:
            titles, winner, _ = self.flash
            pair = [self.store.by_title[t] for t in titles]
        else:
            pair, winner = self.store.pair(), None
        for k, (song, rect) in enumerate(zip(pair, CARDS)):
            self.draw_card(song, rect, winner)
            if not winner:
                ui.buttons[f"pick:{k}"] = rect
        c = (SCREEN_W // 2, (CARDS[0].bottom + CARDS[1].top) // 2)
        pygame.draw.circle(ui.screen, BG, c, 28)
        pygame.draw.circle(ui.screen, GOLD, c, 24)
        ui.text("VS", ui.font(18, True), BG, c, "center")

        w = 150
        if self.store.can_undo() and not winner:
            ui.button("undo", pygame.Rect(PAD, BAR_Y, w, BAR_H), "↶ Undo", BG_CARD_HI, TEXT_SEC)
        if not winner:
            ui.button("skip", pygame.Rect(SCREEN_W - PAD - w, BAR_Y, w, BAR_H), "Skip ›", BG_CARD_HI, TEXT_SEC)
            ui.text("Can't choose?", ui.font(13), TEXT_DIM, (SCREEN_W - PAD - w - 10, BAR_Y + BAR_H // 2),
                    "midright")

    def draw_card(self, song, rect, winner):
        """A song as a card in its era's colour; just after a pick, the winner glows."""
        ui = self.ui
        c = song["colour"]
        ui.card(rect, mix(BG, c, 0.3), radius=20)
        pygame.draw.rect(ui.screen, c, (rect.x, rect.y, 14, rect.h), border_top_left_radius=20,
                         border_bottom_left_radius=20)
        ui.text(f"{song['short']} · {song['year']}", ui.font(15, True), c, (rect.x + 30, rect.y + 16))
        rating, battles, wins = self.store.rec(song["title"])
        rec = "New!" if not battles else f"won {wins} of {battles}"
        ui.text(rec, ui.font(14), TEXT_SEC, (rect.right - 18, rect.y + 17), "topright")

        box = pygame.Rect(rect.x + 30, rect.y + 44, rect.w - 50, rect.h - 64)
        for size in (46, 40, 35, 31, 27, 24):
            f = ui.serif(size, True, True)
            lines = wrap(song["title"], f, box.w)
            if len(lines) * f.get_linesize() <= box.h:
                break
        lh = f.get_linesize()
        y = box.centery - lh * len(lines) / 2
        for i, line in enumerate(lines):
            ui.text(line, f, TEXT_PRI, (box.centerx, round(y + i * lh + lh / 2)), "center")

        if winner == song["title"]:
            ui.card(rect, GOLD, radius=20, width=5)
            rnd = random.Random(song["title"])
            for k in range(12):                      # down both sides, clear of the words
                x = rnd.randrange(rect.x + 24, rect.x + 40) if k % 2 else \
                    rnd.randrange(rect.right - 30, rect.right - 14)
                ui.star(rnd.choice([GOLD, TEXT_PRI, c]), (x, rnd.randrange(rect.y + 48, rect.bottom - 14)),
                        rnd.randrange(5, 12))
            badge = pygame.Rect(0, 0, 130, 36)
            badge.midbottom = (rect.centerx, rect.bottom - 12)
            ui.card(badge, GOLD, radius=18)
            ui.text("♥ Winner", ui.font(17, True), BG, badge.center, "center")
        elif winner:
            veil = pygame.Surface(rect.size, pygame.SRCALPHA)
            veil.fill((*BG, 150))
            ui.screen.blit(veil, rect)

    def draw_top(self):
        ui = self.ui
        rows = self.store.ranking()
        ui.text("Your top songs", ui.serif(30, True, True), TEXT_PRI, (PAD, HEAD_Y - 2))
        if not rows:
            ui.text("Battle some songs to start", ui.font(18), TEXT_PRI, (SCREEN_W // 2, 360), "center")
            ui.text("your very own ranking!", ui.font(18), TEXT_PRI, (SCREEN_W // 2, 388), "center")
            return
        ui.text(f"{len(rows)} of {len(self.store.songs)} songs rated", ui.font(15), TEXT_SEC, (PAD + 2, HEAD_Y + 36))
        self.first = min(self.first, (len(rows) - 1) // PER_PAGE * PER_PAGE)
        for k, (song, rating, battles, wins) in enumerate(rows[self.first:self.first + PER_PAGE]):
            rank = self.first + k + 1
            y = 172 + k * ROW_H
            medal = MEDALS[rank - 1] if rank <= 3 else None
            pygame.draw.circle(ui.screen, medal or BG_CARD_HI, (PAD + 18, y + 22), 17)
            ui.text(str(rank), ui.font(16 if rank < 100 else 13, True), BG if medal else TEXT_PRI, (PAD + 18, y + 22),
                    "center")
            f = ui.serif(23, rank <= 3, True)
            ui.text(truncate(song["title"], f, SCREEN_W - 2 * PAD - 50), f, TEXT_PRI, (PAD + 48, y + 1))
            pygame.draw.circle(ui.screen, song["colour"], (PAD + 53, y + 35), 5)
            ui.text(f"{song['short']} · won {wins} of {battles}", ui.font(13), TEXT_SEC, (PAD + 64, y + 27))
        if len(rows) > PER_PAGE:
            last = min(self.first + PER_PAGE, len(rows))
            ui.text(f"{self.first + 1}–{last} of {len(rows)}", ui.font(15), TEXT_SEC,
                    (SCREEN_W // 2, BAR_Y + BAR_H // 2), "center")
            if self.first:
                ui.button("higher", pygame.Rect(PAD, BAR_Y, 130, BAR_H), "‹ Higher", BG_CARD_HI, TEXT_PRI)
            if last < len(rows):
                ui.button("lower", pygame.Rect(SCREEN_W - PAD - 130, BAR_Y, 130, BAR_H), "Lower ›", BG_CARD_HI,
                          TEXT_PRI)

    def draw_eras(self):
        ui = self.ui
        eras = self.store.eras()
        ui.text("Your favourite eras", ui.serif(30, True, True), TEXT_PRI, (PAD, HEAD_Y - 2))
        rated = [e[1] for e in eras if e[1] is not None]
        if not rated:
            ui.text("Battle some songs to see", ui.font(18), TEXT_PRI, (SCREEN_W // 2, 360), "center")
            ui.text("which era comes out on top!", ui.font(18), TEXT_PRI, (SCREEN_W // 2, 388), "center")
            return
        lo, hi = min(rated), max(rated)
        full = SCREEN_W - 2 * PAD - 30
        for k, (album, avg, n, top) in enumerate(eras):
            y = 156 + k * 47
            c = album["colour"]
            ui.text(str(k + 1) if avg is not None else "", ui.font(16, True), TEXT_SEC, (PAD + 10, y + 13), "center")
            if avg is None:
                bar = pygame.Rect(PAD + 30, y, full, 26)
                ui.card(bar, mix(BG, c, 0.2), radius=13)
                ui.text(album["short"], ui.font(15, True), TEXT_DIM, (bar.x + 12, bar.centery), "midleft")
                ui.text("not rated yet", ui.font(13), TEXT_DIM, (bar.right - 12, bar.centery), "midright")
                continue
            frac = 1 if hi == lo else 0.4 + 0.6 * (avg - lo) / (hi - lo)
            bar = pygame.Rect(PAD + 30, y, round(full * frac), 26)
            ui.card(bar, c, radius=13)
            ui.text(album["short"], ui.font(15, True), ink_on(c), (bar.x + 12, bar.centery), "midleft")
            if k == 0:
                ui.star(GOLD, (bar.right - 16, bar.centery), 10)
            fave = f"♥ {top['title']}" + (f"  ·  {n} songs rated" if n > 1 else "")
            ui.text(truncate(fave, ui.font(13), full - 10), ui.font(13), TEXT_SEC, (PAD + 42, y + 29))

    # ── Helpers ───────────────────────────────────────────────────────────────

    def short(self, era):
        return next((a["short"] for a in self.store.albums if a["title"] == era), era)

    # ── Touch ─────────────────────────────────────────────────────────────────

    def tap(self, hit, now):
        if hit.startswith("view:"):
            self.view, self.picking, self.first = hit[5:], False, 0
        elif hit.startswith("pick:"):
            titles = [s["title"] for s in self.store.pair()]
            winner = titles[int(hit[5:])]
            self.store.pick(winner)
            self.flash = (titles, winner, time.monotonic() + FLASH)
        elif hit == "skip":
            self.store.skip()
        elif hit == "undo":
            self.store.undo()
        elif hit == "eras":
            self.picking = True
        elif hit.startswith("era:"):
            self.store.set_era(hit[4:] or None)
            self.picking = False
        elif hit == "higher":
            self.first = max(0, self.first - PER_PAGE)
        elif hit == "lower":
            self.first += PER_PAGE

    def key(self, ch, now):
        pass

    def tick(self, now):
        if self.flash and time.monotonic() >= self.flash[2]:
            self.flash = None

    def state(self, now):
        return (self.view, self.picking, self.flash is not None, tuple(self.store.mem.get("pair") or ()),
                self.store.battles, self.store.era, self.store.can_undo(), self.first)
