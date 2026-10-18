"""Off-Pi tests for the Departures app's data side (and its screens, when pygame is installed).

Run on a Mac/PC:  python3 -m unittest discover tests
No network: fetches are faked with TfL-shaped predictions for Taplow and Paddington.
"""
import os
import sys
import unittest
import urllib.error
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "apps", "departures"))
import departures_data as dd  # noqa: E402

NOW = datetime(2026, 10, 18, 20, 15, tzinfo=dd.UTC)
CONFIG = {"line": "elizabeth", "stations": [
    {"name": "Taplow", "stop": "910GTAPLOW", "sections": [{"title": "To London", "direction": "inbound"},
                                                          {"title": "To Reading", "direction": "outbound"}]},
    {"name": "Paddington", "stop": "910GPADTLL", "direction": "outbound",
     "to": {"name": "Taplow", "via": ["Maidenhead", "Twyford", "Reading"], "minutes": 35}}]}


def pred(stop, vehicle, mins, dest, dest_id, direction, plat, keep=None):
    t = NOW + timedelta(minutes=mins)
    stamp = "%Y-%m-%dT%H:%M:%SZ"
    return {"naptanId": stop, "vehicleId": vehicle, "expectedArrival": t.strftime(stamp),
            "timeToLive": (t + timedelta(minutes=keep if keep is not None else -1)).strftime(stamp),
            "destinationName": dest, "destinationNaptanId": dest_id, "direction": direction,
            "platformName": plat, "lineId": "elizabeth"}


def taplow():
    return [pred("910GTAPLOW", "V1", 1, "Abbey Wood", "910GABWDXR", "inbound", "Platform 4"),
            pred("910GTAPLOW", "V2", 52, "Reading Rail Station", "910GRDNGSTN", "outbound", "Platform 3"),
            pred("910GTAPLOW", "V3", 31, "London Paddington Rail Station", "910GPADTON", "inbound", "Platform 4"),
            pred("910GTAPLOW", "V9", 120, "London Paddington Rail Station", "910GPADTON", "inbound", "Platform 4"),
            pred("910GTAPLOW", "V1", 3, "Abbey Wood", "910GABWDXR", "inbound", "Platform 4")]   # a repeat


def paddington():
    return [pred("910GPADTLL", "V2", 17, "Reading Rail Station", "910GRDNGSTN", "outbound", "B"),
            pred("910GPADTLL", "V4", 21, "Heathrow Terminal 4 Rail Station", "910GHTRWTM4", "outbound", "B"),
            pred("910GPADTLL", "V5", 47, "Maidenhead Rail Station", "910GMDNHEAD", "outbound", "B"),
            pred("910GPADTLL", "V6", 5, "Abbey Wood", "910GABWDXR", "inbound", "A"),
            pred("910GPADTLL", "V7", 0, "Paddington", "910GPADTLL", "", "B"),                    # ends here
            pred("910GPADTLL", "V8", 0, "Reading Rail Station", "910GRDNGSTN", "outbound",
                 "Platform Unknown", keep=80),                                                    # a ghost
            pred("910GPADTLL", "V9", 60, "Reading Rail Station", "910GRDNGSTN", "outbound", "B")]  # V9 then comes back


class FakeTfl:
    def __init__(self, fail=None):
        self.calls, self.fail = [], fail

    def __call__(self, url, timeout=15):
        self.calls.append(url[len(dd.API):])
        if self.fail:
            raise self.fail
        if url.endswith("/StopPoint/910GTAPLOW/Arrivals"):
            return taplow()
        if url.endswith("/StopPoint/910GPADTLL/Arrivals"):
            return paddington()
        if url.endswith("/Line/elizabeth/Status"):
            return [{"lineStatuses": [{"statusSeverity": 7, "statusSeverityDescription": "Reduced Service",
                                       "reason": "ELIZABETH LINE: Sunday 18 October, a reduced service operates."},
                                      {"statusSeverity": 9, "statusSeverityDescription": "Minor Delays", "reason": ""}]}]
        raise AssertionError("unexpected url " + url)


class TestSums(unittest.TestCase):
    def test_names(self):
        self.assertEqual(dd.place("Reading Rail Station"), "Reading")
        self.assertEqual(dd.place("London Paddington Rail Station"), "Paddington")
        self.assertEqual(dd.place("Heathrow Terminal 4 Rail Station"), "Heathrow T4")
        self.assertEqual(dd.place("Heathrow Terminals 2 & 3 Rail Station"), "Heathrow T2&3")
        self.assertEqual((dd.platform("Platform 4"), dd.platform("B"), dd.platform("Platform Unknown")), ("4", "B", ""))

    def test_trains_sorted_merged_and_filtered(self):
        deps = dd.trains(taplow(), CONFIG["stations"][0], NOW)
        self.assertEqual([(d["vehicle"], d["dest"]) for d in deps],
                         [("V1", "Abbey Wood"), ("V3", "Paddington"), ("V2", "Reading"), ("V9", "Paddington")])
        pad = dd.trains(paddington(), CONFIG["stations"][1], NOW)
        self.assertEqual([d["vehicle"] for d in pad], ["V2", "V4", "V5", "V9"],
                         "westbound only; no train ending here; no ghost")
        later = NOW + timedelta(minutes=2)
        self.assertNotIn("V1", [d["vehicle"] for d in dd.trains(taplow()[:1], CONFIG["stations"][0], later)],
                         "gone a while ago")

    def test_journeys_home(self):
        st = CONFIG["stations"][1]
        deps = dd.journeys(dd.trains(paddington(), st, NOW), dd.trains(taplow(), CONFIG["stations"][0], NOW), st["to"])
        by = {d["vehicle"]: d for d in deps}
        self.assertEqual((by["V2"]["home"] - by["V2"]["time"], by["V2"]["estimated"]), (timedelta(minutes=35), False))
        self.assertFalse(by["V4"]["calls"], "Heathrow trains don't go to Taplow")
        self.assertTrue(by["V5"]["estimated"], "Maidenhead train: not in Taplow's list yet, so estimated")
        self.assertEqual(by["V5"]["home"] - by["V5"]["time"], timedelta(minutes=35), "from the matched ride time")
        self.assertTrue(by["V9"]["estimated"], "same id coming back east isn't this train arriving")

    def test_countdown(self):
        self.assertEqual(dd.countdown(NOW + timedelta(seconds=50), NOW), "Due")
        self.assertEqual(dd.countdown(NOW + timedelta(minutes=12, seconds=30), NOW), "12 min")
        self.assertEqual(dd.countdown(NOW + timedelta(minutes=116), NOW), "1h 56m")

    def test_status_worst_first(self):
        st = dd.parse_status(FakeTfl()(dd.API + "/Line/elizabeth/Status"))
        self.assertEqual([s["text"] for s in st], ["Reduced Service", "Minor Delays"])
        self.assertTrue(st[0]["reason"].startswith("Sunday"), "line name prefix dropped")
        self.assertEqual(dd.parse_status([]), [])


class TestStore(unittest.TestCase):
    def setUp(self):
        self.t = NOW.timestamp()
        self.api = FakeTfl()
        self.store = dd.Store(CONFIG, fetch=self.api, clock=lambda: self.t)

    def test_fetches_then_waits(self):
        self.assertEqual(self.store.refresh(), 3)
        self.assertEqual(self.store.refresh(), 0)
        self.t += dd.ARRIVALS_EVERY
        self.assertEqual(self.store.refresh(), 2, "trains every 30 s; status less often")
        self.t += dd.STATUS_EVERY
        self.assertEqual(self.store.refresh(), 3)

    def test_departures_and_status(self):
        self.assertIsNone(self.store.departures(CONFIG["stations"][1], NOW))
        self.store.refresh()
        deps = self.store.departures(CONFIG["stations"][1], NOW)
        self.assertEqual(deps[0]["home"], NOW + timedelta(minutes=52))
        self.assertEqual(self.store.status()[0]["severity"], 7)

    def test_failure_keeps_old_data_and_backs_off(self):
        self.store.refresh()
        self.t += dd.ARRIVALS_EVERY
        self.store.fetch = FakeTfl(fail=urllib.error.URLError("down"))
        self.assertEqual(self.store.refresh(), 2)
        self.assertEqual(self.store.error(), "offline")
        self.assertIsNotNone(self.store.get("910GTAPLOW"))
        self.assertEqual(self.store.refresh(), 0, "waits before retrying")
        self.t += dd.RETRY_AFTER
        self.store.fetch = self.api
        self.store.refresh()
        self.assertIsNone(self.store.error())


class TestConfig(unittest.TestCase):
    def test_departures_json(self):
        cfg = dd.load_config()
        names = [s["name"] for s in cfg["stations"]]
        for s in cfg["stations"]:
            self.assertRegex(s["stop"], r"^910G[A-Z0-9]+$", "a TfL rail station id")
            if "to" in s:
                self.assertIn(s["to"]["name"], names, "the 'to' station must be on the list too")


try:
    os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
    import pygame  # noqa: F401
    import departures_display
except ImportError:
    departures_display = None


@unittest.skipUnless(departures_display, "pygame not installed")
class TestScreens(unittest.TestCase):
    def setUp(self):
        import pygame
        self.t = NOW.timestamp()
        self.store = dd.Store(CONFIG, fetch=FakeTfl(), clock=lambda: self.t)
        self.app = departures_display.DeparturesDisplay(CONFIG, self.store, screen=pygame.Surface((480, 800)))
        self.now = NOW.astimezone(dd.LOCAL)

    def test_every_station_draws_with_and_without_data(self):
        for filled in (False, True):
            if filled:
                self.store.refresh()
            for i in range(len(CONFIG["stations"])):
                self.app.station_i = i
                for opened in (False, True):
                    self.app.status_open = opened
                    for now in (self.now, self.now + timedelta(hours=3)):
                        self.app.draw(now)

    def tap_button(self, name):
        self.app.draw(self.now)
        r = self.app.buttons[name]
        self.app.touch_down(r.center)
        self.app.touch_up(r.center)

    def test_taps_and_swipes(self):
        self.store.refresh()
        self.tap_button("station1")
        self.assertEqual(self.app.station_i, 1)
        self.tap_button("status")
        self.assertTrue(self.app.status_open)
        self.app.touch_down((100, 400))
        self.app.touch_up((400, 410))
        self.assertEqual(self.app.station_i, 0)

    def test_redraws_only_when_something_changes(self):
        now = self.now
        self.assertTrue(self.app.frame(now))
        self.assertFalse(self.app.frame(now + timedelta(seconds=20)))
        self.store.refresh()
        self.assertTrue(self.app.frame(now))
        self.assertFalse(self.app.frame(now))
        self.assertTrue(self.app.frame(now + timedelta(minutes=1)), "the countdowns move on")


if __name__ == "__main__":
    unittest.main()
