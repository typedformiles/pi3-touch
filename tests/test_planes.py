"""Off-Pi tests for the Planes Overhead app's data side (and its screens, when pygame is installed).

Run on a Mac/PC:  python3 -m unittest discover tests
No network: the ADS-B sources and adsbdb are faked with a few made-up aircraft around Taplow.
"""
import json
import os
import sys
import tempfile
import unittest
import urllib.error

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "apps", "planes"))
import planes_data as pd  # noqa: E402

HOME = {"name": "Taplow", "lat": 51.5235, "lon": -0.6812}
CONFIG = {"home": HOME, "ranges_nm": [5, 10, 20], "range_nm": 10,
          "landmarks": [{"name": "Windsor", "lat": 51.4839, "lon": -0.6044}],
          "runways": [{"name": "Heathrow", "ends": [[51.4775, -0.4895], [51.4776, -0.4330]], "label": [51.46, -0.46]}]}
T0 = 1792356000.0


def aircraft():
    return [
        # 3 nm south-south-east, on the approach to Heathrow, descending
        {"hex": "4076e3", "flight": "BAW43HA ", "r": "G-NEOU", "t": "A21N", "desc": "AIRBUS A-321neo",
         "alt_baro": 1800, "gs": 143.0, "track": 89.6, "baro_rate": -768, "nav_qnh": 1027.2,
         "lat": 51.4768, "lon": -0.6527, "seen_pos": 0.1, "squawk": "6260", "emergency": "none"},
        # 4.6 nm north, high and climbing; no callsign
        {"hex": "aaaaaa", "r": "N123", "t": "B789", "desc": "BOEING 787-9 Dreamliner", "alt_baro": 12000,
         "gs": 300, "track": 270, "baro_rate": 1500, "nav_qnh": 1027.0, "lat": 51.60, "lon": -0.68, "seen_pos": 2},
        # On the ground at Heathrow, 13 nm away
        {"hex": "bbbbbb", "flight": "EIN183", "alt_baro": "ground", "gs": 8, "lat": 51.4700, "lon": -0.4543,
         "seen_pos": 1},
        # Squawking 7700, 15 nm out
        {"hex": "cccccc", "flight": "TEST1", "alt_baro": 9000, "gs": 250, "track": 10, "squawk": "7700",
         "lat": 51.27, "lon": -0.68, "seen_pos": 1},
        # A position too old to show
        {"hex": "dddddd", "flight": "OLD1", "alt_baro": 5000, "lat": 51.52, "lon": -0.68, "seen_pos": 120},
    ]


class FakeNet:
    """adsb.fi, adsb.lol and adsbdb, recording what's asked."""

    def __init__(self, fail_fi=False, fail_all=False):
        self.calls, self.fail_fi, self.fail_all = [], fail_fi, fail_all
        self.planes = aircraft()

    def __call__(self, url, timeout=12):
        self.calls.append(url)
        if self.fail_all or (self.fail_fi and "adsb.fi" in url):
            raise urllib.error.URLError("down")
        if "adsb.fi" in url:
            return json.dumps({"aircraft": self.planes}).encode()
        if "adsb.lol" in url:
            return json.dumps({"ac": self.planes}).encode()
        if url.endswith("/callsign/BAW43HA"):
            return json.dumps({"response": {"flightroute": {
                "airline": {"name": "British Airways"},
                "origin": {"municipality": "Amsterdam", "iata_code": "AMS"},
                "destination": {"municipality": "London", "iata_code": "LHR"}}}}).encode()
        if "/callsign/" in url:
            raise urllib.error.HTTPError(url, 404, "unknown callsign", {}, None)
        if url.endswith("/aircraft/4076e3"):
            return json.dumps({"response": {"aircraft": {"registered_owner": "British Airways",
                                                         "url_photo_thumbnail": "https://img.example/1.jpg"}}}).encode()
        if "/aircraft/" in url:
            raise urllib.error.HTTPError(url, 404, "unknown aircraft", {}, None)
        if url == "https://img.example/1.jpg":
            return b"not really a jpeg"
        raise AssertionError("unexpected url " + url)


class TestSums(unittest.TestCase):
    def test_parse(self):
        planes = pd.parse({"aircraft": aircraft()}, HOME, T0)
        self.assertEqual(sorted(planes), ["4076e3", "aaaaaa", "bbbbbb", "cccccc"], "old position dropped")
        ba = planes["4076e3"]
        self.assertEqual(ba["callsign"], "BAW43HA")
        self.assertAlmostEqual(ba["dist"], 3.0, delta=0.1)
        self.assertEqual(pd.compass(ba["bearing"]), "S", "159 degrees: nearer S than SE")
        self.assertEqual(ba["alt"], 2175, "1800 ft on 1013 hPa is ~2175 ft on today's 1027")
        self.assertEqual(planes["aaaaaa"]["alt"], 12000, "above transition: left as it is")
        self.assertTrue(planes["bbbbbb"]["ground"])
        self.assertEqual(planes["cccccc"]["emergency"], "Emergency")

    def test_height(self):
        self.assertEqual(pd.height(-150, 1027.2), 225)
        self.assertEqual(pd.height(-500, 1027.2), 0, "never below sea level")
        self.assertEqual(pd.height(1000, None), 1000)
        self.assertIsNone(pd.local_qnh([{"nav_qnh": 2000}]))

    def test_visible_nearest_first_without_ground(self):
        planes = pd.parse({"aircraft": aircraft()}, HOME, T0)
        self.assertEqual([p["hex"] for p in pd.visible(planes, 20)], ["4076e3", "aaaaaa", "cccccc"])
        self.assertIn("bbbbbb", [p["hex"] for p in pd.visible(planes, 20, show_ground=True)])
        self.assertEqual([p["hex"] for p in pd.visible(planes, 5)], ["4076e3", "aaaaaa"])

    def test_words(self):
        self.assertEqual(pd.nice_type("AIRBUS A-320neo", "A20N"), "Airbus A320neo")
        self.assertEqual(pd.nice_type("AIRBUS HELICOPTERS EC-145", ""), "Airbus Helicopters EC145")
        self.assertEqual(pd.nice_type("", "A20N"), "A20N")
        self.assertEqual((pd.callsign("BAW43HA "), pd.callsign("@@@@@@@@"), pd.callsign(None)), ("BAW43HA", "", ""))
        self.assertEqual((pd.climb(-768), pd.climb(1500), pd.climb(100), pd.climb(None)), ("down", "up", "", ""))
        self.assertEqual(pd.place({"municipality": "Amsterdam", "iata_code": "AMS"}), "Amsterdam (AMS)")
        self.assertEqual([pd.compass(b) for b in (0, 44, 91, 180, 300, 350)], ["N", "NE", "E", "S", "NW", "N"])


class TestStore(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.t = T0

    def store(self, net):
        return pd.Store(CONFIG, cache_dir=self.dir, get=net, clock=lambda: self.t)

    def test_fetches_looks_up_and_caches(self):
        net = FakeNet()
        s = self.store(net)
        self.assertTrue(s.refresh())
        self.assertEqual(s.source, "adsb.fi")
        self.assertEqual(s.route("BAW43HA")["airline"]["name"], "British Airways", "nearest first")
        self.assertIsNone(s.aircraft("aaaaaa"), "only two lookups a pass")
        n = len(net.calls)
        self.assertFalse(s.refresh(), "positions not due yet...")
        self.assertEqual(len(net.calls), n, "...and no lookups in between")
        self.t += pd.EVERY_ACTIVE
        self.assertTrue(s.refresh())
        self.assertEqual(s.aircraft("aaaaaa"), {}, "adsbdb doesn't know it: remembered as {}")
        self.assertEqual(self.store(FakeNet()).route("BAW43HA")["origin"]["iata_code"], "AMS", "cached on disk")

    def test_pace(self):
        s = self.store(FakeNet())
        s.refresh()
        self.t += pd.EVERY_ACTIVE
        self.assertTrue(s.refresh())
        s.idle = True
        self.t += pd.EVERY_ACTIVE
        self.assertFalse(s.refresh(), "dimmed: every 30 s")
        self.t += pd.EVERY_IDLE
        self.assertTrue(s.refresh())

    def test_trails(self):
        net = FakeNet()
        s = self.store(net)
        s.refresh()
        for _ in range(3):
            self.t += pd.EVERY_ACTIVE
            net.planes[0]["lon"] += 0.01
            s.refresh()
        self.assertEqual(len(s.trail("4076e3")), 4)
        self.t += pd.TRAIL_SECONDS + 1
        net.planes = []
        s.refresh()
        self.assertEqual(s.trail("4076e3"), [], "forgotten once gone")

    def test_falls_back_and_counts_the_daily_allowance(self):
        s = self.store(FakeNet(fail_fi=True))
        self.assertTrue(s.refresh())
        self.assertEqual(s.source, "adsb.lol")
        s.used["adsb.lol"] = (int(self.t // 86400), 400)
        self.t += pd.EVERY_ACTIVE
        self.assertFalse(s.refresh())
        self.assertIn("adsb.fi", s.error())
        self.t += 86400                                  # a new day
        self.assertTrue(s.refresh())

    def test_failure_keeps_old_planes(self):
        net = FakeNet()
        s = self.store(net)
        s.refresh()
        net.fail_all = True
        self.t += pd.EVERY_ACTIVE
        self.assertFalse(s.refresh())
        self.assertEqual(len(s.planes()), 4)
        self.assertEqual(s.error(), "adsb.lol: offline")

    def test_photo_on_request(self):
        s = self.store(FakeNet())
        s.refresh()
        s.refresh()
        s.want_photo = "4076e3"
        s.refresh()
        self.assertEqual(s.photo("4076e3"), b"not really a jpeg")


class TestConfig(unittest.TestCase):
    def test_planes_json(self):
        cfg = pd.load_config()
        self.assertTrue(-90 <= cfg["home"]["lat"] <= 90 and -180 <= cfg["home"]["lon"] <= 180)
        self.assertIn(cfg["range_nm"], cfg["ranges_nm"])
        self.assertLessEqual(max(cfg["ranges_nm"]), pd.QUERY_NM)


try:
    os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
    import pygame  # noqa: F401
    import planes_display
except ImportError:
    planes_display = None


@unittest.skipUnless(planes_display, "pygame not installed")
class TestScreens(unittest.TestCase):
    def setUp(self):
        import pygame
        self.t = T0
        self.store = pd.Store(CONFIG, cache_dir=tempfile.mkdtemp(), get=FakeNet(), clock=lambda: self.t)
        self.app = planes_display.PlanesDisplay(CONFIG, self.store, screen=pygame.Surface((480, 800)))

    def test_every_page_draws_with_and_without_data(self):
        for filled in (False, True):
            if filled:
                for _ in range(3):
                    self.store.refresh()
                    self.t += pd.EVERY_ACTIVE
            for page in planes_display.PAGES:
                self.app.page = page
                for nm in CONFIG["ranges_nm"]:
                    self.app.range_nm = nm
                    self.app.draw()

    def test_card_follows_the_nearest_until_tapped(self):
        self.store.refresh()
        self.app.draw()
        self.assertEqual(self.app.focus(self.app.planes())["hex"], "4076e3")
        x, y = self.app.spots["aaaaaa"]
        self.app.touch_down((x + 5, y + 5))
        self.app.touch_up((x + 5, y + 5))
        self.assertEqual(self.app.selected, "aaaaaa")
        self.app.draw()
        self.app.touch_down((120, 300))                  # empty sky
        self.app.touch_up((120, 300))
        self.assertIsNone(self.app.selected)

    def test_range_list_and_swipes(self):
        self.store.refresh()
        self.app.draw()
        r = self.app.buttons["range:20"]
        self.app.touch_down(r.center)
        self.app.touch_up(r.center)
        self.assertEqual(self.app.range_nm, 20)
        self.app.touch_down((400, 400))
        self.app.touch_up((100, 410))
        self.assertEqual(self.app.page, "List")
        self.app.draw()
        r = self.app.buttons["plane:cccccc"]
        self.app.touch_down(r.center)
        self.app.touch_up(r.center)
        self.assertEqual((self.app.page, self.app.selected), ("Radar", "cccccc"))

    def test_redraws_only_when_something_changes_and_idles(self):
        self.assertTrue(self.app.frame())
        self.assertFalse(self.app.frame())
        self.store.refresh()
        self.assertTrue(self.app.frame())
        self.app.last_touch -= planes_display.DIM_AFTER + 1
        self.assertTrue(self.app.frame(), "dimmed")
        self.assertTrue(self.store.idle, "fetch less often while dimmed")


if __name__ == "__main__":
    unittest.main()
