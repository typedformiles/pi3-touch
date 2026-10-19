"""Swiftie Quiz in the Swifty app: rounds of ten questions about the songs (pygame).

Views along the top: Play (start a round from every era or one, answer, see the score)
and Scores (the best round in each era, rounds played, how many answers were right).
"""
import pygame

import swifty_data as sd
from swifty_ui import (ACCENT, BG, BG_CARD, BG_CARD_HI, GOLD, PAD, RIGHT, SCREEN_W, TEXT_DIM, TEXT_PRI, TEXT_SEC,
                       WRONG, ink_on, mix, truncate, wrap)

VIEWS = ["Play", "Scores"]
HEAD_Y = 110
CARD = pygame.Rect(PAD, 156, SCREEN_W - 2 * PAD, 196)
ANS_Y, ANS_H, ANS_GAP = 362, 68, 8
BAR_Y, BAR_H = 668, 58
LABELS = {"album_of": "Albums", "song_on": "Albums", "not_on": "Odd one out", "year": "Years",
          "first_album": "Years", "colour": "Era colours", "opener": "Openers", "track_no": "Track numbers",
          "fill": "Finish the title", "first_track": "Track order"}


class QuizScreen:
    def __init__(self, ui, store):
        self.ui, self.store = ui, store
        self.view = "Play"
        self.picking = False        # choosing the era for the next round

    def background(self, now):
        return BG

    # ── Drawing ───────────────────────────────────────────────────────────────

    def draw(self, now):
        ui = self.ui
        ui.segments(VIEWS, self.view, ACCENT, BG)
        if self.view == "Scores":
            self.draw_scores()
        elif self.picking:
            ui.text("Questions from…", ui.serif(30, True, True), TEXT_PRI, (PAD, HEAD_Y))
            ui.era_grid(self.store.albums, self.store.era, 172)
        elif self.store.done():
            self.draw_done()
        elif self.store.question():
            self.draw_question()
        else:
            self.draw_start()

    def era_name(self, era):
        return era or "All eras"

    def draw_start(self):
        ui = self.ui
        ui.text("Swiftie Quiz", ui.serif(44, True, True), TEXT_PRI, (SCREEN_W // 2, HEAD_Y + 10), "midtop")
        for i, line in enumerate(["Ten questions about the songs:", "albums, tracks, years and titles"]):
            ui.text(line, ui.font(17), TEXT_SEC, (SCREEN_W // 2, HEAD_Y + 72 + i * 24), "midtop")

        era = self.store.era
        r = pygame.Rect(PAD, 266, SCREEN_W - 2 * PAD, 76)
        colour = self.store.quiz.album_colour(era) if era else ACCENT
        ui.card(r, mix(BG, colour, 0.22), radius=16)
        pygame.draw.rect(ui.screen, colour, (r.x, r.y, 12, r.h), border_top_left_radius=16,
                         border_bottom_left_radius=16)
        ui.text("Questions from", ui.font(14), TEXT_SEC, (r.x + 28, r.y + 12))
        f, s = ui.shrink(self.era_name(era), r.w - 170, [26, 22, 19, 17])
        ui.text(s, f, TEXT_PRI, (r.x + 28, r.y + 34))
        ui.text("Change ›", ui.font(17, True), colour, (r.right - 18, r.centery), "midright")
        ui.buttons["eras"] = r

        best, played = self.store.best(era)
        y = 372
        for label, value in (("Best round", f"{best} / {sd.ROUND}" if best is not None else "-"),
                             ("Rounds played", str(played))):
            ui.text(label, ui.font(17), TEXT_SEC, (PAD + 12, y))
            ui.text(value, ui.font(22, True), GOLD if label == "Best round" else TEXT_PRI, (SCREEN_W - PAD - 12, y - 3),
                    "topright")
            y += 44
        for k in range(3):                              # a little sparkle
            ui.star([GOLD, ACCENT, TEXT_PRI][k], (SCREEN_W // 2 + (k - 1) * 60, 540 + (k % 2) * 20), 14 - 4 * (k % 2))
        ui.button("start", pygame.Rect(PAD, BAR_Y - 40, SCREEN_W - 2 * PAD, BAR_H + 40), "Start ›", ACCENT,
                  ink_on(ACCENT), ui.font(30, True), radius=20)

    def draw_question(self):
        ui = self.ui
        r = self.store.round
        q, pos, n = self.store.question(), r["pos"], len(r["questions"])
        picked = self.store.answered()
        ui.text(f"Question {pos + 1} of {n}", ui.font(18, True), TEXT_PRI, (PAD, HEAD_Y))
        ui.text(f"Score {self.store.score()}", ui.font(18, True), GOLD, (SCREEN_W - PAD, HEAD_Y), "topright")
        w = (SCREEN_W - 2 * PAD - (n - 1) * 4) / n
        for k in range(n):
            if k < len(r["answers"]):
                col = RIGHT if r["answers"][k] == r["questions"][k]["right"] else WRONG
            else:
                col = TEXT_PRI if k == pos else BG_CARD_HI
            pygame.draw.rect(ui.screen, col, (round(PAD + k * (w + 4)), HEAD_Y + 30, round(w), 6), border_radius=3)

        ui.card(CARD, BG_CARD, radius=18)
        ui.text(LABELS.get(q["kind"], ""), ui.font(13, True), ACCENT, (CARD.x + 16, CARD.y + 12))
        body = pygame.Rect(CARD.x + 16, CARD.y + 34, CARD.w - 32, (98 if picked is not None else CARD.h - 46))
        if q["kind"] == "fill":
            ui.text(q["q"], ui.font(18, True), TEXT_SEC, (CARD.centerx, body.y + 4), "midtop")
            f, s = ui.shrink(q["title"], body.w, [36, 32, 28, 25, 22, 19], serif=True, italic=True)
            ui.text(s, f, TEXT_PRI, (CARD.centerx, body.y + (52 if picked is not None else 70)), "center")
        elif q.get("swatch"):
            ui.text(q["q"], ui.font(21, True), TEXT_PRI, (CARD.centerx, body.y + 6), "midtop")
            pill = pygame.Rect(0, 0, 200, 40)
            pill.center = (CARD.centerx, body.y + (64 if picked is not None else 84))
            pygame.draw.rect(ui.screen, q["swatch"], pill, border_radius=20)
            pygame.draw.rect(ui.screen, TEXT_DIM, pill, 1, border_radius=20)
        else:
            for size in (27, 25, 23, 21, 19, 17):
                f = ui.font(size, True)
                lines = wrap(q["q"], f, body.w)
                if len(lines) * f.get_linesize() <= body.h:
                    break
            lh = f.get_linesize()
            y = body.centery - lh * len(lines) / 2
            for i, line in enumerate(lines):
                ui.text(line, f, TEXT_PRI, (body.centerx, round(y + i * lh + lh / 2)), "center")
        if picked is not None:
            ok = picked == q["right"]
            f = ui.font(15)
            lines = wrap(q["note"], f, CARD.w - 32)[:2]
            head = "Right!" if ok else "Not quite"
            ui.text(head, ui.font(16, True), RIGHT if ok else WRONG, (CARD.x + 16, CARD.bottom - 62))
            for i, line in enumerate(lines):
                ui.text(line, f, TEXT_SEC, (CARD.x + 16, CARD.bottom - 40 + i * 19))

        for k, ans in enumerate(q["answers"]):
            br = pygame.Rect(PAD, ANS_Y + k * (ANS_H + ANS_GAP), SCREEN_W - 2 * PAD, ANS_H)
            fill, ink = BG_CARD_HI, TEXT_PRI
            if picked is not None:
                if k == q["right"]:
                    fill = RIGHT
                elif k == picked:
                    fill = WRONG
                else:
                    fill, ink = BG_CARD, TEXT_DIM
            ui.card(br, fill, radius=14)
            x0 = br.x + 18
            if q.get("colours"):
                pygame.draw.rect(ui.screen, q["colours"][k], (br.x, br.y, 12, br.h), border_top_left_radius=14,
                                 border_bottom_left_radius=14)
                x0 = br.x + 28
            sizes = [30, 26] if q["kind"] in ("track_no", "year") else [22, 20, 18, 16, 14]
            f, s = ui.shrink(ans, br.right - 16 - x0, sizes)
            ui.text(s, f, ink, (x0, br.centery), "midleft")
            if picked is None:
                ui.buttons[f"answer:{k}"] = br
        if picked is not None:
            last = pos == n - 1
            ui.button("next", pygame.Rect(PAD, BAR_Y, SCREEN_W - 2 * PAD, BAR_H), "Results ›" if last else "Next ›",
                      ACCENT, ink_on(ACCENT), ui.font(24, True))

    def draw_done(self):
        ui = self.ui
        r = self.store.round
        score, total = self.store.score(), len(r["questions"])
        ui.text(self.era_name(r["era"]), ui.font(17, True), TEXT_SEC, (SCREEN_W // 2, HEAD_Y), "midtop")
        sr = ui.text(str(score), ui.font(84, True), GOLD, (SCREEN_W // 2 - 8, HEAD_Y + 24), "topright")
        ui.text(f"/ {total}", ui.font(30, True), TEXT_SEC, (SCREEN_W // 2 + 4, sr.bottom - 46))
        ui.text(sd.verdict(score, total), ui.serif(38, True, True), TEXT_PRI, (SCREEN_W // 2, sr.bottom + 2), "midtop")
        best, _ = self.store.best(r["era"])
        if best is not None:
            msg = "A new best!" if score == best and score > 0 else f"Best {best} / {total}"
            ui.text(msg, ui.font(15, True), GOLD if msg.startswith("A new") else TEXT_SEC,
                    (SCREEN_W // 2, sr.bottom + 50), "midtop")
        y, row_h = 300, 36
        for k, (q, a) in enumerate(zip(r["questions"], r["answers"])):
            ry = y + k * row_h
            ok = a == q["right"]
            pygame.draw.circle(ui.screen, RIGHT if ok else WRONG, (PAD + 10, ry + 17), 9)
            ui.text("✓" if ok else "✗", ui.font(13, True), TEXT_PRI, (PAD + 10, ry + 17), "center")
            line = q["title"] if q["kind"] == "fill" else q["q"]
            ui.text(truncate(line, ui.font(14), SCREEN_W - 2 * PAD - 30), ui.font(14), TEXT_SEC, (PAD + 28, ry + 1))
            ui.text(truncate(q["answers"][q["right"]], ui.font(14, True), SCREEN_W - 2 * PAD - 30), ui.font(14, True),
                    RIGHT if ok else TEXT_PRI, (PAD + 28, ry + 18))
        ui.button("again", pygame.Rect(PAD, BAR_Y, SCREEN_W - 2 * PAD - 150, BAR_H), "Play again ›", ACCENT,
                  ink_on(ACCENT), ui.font(22, True))
        ui.button("change", pygame.Rect(SCREEN_W - PAD - 140, BAR_Y, 140, BAR_H), "Change era", BG_CARD_HI,
                  TEXT_SEC, ui.font(16, True))

    def draw_scores(self):
        ui = self.ui
        m = self.store.mem
        ui.text("Your scores", ui.serif(30, True, True), TEXT_PRI, (PAD, HEAD_Y - 2))
        rounds = sum(m["played"].values())
        pct = f"{round(100 * m['right'] / m['asked'])}% right" if m["asked"] else "no answers yet"
        ui.text(f"{rounds} round{'s' * (rounds != 1)} · {pct}", ui.font(15), TEXT_SEC, (PAD + 2, HEAD_Y + 36))
        for k, era in enumerate([None] + [a["title"] for a in self.store.albums]):
            y = 168 + k * 42
            r = pygame.Rect(PAD, y, SCREEN_W - 2 * PAD, 36)
            colour = self.store.quiz.album_colour(era) if era else ACCENT
            ui.card(r, mix(BG, colour, 0.16), radius=10)
            pygame.draw.rect(ui.screen, colour, (r.x, r.y, 8, r.h), border_top_left_radius=10,
                             border_bottom_left_radius=10)
            name = next((a["short"] for a in self.store.albums if a["title"] == era), "All eras")
            ui.text(name, ui.font(16, True), TEXT_PRI, (r.x + 20, r.centery), "midleft")
            best, played = self.store.best(era)
            if played:
                ui.text(f"{played} played", ui.font(13), TEXT_SEC, (r.right - 100, r.centery), "midright")
                ui.text(f"{best} / {sd.ROUND}", ui.font(17, True), GOLD if best == sd.ROUND else TEXT_PRI,
                        (r.right - 14, r.centery), "midright")
            else:
                ui.text("not played", ui.font(13), TEXT_DIM, (r.right - 14, r.centery), "midright")

    # ── Touch ─────────────────────────────────────────────────────────────────

    def tap(self, hit, now):
        if hit.startswith("view:"):
            self.view, self.picking = hit[5:], False
        elif hit == "start" or hit == "again":
            self.store.start()
        elif hit.startswith("answer:"):
            self.store.answer(int(hit[7:]))
        elif hit == "next":
            self.store.next()
        elif hit in ("eras", "change"):
            self.picking = True
        elif hit.startswith("era:"):
            self.store.mem["round"] = None          # back to the start, with the new era
            self.store.set_era(hit[4:] or None)
            self.picking = False

    def key(self, ch, now):
        pass

    def state(self, now):
        r = self.store.round
        return (self.view, self.picking, self.store.era, r and (len(r["questions"]), r["pos"], len(r["answers"])),
                self.store.mem["asked"])
