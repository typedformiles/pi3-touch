"""Off-Pi tests for the Swifty app: the song list, Swiftle's rules, streaks and memory,
Song Showdown's ratings and pairs (and the screens, when pygame is installed).

Run on a Mac/PC:  python3 -m unittest discover tests
"""
import os
import random
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "apps", "swifty"))
import swifty_data as sd  # noqa: E402

ALBUMS, SONGS = sd.load_catalogue()
TODAY = date(2026, 10, 19)


def song(title):
    return next(s for s in SONGS if s["title"] == title)


def play(store, game, letters):
    for c in letters:
        store.guess(game, c)


def win(store, game, misses=0):
    wrong = [c for c in "ZQXJKVWYBPFGMUCLDHRSNOITAE" if c not in game.hidden][:misses]
    play(store, game, wrong + sorted(game.hidden))
    return game


def lose(store, game):
    play(store, game, [c for c in "ZQXJKVWYBPFGMUCLDHRSNOITAE" if c not in game.hidden][:sd.LIVES])
    return game


class TestCatalogue(unittest.TestCase):
    def test_albums_in_order_with_colours(self):
        self.assertEqual(len(ALBUMS), 12)
        self.assertEqual([a["year"] for a in ALBUMS], sorted(a["year"] for a in ALBUMS))
        for a in ALBUMS:
            self.assertEqual(len(a["colour"]), 3)
            self.assertTrue(a["colour_name"], a["title"])

    def test_every_title_is_guessable_and_unique(self):
        titles = [s["title"] for s in SONGS]
        self.assertEqual(len(titles), len(set(titles)))
        self.assertNotIn("22", titles)
        self.assertNotIn("Vigilante Shit", titles)
        for s in SONGS:
            self.assertGreaterEqual(len(sd.letters(s["title"])), 2, s["title"])

    def test_track_numbers_skip_nothing(self):
        """Left-out songs keep their place: Red's track 7 is still I Almost Do."""
        self.assertEqual(song("I Almost Do")["track"], 7)
        self.assertEqual(song("Mastermind")["track"], 13)
        self.assertEqual(song("cardigan")["album"], "folklore")


class TestSwiftle(unittest.TestCase):
    def test_letters_misses_and_winning(self):
        g = sd.Swiftle(song("Style"))
        self.assertEqual(g.hidden, set("STYLE"))
        self.assertTrue(g.guess("s"))
        self.assertFalse(g.guess("S"), "a letter counts once")
        self.assertFalse(g.guess("?"))
        g.guess("A")
        self.assertEqual((g.misses, g.lives), (["A"], sd.LIVES - 1))
        for c in "TYLE":
            g.guess(c)
        self.assertTrue(g.solved and g.over and not g.lost)
        self.assertFalse(g.guess("B"), "no guesses after the end")

    def test_punctuation_and_digits_are_on_show(self):
        g = sd.Swiftle(song("Wi$h Li$t"))
        self.assertTrue(g.shows("$") and g.shows(" "))
        self.assertFalse(g.shows("W"))
        g.guess("w")
        self.assertTrue(g.shows("w") and g.shows("W"))

    def test_six_misses_lose_and_clues_open_on_the_way(self):
        g = sd.Swiftle(song("Karma"))
        opened = []
        for c in "BCDEFG":
            g.guess(c)
            opened.append([name for name, _ in sd.CLUES if g.clue(name)])
        self.assertEqual(opened[0], [])
        self.assertEqual(opened[1], ["colour"])
        self.assertEqual(opened[4], ["colour", "year", "album", "track"])
        self.assertTrue(g.lost and g.over)

    def test_the_day_decides_the_song_and_none_repeats_for_a_cycle(self):
        self.assertEqual(sd.daily_song(SONGS, TODAY), sd.daily_song(list(reversed(SONGS)), TODAY))
        cycle = [sd.daily_song(SONGS, TODAY + timedelta(days=k))["title"] for k in range(len(SONGS))]
        self.assertEqual(len(set(cycle)), len(SONGS))
        self.assertEqual(sd.daily_song(SONGS, TODAY + timedelta(days=len(SONGS))), sd.daily_song(SONGS, TODAY))
        self.assertEqual(sd.puzzle_number(TODAY), 1)


class TestStore(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.store = self.make()

    def make(self):
        return sd.SwiftleStore(ALBUMS, SONGS, sd.Saves(self.dir), rng=random.Random(1))

    def test_progress_survives_a_restart(self):
        g = self.store.today(TODAY)
        self.store.guess(g, "E")
        self.store.guess(g, "Z")
        again = self.make().today(TODAY)
        self.assertEqual(again.guesses, "EZ")
        self.assertEqual(again.song, g.song)

    def test_a_new_day_brings_a_new_puzzle(self):
        g = self.store.today(TODAY)
        self.store.guess(g, "E")
        tomorrow = self.store.today(TODAY + timedelta(days=1))
        self.assertEqual(tomorrow.guesses, "")
        self.assertNotEqual(tomorrow.song, g.song)

    def test_streaks(self):
        st = self.store
        for k in (6, 5, 4):                     # three wins, a loss, a gap, then wins
            win(st, st.today(TODAY - timedelta(days=k)), misses=k % 3)
        lose(st, st.today(TODAY - timedelta(days=3)))
        win(st, st.today(TODAY - timedelta(days=1)))
        s = st.stats(TODAY)
        self.assertEqual((s["played"], s["won"], s["streak"], s["best"]), (5, 4, 1, 3),
                         "today not played yet: yesterday's streak still stands")
        self.assertEqual(s["dist"], [2, 1, 1, 0, 0, 0])
        win(st, st.today(TODAY), misses=2)
        self.assertEqual(st.stats(TODAY)["streak"], 2)
        self.assertEqual(st.stats(TODAY)["recent"][0][0], TODAY.isoformat())
        self.assertEqual(st.stats(TODAY + timedelta(days=2))["streak"], 0, "a missed day ends it")

    def test_unfinished_days_dont_count(self):
        self.store.guess(self.store.today(TODAY), "E")
        self.assertEqual(self.store.stats(TODAY)["played"], 0)

    def test_practice_from_an_era_not_today_and_not_lately(self):
        today = sd.daily_song(SONGS, TODAY)["title"]
        eligible = {s["title"] for s in SONGS if s["album"] == "Taylor Swift"} - {today}
        seen = set()
        for _ in eligible:
            p = self.store.new_practice("Taylor Swift", TODAY)
            seen.add(p.song["title"])
        self.assertEqual(seen, eligible, "every song in the era once, before any repeats")
        p = self.store.new_practice(None, TODAY)
        self.store.guess(p, "E")
        again = self.make()
        self.assertEqual((again.practice().song, again.practice().guesses, again.era), (p.song, "E", None))
        self.assertEqual(again.stats(TODAY)["played"], 0, "practice doesn't count")


class TestShowdown(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.store = self.make()

    def make(self):
        return sd.ShowdownStore(ALBUMS, SONGS, sd.Saves(self.dir), rng=random.Random(2))

    def test_a_pick_moves_points_from_loser_to_winner(self):
        a, b = self.store.pair()
        self.store.pick(b["title"])
        rb, ra = self.store.rec(b["title"]), self.store.rec(a["title"])
        self.assertEqual((rb, ra), ([sd.START + sd.K / 2, 1, 1], [sd.START - sd.K / 2, 1, 0]))
        self.assertEqual(self.store.battles, 1)
        self.assertNotEqual({s["title"] for s in self.store.pair()}, {a["title"], b["title"]}, "a new pair")

    def test_beating_a_stronger_song_is_worth_more(self):
        self.store.mem["songs"] = {"Style": [1200, 5, 5], "Karma": [1000, 5, 1]}
        self.store.mem["pair"] = ["Style", "Karma"]
        self.store.pick("Karma")
        self.assertGreater(self.store.rec("Karma")[0] - 1000, sd.K * 0.7)

    def test_the_pair_waits_on_disk_until_picked(self):
        pair = self.store.pair()
        self.assertEqual(self.make().pair(), pair)
        self.assertIsNone(self.store.pick("Not in the pair"))

    def test_every_song_battles_before_any_battles_twice(self):
        for _ in range(len(SONGS) // 2):
            a, b = self.store.pair()
            self.store.pick(a["title"])
        self.assertEqual({self.store.rec(s["title"])[1] for s in SONGS}, {1})

    def test_undo_puts_everything_back(self):
        a, b = self.store.pair()
        self.store.pick(a["title"])
        before = (self.store.rec(a["title"]), self.store.battles)
        c, d = self.store.pair()
        self.store.pick(d["title"])
        self.assertTrue(self.store.undo())
        self.assertEqual([s["title"] for s in self.store.pair()], [c["title"], d["title"]])
        self.assertEqual((self.store.rec(a["title"]), self.store.battles), before)
        self.assertEqual(self.store.rec(d["title"])[1], 1 if d["title"] in (a["title"], b["title"]) else 0)
        self.assertFalse(self.store.undo(), "one step only")

    def test_skip_scores_nothing_and_cant_be_undone(self):
        a, b = self.store.pair()
        self.store.skip()
        self.assertEqual((self.store.battles, self.store.rec(a["title"])[1]), (0, 0))
        self.assertFalse(self.store.can_undo())

    def test_an_era_only(self):
        self.store.set_era("evermore")
        for _ in range(10):
            pair = self.store.pair()
            self.assertEqual({s["album"] for s in pair}, {"evermore"})
            self.store.pick(pair[0]["title"])

    def test_rankings_of_songs_and_eras(self):
        self.store.mem["songs"] = {"cardigan": [1100, 4, 4], "willow": [1050, 3, 2], "Karma": [900, 3, 0],
                                   "betty": [1000, 2, 1]}
        self.assertEqual([r[0]["title"] for r in self.store.ranking()], ["cardigan", "willow", "betty", "Karma"])
        eras = self.store.eras()
        self.assertEqual([(e[0]["title"], e[2], e[3]["title"]) for e in eras[:3]],
                         [("folklore", 2, "cardigan"), ("evermore", 1, "willow"), ("Midnights", 1, "Karma")])
        self.assertTrue(all(e[1] is None for e in eras[3:]))
        self.assertEqual(len(eras), len(ALBUMS))


try:
    os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
    import pygame  # noqa: F401
    import swifty_display
    import swiftle_screen
    import showdown_screen
except ImportError:
    swifty_display = None


@unittest.skipUnless(swifty_display, "pygame not installed")
class TestScreens(unittest.TestCase):
    def setUp(self):
        import pygame
        self.saves = sd.Saves(tempfile.mkdtemp())
        self.app = swifty_display.SwiftyDisplay(ALBUMS, SONGS, self.saves, screen=pygame.Surface((480, 800)))
        self.now = datetime(2026, 10, 19, 16, 47)
        self.sw = self.app.games["Swiftle"]

    def tap(self, name):
        self.app.draw(self.now)
        self.app.tap(self.app.ui.buttons[name].center, self.now)

    def test_every_title_fits_its_tiles(self):
        for s in SONGS:
            size, gap, space, row_h, lines = swiftle_screen.tile_layout(s["title"], 448, swiftle_screen.TILES_H)
            widest = max(sum(swiftle_screen.word_width(w, size, gap) for w in ln) + space * (len(ln) - 1)
                         for ln in lines)
            self.assertLessEqual(widest, 448, s["title"])
            self.assertLessEqual(len(lines) * row_h - size // 4, swiftle_screen.TILES_H, s["title"])
            self.assertGreaterEqual(size, 30, s["title"])

    def test_text_reads_on_every_era_colour(self):
        """Pills, keys and buttons in the era's colour, the found-letter tiles, and the colour
        clue's swatch."""
        import swifty_ui as ui
        for a in ALBUMS:
            c = a["colour"]
            tile = ui.mix(c, ui.mix(ui.BG, c, 0.16), 0.45)
            self.assertGreaterEqual(ui.contrast(c, ui.ink_on(c)), 4, a["title"])
            self.assertGreaterEqual(ui.contrast(a["swatch"], ui.ink_on(a["swatch"])), 4, a["title"])
            self.assertGreaterEqual(ui.contrast(tile, ui.ink_on(tile)), 4, a["title"])

    def test_playing_today_by_tapping_keys(self):
        game = self.app.swiftle.today(self.now.date())
        for c in sorted(game.hidden):
            self.tap(f"key:{c}")
        self.assertTrue(game.solved)
        self.app.draw(self.now)
        self.assertNotIn("key:A", self.app.ui.buttons, "the keys go once it's over")
        self.tap("practice")
        self.assertEqual(self.sw.view, "Practice")

    def test_practice_eras_and_next_song(self):
        self.tap("view:Practice")
        self.assertTrue(self.sw.picking)
        self.tap("era:Lover")
        p = self.app.swiftle.practice()
        self.assertEqual(p.song["album"], "Lover")
        lose(self.app.swiftle, p)
        self.tap("again")
        self.assertNotEqual(self.app.swiftle.practice().song, p.song)
        self.tap("eras")
        self.tap("era:")
        self.assertIsNone(self.app.swiftle.era)

    def test_every_view_and_game_draws(self):
        st = self.app.swiftle
        for k in range(1, 8):
            game = st.today(self.now.date() - timedelta(days=k))
            win(st, game, misses=k % 4) if k % 3 else lose(st, game)
        for game in swifty_display.GAMES:
            self.tap(f"game:{game}")
            if game == "Swiftle":
                for view in swiftle_screen.VIEWS:
                    self.tap(f"view:{view}")
                    self.app.draw(self.now)
        self.assertEqual(self.saves.section("app")["game"], "Showdown")

    def test_showdown_pick_glows_then_moves_on(self):
        self.tap("game:Showdown")
        sc = self.app.games["Showdown"]
        first = self.app.showdown.pair()
        self.tap("pick:1")
        self.assertEqual(self.app.showdown.rec(first[1]["title"])[2], 1)
        self.app.draw(self.now)
        self.assertNotIn("pick:0", self.app.ui.buttons, "no picking while the winner glows")
        sc.flash = (sc.flash[0], sc.flash[1], 0)
        self.assertTrue(self.app.frame(self.now))
        self.assertIsNone(sc.flash)
        self.assertIn("pick:0", self.app.ui.buttons)
        self.tap("undo")
        self.assertEqual(self.app.showdown.battles, 0)
        self.tap("eras")
        self.tap("era:Red")
        self.assertEqual({s["album"] for s in self.app.showdown.pair()}, {"Red"})

    def test_showdown_views_draw_empty_and_full(self):
        self.tap("game:Showdown")
        for view in showdown_screen.VIEWS:
            self.tap(f"view:{view}")
        st = self.app.showdown
        for _ in range(300):
            st.pick(st.pair()[0]["title"])
        for view in showdown_screen.VIEWS:
            self.tap(f"view:{view}")
        self.tap("view:Top songs")
        for _ in range(20):
            self.app.draw(self.now)
            if "lower" not in self.app.ui.buttons:
                break
            self.tap("lower")
        self.assertIn("higher", self.app.ui.buttons, "pages end at the last song")
        self.tap("higher")

    def test_redraws_only_when_something_changes(self):
        self.assertTrue(self.app.frame(self.now))
        self.assertFalse(self.app.frame(self.now))
        self.app.tap(self.app.ui.buttons["key:E"].center, self.now)
        self.assertTrue(self.app.frame(self.now))
        self.assertFalse(self.app.frame(self.now))
        self.assertTrue(self.app.frame(self.now + timedelta(minutes=1)))


if __name__ == "__main__":
    unittest.main()
