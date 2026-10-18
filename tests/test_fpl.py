"""Off-Pi tests for the FPL Live app's data side (and its screens, when pygame is installed).

Run on a Mac/PC:  python3 -m unittest discover tests
No network: fetches are faked with a small made-up FPL world (four teams, one squad).
"""
import copy
import os
import sys
import tempfile
import unittest
import urllib.error
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "apps", "fpl"))
import fpl_data as fd  # noqa: E402

TEAM_ID = 4192362
NOW = "2026-10-18T15:00:00Z"            # Sunday of gameweek 2: one game over, one on
TEAMS = [(1, "Arsenal", "ARS"), (2, "Chelsea", "CHE"), (3, "Liverpool", "LIV"), (4, "Man City", "MCI")]
# The squad: (id = squad slot, name, team, position). 4-4-2, then the bench GK, DEF, MID, FWD.
SQUAD = [(1, "Raya", 1, 1), (2, "Saliba", 1, 2), (3, "James", 2, 2), (4, "Van Dijk", 3, 2), (5, "Gvardiol", 4, 2),
         (6, "Saka", 1, 3), (7, "Palmer", 2, 3), (8, "Salah", 3, 3), (9, "Foden", 4, 3),
         (10, "Haaland", 4, 4), (11, "Jackson", 2, 4),
         (12, "Ortega", 4, 1), (13, "Gabriel", 1, 2), (14, "Szoboszlai", 3, 3), (15, "Havertz", 1, 4)]


def utc(s):
    return fd.api_time(s)


def fixture(fid, event, h, a, kickoff, started=False, ended=False, minutes=0, bonus=(), bps=(), hs=None, as_=None,
            hd=3, ad=3):
    return {"id": fid, "event": event, "team_h": h, "team_a": a, "kickoff_time": kickoff, "started": started,
            "finished_provisional": ended, "finished": False, "minutes": minutes, "team_h_score": hs,
            "team_a_score": as_, "team_h_difficulty": hd, "team_a_difficulty": ad,
            "stats": [{"identifier": "bonus", "h": [{"element": e, "value": v} for e, v in bonus], "a": []},
                      {"identifier": "bps", "h": [{"element": e, "value": v} for e, v in bps], "a": []}]}


class World:
    """The made-up game: replies shaped like FPL's, editable by the tests."""

    def __init__(self):
        self.static = {
            "events": [{"id": i, "name": f"Gameweek {i}", "deadline_time": d, "finished": i == 1,
                        "data_checked": i == 1, "average_entry_score": 50 + i, "highest_score": 100 + i}
                       for i, d in ((1, "2026-10-10T10:00:00Z"), (2, "2026-10-17T10:00:00Z"),
                                    (3, "2026-10-24T17:30:00Z"), (4, "2026-10-31T13:30:00Z"))],
            "teams": [{"id": i, "name": n, "short_name": s} for i, n, s in TEAMS],
            "elements": [{"id": i, "web_name": n, "team": t, "element_type": pos, "now_cost": 60 + i,
                          "cost_change_event": 1 if i == 10 else 0, "chance_of_playing_next_round": None,
                          "status": "a", "news": ""} for i, n, t, pos in SQUAD],
            "total_players": 11000000}
        self.static["elements"][2].update(chance_of_playing_next_round=25, status="d", news="Knee injury - 25% chance")
        self.picks = {"active_chip": None,
                      "picks": [{"element": i, "position": i, "multiplier": (2 if i == 10 else 1) if i <= 11 else 0,
                                 "is_captain": i == 10, "is_vice_captain": i == 8} for i in range(1, 16)],
                      "entry_history": {"event_transfers_cost": 4, "bank": 5, "value": 1003, "event_transfers": 2}}
        # Gameweek 2: ARS v CHE is over (bonus in); LIV v MCI is 60 minutes in
        self.fixtures = [fixture(1, 2, 1, 2, "2026-10-18T13:00:00Z", True, True, 90, bonus=[(6, 3)], hs=1, as_=0),
                         fixture(2, 2, 3, 4, "2026-10-18T14:30:00Z", True, False, 60,
                                 bps=[(10, 40), (8, 30), (4, 30), (9, 12)], hs=0, as_=1)]
        self.points = {1: (6, 90), 2: (6, 90), 3: (0, 0), 4: (2, 60), 5: (1, 60), 6: (10, 90), 7: (2, 90),
                       8: (3, 60), 9: (1, 60), 10: (9, 60), 11: (0, 0), 12: (0, 0), 13: (2, 90), 14: (0, 0),
                       15: (5, 80)}
        self.future = [fixture(10 + gw * 2 + k, gw, h, a, f"2026-10-{20 + gw}T15:00:00Z", hd=hd, ad=ad)
                       for gw in (3, 4) for k, (h, a, hd, ad) in enumerate(((2, 1, 4, 2), (4, 3, 5, 3)))]
        self.league_rows = [{"entry": TEAM_ID if i == 5 else 100 + i, "entry_name": f"Team {i}",
                             "player_name": f"Manager {i}", "rank": i, "last_rank": i + (1 if i % 2 else -1),
                             "rank_sort": i, "event_total": 40 + i, "total": 500 - i} for i in range(1, 9)]
        self.calls = []
        self.fail = None

    def __call__(self, url, timeout=20):
        self.calls.append(url[len(fd.API):])
        if self.fail:
            raise self.fail
        path = url[len(fd.API):]
        if path == "/bootstrap-static/":
            return copy.deepcopy(self.static)
        if path == f"/entry/{TEAM_ID}/":
            return {"name": "Basil's Kite", "player_first_name": "Tim", "player_last_name": "N", "summary_overall_points": 400,
                    "summary_overall_rank": 4000000, "summary_event_points": 50, "summary_event_rank": 3000000,
                    "leagues": {"classic": [
                        {"id": 314, "name": "Overall", "league_type": "s", "entry_rank": 4000000, "rank_count": 11000000},
                        {"id": 1743621, "name": "Norri", "league_type": "x", "entry_rank": 5, "entry_last_rank": 6,
                         "rank_count": 8}]}}
        if path == f"/entry/{TEAM_ID}/history/":
            return {"current": [{"event": 1, "points": 60, "total_points": 60, "overall_rank": 5000000,
                                 "event_transfers_cost": 0}]}
        if path.startswith(f"/entry/{TEAM_ID}/event/"):
            return copy.deepcopy(self.picks)
        if path.startswith("/fixtures/?event="):
            return copy.deepcopy(self.fixtures)
        if path == "/fixtures/?future=1":
            return copy.deepcopy(self.future)
        if path.startswith("/event/") and path.endswith("/live/"):
            fx = {1: 1, 2: 1, 3: 2, 4: 2}               # team -> fixture
            return {"elements": [{"id": i, "stats": {"total_points": p, "minutes": m},
                                  "explain": [{"fixture": fx[SQUAD[i - 1][2]], "stats": []}]}
                                 for i, (p, m) in self.points.items()]}
        if path.startswith("/leagues-classic/1743621/standings/"):
            return {"league": {"id": 1743621, "name": "Norri"}, "last_updated_data": "2026-10-18T14:55:00Z",
                    "standings": {"has_next": False, "results": copy.deepcopy(self.league_rows)}}
        raise AssertionError("unexpected url " + url)


def store_for(world, cache_dir, t, config=None):
    clock = [utc(t).timestamp()]
    s = fd.Store(config or {"team_id": TEAM_ID}, cache_dir=cache_dir, fetch=world, clock=lambda: clock[0])
    return s, clock


def team_now(world, now=NOW, chip=None):
    w = world
    static = fd.slim_static(w.static)
    picks = fd.slim_picks(dict(w.picks, active_chip=chip), 2)
    live = fd.slim_live(w("".join([fd.API, "/event/2/live/"])), w.fixtures, 2)
    return fd.squad(static, picks, live, utc(now)), picks


class TestSums(unittest.TestCase):
    def setUp(self):
        self.world = World()

    def test_gameweeks_follow_the_deadlines(self):
        static = fd.slim_static(self.world.static)
        cur, nxt = fd.gameweeks(static, utc(NOW))
        self.assertEqual((cur, nxt["id"]), (2, 3))
        self.assertEqual(fd.gameweeks(static, utc("2026-10-01T00:00:00Z"))[0], None, "pre-season")

    def test_phase(self):
        fx = [fd.slim_fixture(f) for f in self.world.fixtures]
        self.assertEqual(fd.phase(fx, utc(NOW)), "live")
        fx[1].update(ended=True)
        self.assertEqual(fd.phase(fx, utc(NOW)), "bonus")
        fx[1].update(bonus_in=True)
        self.assertEqual(fd.phase(fx, utc(NOW)), "done")
        fx.append(fd.slim_fixture(fixture(3, 2, 1, 3, "2026-10-19T19:00:00Z")))
        self.assertEqual(fd.phase(fx, utc(NOW)), "waiting")
        self.assertEqual(fd.next_kickoff(fx, utc(NOW)), utc("2026-10-19T19:00:00Z"))
        self.assertEqual(fd.phase(fx, utc("2026-10-19T19:01:00Z")), "live", "kicked off, API not caught up")
        self.assertIsNone(fd.phase([], utc(NOW)))

    def test_provisional_bonus_ties_share_the_rank(self):
        f = fd.slim_fixture(self.world.fixtures[1])
        self.assertEqual(fd.provisional_bonus(f), {10: 3, 8: 2, 4: 2})
        f["bps"] = [[1, 30], [2, 30], [3, 20], [4, 10]]
        self.assertEqual(fd.provisional_bonus(f), {1: 3, 2: 3, 3: 1})
        f["bps"] = [[1, 30], [2, 20], [3, 10], [4, 10]]
        self.assertEqual(fd.provisional_bonus(f), {1: 3, 2: 2, 3: 1, 4: 1})
        f["bonus_in"] = True
        self.assertEqual(fd.provisional_bonus(f), {}, "the real bonus is in the points already")

    def test_squad_points_bonus_and_states(self):
        sq, picks = team_now(self.world)
        by = {p["name"]: p for p in sq}
        self.assertEqual((by["Haaland"]["shown"], by["Haaland"]["bonus"]), ((9 + 3) * 2, 3), "captain x2 + bonus")
        self.assertEqual(by["Saka"]["shown"], 10, "confirmed bonus is already in the points")
        self.assertEqual((by["Salah"]["state"], by["Raya"]["state"]), ("playing", "done"))
        self.assertEqual(by["Salah"]["label"], "MCI 60'")
        self.assertEqual(by["Raya"]["label"], "CHE FT")
        self.assertEqual(by["James"]["label"], "ars FT", "away games in lower case")

    def test_auto_subs(self):
        sq, picks = team_now(self.world)
        by = {p["name"]: p for p in sq}
        # James (DEF, CHE) didn't play and his game is over: first bench outfielder who played
        # and keeps the formation legal is Gabriel (DEF). Jackson (FWD, CHE) too: Szoboszlai
        # hasn't played (still on, 0 mins), so Havertz.
        self.assertEqual((by["James"]["sub"], by["Gabriel"]["sub"]), ("out", "in"))
        self.assertEqual((by["Jackson"]["sub"], by["Havertz"]["sub"]), ("out", "in"))
        self.assertIsNone(by["Szoboszlai"]["sub"])
        self.assertEqual((by["Gabriel"]["mult"], by["James"]["mult"]), (1, 0))
        t = fd.totals(sq, picks)
        counted = 6 + 6 + (2 + 2) + 1 + 10 + 2 + (3 + 2) + 1 + (9 + 3) * 2 + 2 + 5
        self.assertEqual((t["points"], t["hit"], t["net"]), (counted, 4, counted - 4))
        self.assertEqual(t["to_play"], 5, "Van Dijk, Gvardiol, Salah, Foden, Haaland still on")

    def test_auto_sub_keeps_the_formation(self):
        team = [{"id": i, "slot": i, "pos": pos, "minutes": 90, "state": "done"} for i, _, _, pos in SQUAD]
        for i in (2, 3):                                   # two defenders missing in a 4-4-2
            team[i - 1]["minutes"] = 0
        team[12]["minutes"] = 0                            # bench defender didn't play either
        # 4-4-2 -> 3-5-2 is legal; the second missing defender would leave two, so no sub
        self.assertEqual(fd.auto_subs(team), [(2, 14)])
        team[12]["minutes"] = 90                           # the bench defender did play
        self.assertEqual(fd.auto_subs(team), [(2, 13), (3, 14)])
        team[0]["minutes"] = 0                             # keeper: only the bench keeper
        self.assertEqual(fd.auto_subs(team), [(1, 12), (2, 13), (3, 14)])
        self.assertEqual(fd.auto_subs(team, "bboost"), [])

    def test_captaincy_passes_to_the_vice(self):
        w = self.world
        w.points[10] = (0, 0)
        w.fixtures[1].update(finished_provisional=True)    # Haaland's game over, he didn't play
        sq, _ = team_now(w)
        by = {p["name"]: p for p in sq}
        self.assertEqual((by["Haaland"]["mult"], by["Salah"]["mult"]), (0, 2))
        w.picks["picks"][9]["multiplier"] = 3              # Triple Captain passes on as x3
        sq, _ = team_now(w, chip="3xc")
        self.assertEqual({p["name"]: p for p in sq}["Salah"]["mult"], 3)

    def test_blank_and_double_gameweeks(self):
        w = self.world
        w.fixtures = [w.fixtures[0]]                       # LIV and MCI blank
        sq, _ = team_now(w)
        by = {p["name"]: p for p in sq}
        self.assertEqual((by["Salah"]["state"], by["Salah"]["label"]), ("blank", "no game"))
        future = fd.slim_future(w.future + [fixture(30, 3, 2, 4, "2026-10-25T15:00:00Z", hd=2, ad=4)])
        rows = fd.difficulty(future, 2, 3, weeks=3)
        self.assertEqual(rows[0], (3, [(1, True, 4), (4, True, 2)]), "a double")
        self.assertEqual(rows[2], (5, []), "beyond the data: a blank")

    def test_flags_worst_first(self):
        static = fd.slim_static(self.world.static)
        picks = fd.slim_picks(self.world.picks, 2)
        flags = fd.flagged(static, picks)
        self.assertEqual([(p["name"], c) for p, c in flags], [("James", 25)])

    def test_leagues_to_show(self):
        entry = fd.slim_entry(self.world(fd.API + f"/entry/{TEAM_ID}/"))
        self.assertEqual([lg["name"] for lg in fd.leagues_to_show(entry, {})], ["Norri"], "private ones only")
        self.assertEqual([lg["id"] for lg in fd.leagues_to_show(entry, {"leagues": [314]})], [314])
        self.assertEqual(fd.leagues_to_show(None, {}), [])


class TestStore(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.world = World()

    def test_fetches_everything_then_waits(self):
        s, clock = store_for(self.world, self.dir, NOW)
        s.refresh()
        self.assertEqual(sorted(c.split("?")[0].split("/")[1] for c in self.world.calls),
                         ["bootstrap-static", "entry", "entry", "entry", "event", "fixtures", "fixtures",
                          "leagues-classic"])
        self.assertEqual(s.get("picks")["event"], 2)
        self.assertEqual(s.get("league:1743621")["rows"][4]["me"], True)
        self.assertEqual(s.refresh(), 0, "fresh - nothing to do")
        clock[0] += 61
        self.world.calls = []
        self.assertEqual(s.refresh(), 1, "a game is on: live points every minute")
        self.assertEqual(sorted(self.world.calls), ["/event/2/live/", "/fixtures/?event=2"])
        restarted, _ = store_for(World(), self.dir, NOW)
        self.assertEqual(restarted.get("picks")["event"], 2, "a restart starts from the cache")
        other, _ = store_for(World(), self.dir, NOW, {"team_id": 1})
        self.assertIsNone(other.get("picks"), "another team's cache is ignored")

    def test_pace_follows_the_matches(self):
        s, clock = store_for(self.world, self.dir, NOW)
        s.refresh()
        self.assertEqual(s.due(clock[0])["live"], fd.MINUTE)
        self.assertEqual(s.due(clock[0])["league:1743621"], 5 * fd.MINUTE)
        self.world.fixtures[1].update(finished_provisional=True)
        clock[0] += 61
        s.refresh()
        self.assertEqual(s.due(clock[0])["live"], 5 * fd.MINUTE, "waiting for bonus points")
        self.world.fixtures[1]["stats"][0]["h"] = [{"element": 10, "value": 3}]
        self.world.fixtures.append(fixture(3, 2, 1, 3, "2026-10-19T19:00:00Z"))
        clock[0] += 5 * 60
        s.refresh()
        self.assertEqual(s.due(clock[0])["live"], fd.HOUR, "next game tomorrow: hourly")
        clock[0] = utc("2026-10-19T18:50:00Z").timestamp()
        self.assertEqual(s.due(clock[0])["live"], 10 * 60, "...until its kick-off")

    def test_new_gameweek_fetches_picks_and_tables(self):
        s, clock = store_for(self.world, self.dir, NOW)
        s.refresh()
        clock[0] = utc("2026-10-24T17:31:00Z").timestamp()   # past gameweek 3's deadline
        self.world.calls = []
        s.refresh()
        self.assertIn(f"/entry/{TEAM_ID}/event/3/picks/", self.world.calls)
        self.assertIn("/event/3/live/", self.world.calls)
        self.assertIn("/bootstrap-static/", self.world.calls)
        self.assertIn("/leagues-classic/1743621/standings/", self.world.calls)

    def test_league_page_with_the_team_further_down(self):
        rows = [{"entry": 100 + i, "entry_name": f"T{i}", "player_name": f"M{i}", "rank": i, "last_rank": i,
                 "rank_sort": i, "event_total": 1, "total": 1} for i in range(1, 51)]
        mine = [dict(rows[0], entry=TEAM_ID, rank=72, rank_sort=72)]
        pages = {"": rows, "?page_standings=2": mine}

        def fetch(url, timeout=20):
            if "leagues-classic" in url:
                q = url.split("standings/")[1]
                return {"league": {"id": 9, "name": "Big"}, "standings": {"has_next": True, "results": pages[q]}}
            return self.world(url)
        s, _ = store_for(self.world, self.dir, NOW, {"team_id": TEAM_ID, "leagues": [9]})
        s.fetch = fetch
        s.entries["entry"] = {"team": TEAM_ID, "fetched": 0,
                              "data": {"leagues": [{"id": 9, "name": "Big", "type": "x", "rank": 72, "size": 85}]}}
        table = s._league(9)
        self.assertEqual(len(table["rows"]), 51)
        self.assertTrue(table["rows"][-1]["me"])

    def test_failure_keeps_old_data_and_backs_off(self):
        s, clock = store_for(self.world, self.dir, NOW)
        s.refresh()
        clock[0] += 61
        self.world.fail = urllib.error.URLError("down")
        self.assertEqual(s.refresh(), 1)
        self.assertEqual(s.error(), "offline")
        self.assertIsNotNone(s.get("live"), "old data kept")
        clock[0] += 61
        self.assertEqual(s.refresh(), 0, "waits before retrying")
        clock[0] += fd.RETRY_AFTER
        self.world.fail = None
        s.refresh()
        self.assertIsNone(s.error())

    def test_errors_explained(self):
        self.assertEqual(fd.explain(urllib.error.HTTPError("u", 503, "x", {}, None)), "FPL is updating")
        self.assertEqual(fd.explain(urllib.error.URLError("x")), "offline")


class TestConfig(unittest.TestCase):
    def test_fpl_json(self):
        cfg = fd.load_config()
        self.assertIsInstance(cfg["team_id"], int)
        self.assertTrue(all(isinstance(i, int) for i in cfg.get("leagues", [])))


try:
    os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
    import pygame  # noqa: F401
    import fpl_display
except ImportError:
    fpl_display = None


@unittest.skipUnless(fpl_display, "pygame not installed")
class TestScreens(unittest.TestCase):
    """Draw every page (with and without data) and poke the controls."""

    def setUp(self):
        import pygame
        self.world = World()
        self.store, self.clock = store_for(self.world, tempfile.mkdtemp(), NOW)
        self.app = fpl_display.FplDisplay({"team_id": TEAM_ID}, self.store, screen=pygame.Surface((480, 800)))
        self.now = utc(NOW).astimezone(fd.LOCAL)

    def test_every_page_draws_with_and_without_data(self):
        for filled in (False, True):
            if filled:
                self.store.refresh()
            for page in fpl_display.PAGES:
                self.app.page = page
                for now in (self.now, self.now + timedelta(days=3), self.now + timedelta(days=60)):
                    self.app.draw(now)

    def test_chips_and_long_tables_draw(self):
        self.world.picks["active_chip"] = "bboost"
        self.world.league_rows = [dict(self.world.league_rows[0], entry=TEAM_ID if i == 30 else i, rank=i,
                                       rank_sort=i) for i in range(1, 41)]
        self.store.refresh()
        for page in fpl_display.PAGES:
            self.app.page = page
            self.app.draw(self.now)

    def test_table_rows_keep_the_top_and_the_team(self):
        rows = [{"rank": i, "me": i == 30} for i in range(1, 41)]
        shown = fpl_display.FplDisplay.table_rows(rows, 12)
        self.assertEqual(len(shown), 12)
        self.assertEqual([r["rank"] for r in shown[:3]], [1, 2, 3])
        self.assertIsNone(shown[3])
        self.assertIn(30, [r["rank"] for r in shown[4:]])
        self.assertEqual(fpl_display.FplDisplay.table_rows(rows[:8], 12), rows[:8])

    def tap_button(self, name):
        self.app.draw(self.now)
        r = self.app.buttons[name]
        self.app.touch_down(r.center)
        self.app.touch_up(r.center)

    def test_taps_and_swipes(self):
        self.store.refresh()
        self.tap_button("page:Leagues")
        self.assertEqual(self.app.page, "Leagues")
        self.tap_button("league0")
        self.assertEqual(self.app.league_i, 0)
        self.app.touch_down((400, 400))
        self.app.touch_up((100, 410))
        self.assertEqual(self.app.page, "Next")
        self.app.touch_down((100, 400))
        self.app.touch_up((400, 390))
        self.assertEqual(self.app.page, "Leagues")

    def test_redraws_only_when_something_changes(self):
        now = self.now
        self.assertTrue(self.app.frame(now), "first frame")
        self.assertFalse(self.app.frame(now + timedelta(seconds=20)), "same minute, nothing new")
        self.store.refresh()                         # data arrives
        self.assertTrue(self.app.frame(now))
        self.assertFalse(self.app.frame(now))
        now += timedelta(minutes=1)
        self.assertTrue(self.app.frame(now), "the clock moves on")
        self.app.last_touch -= fpl_display.DIM_AFTER + 1
        self.assertTrue(self.app.frame(now), "dimmed")
        self.assertFalse(self.app.frame(now))

    def test_new_points_flash(self):
        self.store.refresh()
        self.app.frame(self.now)
        self.assertEqual(self.app.flashing(), {})
        self.world.points[8] = (9, 65)                 # Salah scores
        self.clock[0] += 61
        self.store.refresh()
        self.assertTrue(self.app.frame(self.now + timedelta(minutes=1)))
        self.assertEqual(self.app.flashing(), {8: 6})
        self.app.flash[8] = (6, self.app.flash[8][1] - fpl_display.FLASH - 1)
        self.assertEqual(self.app.flashing(), {})


if __name__ == "__main__":
    unittest.main()
