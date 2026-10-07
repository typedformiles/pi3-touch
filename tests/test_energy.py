"""Off-Pi tests for the Energy & Grid app's data side (and its screens, when pygame is installed).

Run on a Mac/PC:  python3 -m unittest discover tests
No network: fetches are faked with canned Carbon Intensity API responses.
"""
import os
import sys
import tempfile
import unittest
import urllib.error
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "apps", "energy"))
import energy_data as ed  # noqa: E402

REGION = {"name": "Maidenhead", "postcode": "SL6"}
GB = {"name": "Great Britain"}
MIX = {"biomass": 10.4, "coal": 0, "imports": 1.8, "gas": 40.1, "nuclear": 5.2, "other": 0,
       "hydro": 0, "solar": 13, "wind": 29.5}
# A day's shape in gCO2/kWh, one value per hour from 15:00 UTC (16:00 BST) on Wed 7 Oct 2026:
# dirty evening peak, cleaner overnight, cleanest late morning, then up again
SHAPE = [170, 200, 230, 210, 180, 150, 140, 130, 120, 110, 100, 100, 110, 120, 130, 110,
         90, 70, 60, 55, 60, 80, 120, 160]


def band_of(g):
    return "very low" if g < 40 else "low" if g < 90 else "moderate" if g < 170 else "high" if g < 230 else "very high"


def half_hours(start="2026-10-07T15:00Z", n=96, regional=True, actual_first=False):
    t0 = ed.api_time(start)
    out = []
    for i in range(n):
        g = SHAPE[(i // 2) % len(SHAPE)]
        p = {"from": (t0 + i * ed.HALF_HOUR).strftime("%Y-%m-%dT%H:%MZ"),
             "to": (t0 + (i + 1) * ed.HALF_HOUR).strftime("%Y-%m-%dT%H:%MZ"),
             "intensity": {"forecast": g, "index": band_of(g)}}
        if regional:
            p["generationmix"] = [{"fuel": f, "perc": v} for f, v in MIX.items()]
        else:
            p["intensity"]["actual"] = g - 5 if actual_first and i == 0 else None
        out.append(p)
    return out


def utc(s):
    return ed.api_time(s)


class FakeApi:
    """Answers energy_data's fetches like the Carbon Intensity API would, and records them."""

    def __init__(self, fail=None):
        self.calls, self.fail = [], fail

    def __call__(self, url, timeout=20):
        self.calls.append(url)
        if self.fail:
            raise self.fail
        if "/regional/intensity/" in url:
            return {"data": [{"regionid": 12, "shortname": "South England", "postcode": "SL6",
                              "data": half_hours()}]}
        if url.startswith(ed.API + "/intensity/"):
            return {"data": half_hours(regional=False, actual_first=True)}
        if url == ed.API + "/generation":
            return {"data": {"from": "2026-10-07T15:00Z", "to": "2026-10-07T15:30Z",
                             "generationmix": [{"fuel": "wind", "perc": 60}, {"fuel": "gas", "perc": 30},
                                               {"fuel": "nuclear", "perc": 10}]}}
        raise AssertionError("unexpected url " + url)


class TestSums(unittest.TestCase):
    def setUp(self):
        self.ps = ed.parse_periods(half_hours())
        self.now = utc("2026-10-07T15:10Z")

    def test_parse_and_current(self):
        self.assertEqual(len(self.ps), 96)
        cur = ed.current(self.ps, self.now)
        self.assertEqual(cur["start"], utc("2026-10-07T15:00Z"))
        self.assertEqual((cur["forecast"], cur["index"]), (170, "high"))
        self.assertEqual(cur["mix"]["wind"], 29.5)
        self.assertIsNone(ed.current(self.ps, utc("2026-10-10T00:00Z")))

    def test_parse_skips_junk(self):
        ps = ed.parse_periods([{"from": "2026-10-07T15:00Z", "to": "2026-10-07T15:30Z"},
                               {"from": "bad", "to": "x", "intensity": {"forecast": 1}},
                               {"from": "2026-10-07T15:30Z", "to": "2026-10-07T16:00Z",
                                "intensity": {"forecast": None}}])
        self.assertEqual(ps, [])

    def test_measured_beats_forecast(self):
        ps = ed.parse_periods(half_hours(regional=False, actual_first=True))
        self.assertEqual(ed.value(ps[0]), 165)
        self.assertEqual(ed.value(ps[1]), 170)
        self.assertIsNone(ps[0]["mix"])

    def test_band_of_an_average_follows_the_api(self):
        self.assertEqual(ed.band(57, self.ps), "low")
        self.assertEqual(ed.band(215, self.ps), "high")
        self.assertIsNone(ed.band(100, []))

    def test_greenest_window_in_the_next_24_hours(self):
        best = ed.best_window(self.ps, self.now, 3)
        # cleanest 3 h of SHAPE: 60, 55, 60 -> hours 18-20 after 15:00 UTC = 09:00-12:00 UTC Thursday
        self.assertEqual(best["start"], utc("2026-10-08T09:00Z"))
        self.assertEqual(best["end"], utc("2026-10-08T12:00Z"))
        self.assertAlmostEqual(best["avg"], (60 + 55 + 60) / 3)
        self.assertEqual(best["band"], "low")
        worst = ed.best_window(self.ps, self.now, 3, worst=True)
        self.assertEqual(worst["start"], utc("2026-10-07T16:00Z"))          # 200, 230, 210
        self.assertEqual(worst["band"], "high")

    def test_windows_start_with_the_half_hour_were_in(self):
        ws = ed.windows(self.ps, utc("2026-10-07T15:29Z"), 1)
        self.assertEqual(ws[0]["start"], utc("2026-10-07T15:00Z"))
        self.assertEqual(len(ws), 95)
        self.assertEqual(ed.windows(self.ps[:3], self.now, 2), [], "not enough half hours")

    def test_windows_skip_gaps_in_the_data(self):
        ps = self.ps[:2] + self.ps[3:6]
        self.assertEqual([w["start"] for w in ed.windows(ps, self.now, 1)],
                         [utc("2026-10-07T15:00Z"), utc("2026-10-07T16:30Z"), utc("2026-10-07T17:00Z")])

    def test_best_by_day_uses_uk_dates(self):
        days = ed.best_by_day(self.ps, self.now, 3)
        self.assertEqual([d.isoformat() for d, _ in days], ["2026-10-07", "2026-10-08", "2026-10-09"])
        today = days[0][1]
        self.assertEqual(today["start"].astimezone(ed.LOCAL).date().isoformat(), "2026-10-07")
        self.assertLessEqual(days[1][1]["avg"], today["avg"])

    def test_trend(self):
        self.assertEqual(ed.trend(self.ps, self.now)[0], "rising")         # 170 -> evening peak
        self.assertEqual(ed.trend(self.ps, utc("2026-10-07T19:10Z"))[0], "falling")
        self.assertIsNone(ed.trend(self.ps, utc("2026-10-12T00:00Z")))

    def test_groups_and_order(self):
        g = ed.groups(MIX)
        self.assertAlmostEqual(g["zero carbon"], 29.5 + 13 + 5.2)
        self.assertAlmostEqual(g["fossil"], 40.1)
        self.assertAlmostEqual(sum(g.values()), sum(MIX.values()))
        order = [f for f, _ in ed.by_share(MIX)]
        self.assertEqual(order[:3], ["gas", "wind", "solar"])
        self.assertEqual(order[-3:], ["hydro", "other", "coal"], "zeros in FUELS order")
        self.assertEqual(ed.by_share({"wind": 1, "tidal": 1})[-1][0], "tidal", "unknown fuels last")

    def test_urls(self):
        now = utc("2026-10-07T15:47Z")
        self.assertEqual(ed.urls(REGION, now)["forecast"],
                         ed.API + "/regional/intensity/2026-10-07T15:30Z/fw48h/postcode/SL6")
        self.assertEqual(ed.urls(GB, now), {"forecast": ed.API + "/intensity/2026-10-07T15:30Z/fw48h",
                                            "mix": ed.API + "/generation"})


class TestStore(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.t = utc("2026-10-07T15:10Z").timestamp()

    def store(self, api, places=(REGION, GB)):
        return ed.Store(list(places), cache_dir=self.dir, fetch=api, clock=lambda: self.t)

    def test_fetches_caches_and_waits(self):
        api = FakeApi()
        s = self.store(api)
        self.assertEqual(s.refresh(), 2)
        self.assertEqual(len(api.calls), 3, "region: one call; Great Britain: forecast + mix")
        self.assertEqual(s.get(REGION)["region"], "South England")
        self.assertEqual(s.get(GB)["mix"]["generationmix"][0]["fuel"], "wind")
        self.assertEqual(s.refresh(), 0, "fresh - nothing to do")
        self.t += ed.FETCH_EVERY + 1
        self.assertEqual(s.refresh(), 2)
        self.assertIsNotNone(self.store(FakeApi()).get(GB), "a restart starts from the cache")

    def test_mix_now_regional_and_national(self):
        s = self.store(FakeApi())
        s.refresh()
        now = utc("2026-10-07T15:10Z")
        mix, when = ed.mix_now(ed.parse_entry(s.get(REGION)), now)
        self.assertEqual(mix["gas"], 40.1)
        mix, when = ed.mix_now(ed.parse_entry(s.get(GB)), now)
        self.assertEqual((mix["wind"], when), (60, utc("2026-10-07T15:00Z")))

    def test_failure_keeps_old_data_and_backs_off(self):
        s = self.store(FakeApi(), [REGION])
        s.refresh()
        self.t += ed.FETCH_EVERY + 1
        s.fetch = FakeApi(fail=urllib.error.URLError("down"))
        self.assertEqual(s.refresh(), 1)
        self.assertEqual(s.error(REGION), "offline")
        self.assertIsNotNone(s.get(REGION), "old data kept")
        self.assertEqual(s.refresh(), 0, "waits before retrying")
        self.t += ed.RETRY_AFTER + 1
        s.fetch = FakeApi()
        s.refresh()
        self.assertIsNone(s.error(REGION))

    def test_empty_reply_is_an_error(self):
        s = self.store(lambda url, timeout=20: {"data": [{"shortname": "X", "data": []}]}, [REGION])
        s.refresh()
        self.assertIsNone(s.get(REGION))
        self.assertIn("no half hours", s.error(REGION))

    def test_bad_postcode_is_explained(self):
        self.assertEqual(ed.explain(urllib.error.HTTPError("u", 400, "no", {}, None)), "bad postcode?")


class TestConfig(unittest.TestCase):
    def test_places_json(self):
        cfg = ed.load_config()
        self.assertTrue(cfg["places"])
        self.assertEqual(len({ed.slug(p["name"]) for p in cfg["places"]}), len(cfg["places"]))
        for p in cfg["places"]:
            if "postcode" in p:
                self.assertRegex(p["postcode"], r"^[A-Z]{1,2}[0-9][0-9A-Z]?$", "outward code only, e.g. SL6")


try:
    os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
    import pygame  # noqa: F401
    import energy_display
except ImportError:
    energy_display = None


@unittest.skipUnless(energy_display, "pygame not installed")
class TestScreens(unittest.TestCase):
    """Draw every page of every place (with and without data) and poke the controls."""

    def setUp(self):
        import pygame
        self.cfg = {"places": [REGION, GB], "window_hours": 3}
        self.t = utc("2026-10-07T15:10Z").timestamp()
        self.store = ed.Store(self.cfg["places"], cache_dir=tempfile.mkdtemp(), fetch=FakeApi(),
                              clock=lambda: self.t)
        self.app = energy_display.EnergyDisplay(self.cfg, self.store, screen=pygame.Surface((480, 800)))
        self.now = utc("2026-10-07T15:10Z").astimezone(ed.LOCAL)

    def test_every_page_draws_with_and_without_data(self):
        for filled in (False, True):
            if filled:
                self.store.refresh()
            for i in range(len(self.cfg["places"])):
                self.app.place_i = i
                for page in energy_display.PAGES:
                    self.app.page = page
                    for now in (self.now, self.now + timedelta(hours=8, minutes=40), self.now + timedelta(days=3)):
                        self.app.draw(now)

    def tap_button(self, name):
        self.app.draw(self.now)
        r = self.app.buttons[name]
        self.app.touch_down(r.center)
        self.app.touch_up(r.center)

    def test_taps_switch_place_and_page(self):
        self.store.refresh()
        self.tap_button("place1")
        self.assertEqual(self.app.place_i, 1)
        self.tap_button("page:Forecast")
        self.assertEqual(self.app.page, "Forecast")
        self.tap_button("place0")
        self.assertEqual((self.app.place_i, self.app.page), (0, "Forecast"))

    def test_swipe_turns_pages(self):
        self.app.draw(self.now)
        self.app.touch_down((400, 400))
        self.app.touch_up((100, 410))
        self.assertEqual(self.app.page, "Mix")
        self.app.touch_down((100, 400))
        self.app.touch_up((400, 390))
        self.assertEqual(self.app.page, "Now")

    def test_redraws_only_when_something_changes(self):
        now = self.now
        self.assertTrue(self.app.frame(now), "first frame")
        self.assertFalse(self.app.frame(now + timedelta(seconds=20)), "same minute, nothing new")
        now += timedelta(minutes=1)
        self.assertTrue(self.app.frame(now), "the clock moves on")
        self.store.refresh()                         # data arrives
        self.assertTrue(self.app.frame(now))
        self.assertFalse(self.app.frame(now))
        self.app.last_touch -= energy_display.DIM_AFTER + 1
        self.assertTrue(self.app.frame(now), "dimmed")
        self.assertFalse(self.app.frame(now))

    def test_pages_cycle_after_a_minute_untouched(self):
        self.app.cycle_seconds = 15
        self.app.last_touch -= energy_display.CYCLE_IDLE + 1
        seen = []
        for _ in range(4):
            self.app.last_cycle -= 16
            self.app.tick()
            seen.append(self.app.page)
        self.assertEqual(seen, ["Mix", "Forecast", "Now", "Mix"])


if __name__ == "__main__":
    unittest.main()
