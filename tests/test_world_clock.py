"""Off-Pi tests for the World Clock: sun position, sky colours, labels (and its screen, when
pygame is installed).

Run on a Mac/PC:  python3 -m unittest discover tests
"""
import json
import os
import sys
import tempfile
import time
import unittest
import urllib.error
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "apps", "world-clock"))
import city_data as cd  # noqa: E402
import sky  # noqa: E402
from cities import CITIES  # noqa: E402

TOKYO = next(c for c in CITIES if c["name"] == "Tokyo")
LONDON_CITY = next(c for c in CITIES if c["name"] == "London")
DUBAI = next(c for c in CITIES if c["name"] == "Dubai")
UTC = cd.UTC

RSS = b"""<?xml version="1.0"?><rss><channel><title>Feed</title>
<item><title>  First   headline </title><pubDate>Wed, 07 Oct 2026 09:00:00 +0000</pubDate></item>
<item><title></title></item>
<item><title>Second headline</title></item>
</channel></rss>"""


def fake_responses(url):
    """What each free API sends back, trimmed to what the pages read."""
    if "air-quality" in url:
        return json.dumps({"current": {"us_aqi": 63, "pm2_5": 20}}).encode()
    if "marine" in url:
        return json.dumps({"current": {"sea_surface_temperature": 22.4, "wave_height": 0.2}}).encode()
    if "open-meteo" in url:
        days = [f"2026-10-{d:02d}" for d in range(7, 13)]
        hours = [f"2026-10-07T{h:02d}:00" for h in range(24)] + [f"2026-10-08T{h:02d}:00" for h in range(24)]
        n = len(hours)
        return json.dumps({
            "current": {"temperature_2m": 20.3, "apparent_temperature": 21, "weather_code": 0, "is_day": 0,
                        "wind_speed_10m": 1.8, "wind_direction_10m": 90},
            "hourly": {"time": hours, "temperature_2m": [18.0] * n, "weather_code": [2] * n, "is_day": [1] * n,
                       "precipitation_probability": [40] * n},
            "daily": {"time": days, "weather_code": [0] * 6, "temperature_2m_max": [24.0] * 6,
                      "temperature_2m_min": [15.0] * 6, "precipitation_probability_max": [10] * 6,
                      "sunrise": [d + "T05:40" for d in days], "sunset": [d + "T17:17" for d in days],
                      "daylight_duration": [41760.0] * 6, "uv_index_max": [2.75] * 6}}).encode()
    if "frankfurter" in url:
        return json.dumps({"date": "2026-10-06", "rates": {"USD": 1.3276, "EUR": 1.18, "JPY": 209.88, "AUD": 1.9015}}).encode()
    if "nager" in url:
        return json.dumps([{"date": "2026-10-12", "name": "Sports Day", "countryCode": "JP", "global": True, "counties": None},
                           {"date": "2026-12-28", "name": "St. Stephen's Day", "countryCode": "JP", "global": True, "counties": None},
                           {"date": "2026-11-30", "name": "Local Day", "countryCode": "JP", "global": False, "counties": ["JP-01"]}]).encode()
    if "usgs" in url:
        return json.dumps({"features": [
            {"properties": {"mag": 4.5, "place": "37 km ESE of Iwaki, Japan", "time": 1791333761953},
             "geometry": {"coordinates": [141.3, 36.9, 10]}},
            {"properties": {"mag": 2.6, "place": "tiny", "time": 1791333761953},
             "geometry": {"coordinates": [139.7, 35.7, 10]}}]}).encode()
    if url.startswith("http"):
        return RSS
    raise AssertionError(url)

LONDON = ZoneInfo("Europe/London")


def london(s):
    return datetime.fromisoformat(s).replace(tzinfo=LONDON)


class TestSun(unittest.TestCase):
    def test_london_sunrise_noon_and_night(self):
        # 7 Oct 2026: sunrise 07:13, sunset 18:27 BST (as Open-Meteo gave for Maidenhead)
        self.assertAlmostEqual(sky.sun_elevation(51.51, -0.13, london("2026-10-07T07:13")), -0.8, delta=1.2)
        self.assertAlmostEqual(sky.sun_elevation(51.51, -0.13, london("2026-10-07T18:27")), -0.8, delta=1.2)
        self.assertAlmostEqual(sky.sun_elevation(51.51, -0.13, london("2026-10-07T12:50")), 32, delta=1.5)
        self.assertLess(sky.sun_elevation(51.51, -0.13, london("2026-10-07T00:50")), -40)

    def test_southern_hemisphere(self):
        # Sydney local noon (AEDT, UTC+11) in October: sun high in the north
        self.assertGreater(sky.sun_elevation(-33.87, 151.21, datetime.fromisoformat("2026-10-07T01:50+00:00")), 55)


class TestColours(unittest.TestCase):
    def test_keyframes_and_blending(self):
        self.assertEqual(sky.sky_colour(-60), sky.SKY[0][1])
        self.assertEqual(sky.sky_colour(80), sky.SKY[-1][1])
        self.assertEqual(sky.sky_colour(sky.SKY[3][0]), sky.SKY[3][1])
        mid = sky.sky_colour((sky.SKY[0][0] + sky.SKY[1][0]) / 2)
        self.assertEqual(mid, sky.mix(sky.SKY[0][1], sky.SKY[1][1], 0.5))

    def test_night_is_dark_and_day_is_light(self):
        self.assertFalse(sky.is_light(sky.sky_colour(-30)))
        self.assertTrue(sky.is_light(sky.sky_colour(30)))

    def test_keyframes_are_in_order(self):
        elevations = [e for e, _ in sky.SKY]
        self.assertEqual(elevations, sorted(elevations))


class TestLabels(unittest.TestCase):
    def test_offsets_follow_daylight_saving(self):
        summer, winter = london("2026-10-07T12:00"), london("2026-11-07T12:00")
        ny, tokyo = ZoneInfo("America/New_York"), ZoneInfo("Asia/Tokyo")
        self.assertEqual(sky.offset_label(summer, ny, LONDON), "−5h")
        self.assertEqual(sky.offset_label(summer, tokyo, LONDON), "+8h")
        self.assertEqual(sky.offset_label(winter, tokyo, LONDON), "+9h", "UK clocks went back")
        self.assertEqual(sky.offset_label(summer, LONDON, LONDON), "")
        self.assertEqual(sky.offset_label(summer, ZoneInfo("Asia/Kolkata"), LONDON), "+4h30")

    def test_day_labels(self):
        late = london("2026-10-07T22:30")
        self.assertEqual(sky.day_label(late, ZoneInfo("Asia/Tokyo"), LONDON), "tomorrow")
        self.assertEqual(sky.day_label(late, ZoneInfo("America/New_York"), LONDON), "")
        early = london("2026-10-07T03:00")
        self.assertEqual(sky.day_label(early, ZoneInfo("America/Los_Angeles"), LONDON), "yesterday")


class TestCityData(unittest.TestCase):
    def test_news_feed(self):
        items = cd.parse_news(RSS)
        self.assertEqual([i["title"] for i in items], ["First headline", "Second headline"])
        self.assertEqual(items[0]["time"], "2026-10-07T09:00:00+00:00")
        self.assertIsNone(items[1]["time"])

    def test_holidays_for_a_region_with_local_names(self):
        raw = [{"date": "2026-10-12", "name": "Columbus Day", "countryCode": "US", "global": False, "counties": ["US-NY"]},
               {"date": "2026-10-12", "name": "Indigenous Peoples' Day", "countryCode": "US", "global": False, "counties": ["US-CA"]},
               {"date": "2026-11-11", "name": "Veterans Day", "countryCode": "US", "global": True, "counties": None},
               {"date": "2026-12-26", "name": "St. Stephen's Day", "countryCode": "GB", "global": True, "counties": None}]
        self.assertEqual([h["name"] for h in cd.holidays_for(raw, "US-NY")], ["Columbus Day", "Veterans Day", "Boxing Day"])
        self.assertEqual([h["name"] for h in cd.holidays_for(raw, "US-CA")][0], "Indigenous Peoples' Day")

    def test_rates_including_the_pegged_dirham(self):
        rates = {"rates": {"USD": 1.3276, "JPY": 209.88}}
        self.assertEqual(cd.rate(rates, "GBP"), 1.0)
        self.assertEqual(cd.rate(rates, "JPY"), 209.88)
        self.assertAlmostEqual(cd.rate(rates, "AED"), 1.3276 * 3.6725)
        self.assertIsNone(cd.rate(rates, "AUD"))

    def test_nearby_quakes(self):
        quakes = cd.nearby_quakes(json.loads(fake_responses("https://usgs")), TOKYO["lat"], TOKYO["lon"])
        self.assertEqual(len(quakes), 1, "the M2.6 is too small to mention")
        self.assertAlmostEqual(quakes[0]["km"], 200, delta=25)
        self.assertEqual(cd.nearby_quakes(json.loads(fake_responses("https://usgs")), 51.5, 0), [])

    def test_moon(self):
        age, lit, name = cd.moon(datetime(2026, 10, 7, 10, tzinfo=UTC))
        self.assertEqual(name, "Waning crescent")
        self.assertAlmostEqual(lit, 0.17, delta=0.05)
        self.assertEqual(cd.moon(cd.NEW_MOON)[2], "New moon")
        self.assertEqual(cd.moon(cd.NEW_MOON + timedelta(days=cd.SYNODIC / 2))[2], "Full moon")

    def test_call_window(self):
        london, tokyo, la = ZoneInfo("Europe/London"), ZoneInfo("Asia/Tokyo"), ZoneInfo("America/Los_Angeles")
        now = datetime(2026, 10, 7, 9, 13, tzinfo=UTC)               # 10:13 London, 18:13 Tokyo
        good, (a, b) = cd.call_window(now, tokyo, london)
        self.assertTrue(good)
        self.assertEqual((a.strftime("%H:%M"), b.strftime("%H:%M")), ("08:00", "14:00"))
        good, (a, b) = cd.call_window(now, la, london)               # 02:13 in San Francisco
        self.assertFalse(good)
        self.assertEqual((a.strftime("%H:%M"), b.strftime("%H:%M")), ("16:00", "22:00"))
        good, (a, b) = cd.call_window(now, ZoneInfo("Australia/Sydney"), london)
        self.assertEqual((a.strftime("%H:%M"), b.strftime("%H:%M")), ("08:00", "12:00"))

    def test_exchange_hours(self):
        tse = TOKYO["exchange"]
        at = lambda s: datetime.fromisoformat(s).replace(tzinfo=ZoneInfo("Asia/Tokyo"))  # noqa: E731
        self.assertEqual(cd.exchange_status(at("2026-10-07T10:00"), tse)["state"], "open")
        st = cd.exchange_status(at("2026-10-07T12:00"), tse)
        self.assertEqual((st["state"], st["until"].strftime("%H:%M")), ("break", "12:30"))
        st = cd.exchange_status(at("2026-10-07T16:00"), tse)
        self.assertEqual((st["state"], st["until"].strftime("%a %H:%M")), ("closed", "Thu 09:00"))
        st = cd.exchange_status(at("2026-10-09T16:00"), tse)       # Friday evening -> Monday
        self.assertEqual(st["until"].strftime("%a %H:%M"), "Mon 09:00")
        st = cd.exchange_status(at("2026-10-07T07:00"), tse)
        self.assertEqual((st["state"], st["until"].strftime("%a %H:%M")), ("closed", "Wed 09:00"))

    def test_bands(self):
        self.assertEqual(cd.band(42, cd.AQI), "Good")
        self.assertEqual(cd.band(159, cd.AQI), "Unhealthy")
        self.assertEqual(cd.band(7.35, cd.UV), "High")
        self.assertEqual(cd.band(None, cd.UV), "")

    def test_every_city_is_complete(self):
        for c in CITIES:
            ZoneInfo(c["tz"]), ZoneInfo(c["exchange"]["tz"])
            self.assertIn(c["currency"], ("GBP", "USD", "EUR", "JPY", "AUD", "AED"))
            self.assertTrue(c["news"][1].startswith("https://"))
            self.assertTrue(c.get("holidays") or c.get("weekend"), c["name"])
            for kind in cd.kinds(c):
                self.assertTrue(cd.urls(c, kind).startswith("https://"))


class TestCityStore(unittest.TestCase):
    def setUp(self):
        self.t = 1_791_360_000.0
        self.calls = []

        def fetch(url):
            self.calls.append(url)
            return fake_responses(url)
        self.store = cd.CityStore(CITIES, cache_dir=tempfile.mkdtemp(), fetch=fetch, clock=lambda: self.t)

    def test_fetches_only_what_the_city_needs_then_waits(self):
        self.assertEqual(self.store.refresh(TOKYO), len(cd.kinds(TOKYO)))
        self.assertEqual(self.store.get(TOKYO, "news")["data"][0]["title"], "First headline")
        self.assertEqual([h["name"] for h in self.store.get(TOKYO, "holidays")["data"]], ["Sports Day", "St. Stephen's Day"])
        self.assertEqual(self.store.refresh(TOKYO), 0, "all fresh")
        self.assertFalse(any("marine" in u for u in self.calls if "51.5" in u))
        # rates and quakes are shared: London only needs its own things
        self.calls.clear()
        self.store.refresh(LONDON_CITY)
        self.assertFalse(any("frankfurter" in u or "usgs" in u or "marine" in u for u in self.calls))

    def test_quakes_cached_small(self):
        self.store.refresh(TOKYO)
        self.assertEqual(len(self.store.get(TOKYO, "quakes")["data"]["features"]), 1)

    def test_cache_survives_a_restart(self):
        self.store.refresh(TOKYO)
        again = cd.CityStore(CITIES, cache_dir=self.store.cache_dir, fetch=lambda u: 1 / 0)
        self.assertIsNotNone(again.get(TOKYO, "weather"))

    def test_failure_keeps_old_data_and_backs_off(self):
        self.store.refresh(TOKYO)
        self.t += cd.EVERY["news"] + 1

        def down(url):
            raise urllib.error.URLError("down")
        self.store.fetch = down
        self.assertGreater(self.store.refresh(TOKYO), 0)
        self.assertEqual(self.store.error(TOKYO, "news"), "offline")
        self.assertIsNotNone(self.store.get(TOKYO, "news"))
        self.assertEqual(self.store.refresh(TOKYO), 0, "waits before retrying")


try:
    os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
    import pygame
    import world_clock
except ImportError:
    world_clock = None


@unittest.skipUnless(world_clock, "pygame not installed")
class TestScreen(unittest.TestCase):
    def setUp(self):
        self.store = cd.CityStore(CITIES, cache_dir=tempfile.mkdtemp(), fetch=fake_responses)
        self.app = world_clock.WorldClock(screen=pygame.Surface((480, 800)), store=self.store)
        self.now = datetime.fromisoformat("2026-10-07T08:47:23+00:00")

    def test_draws_round_the_clock(self):
        for h in range(0, 24, 3):
            self.app.draw(self.now + timedelta(hours=h))

    def test_redraws_once_a_second(self):
        self.assertTrue(self.app.frame(self.now))
        self.assertFalse(self.app.frame(self.now + timedelta(milliseconds=400)))
        self.assertTrue(self.app.frame(self.now + timedelta(seconds=1)))

    def test_face_cache_reuses_faces(self):
        self.app.draw(self.now)
        n = len(self.app.faces)
        self.app.draw(self.now + timedelta(seconds=1))
        self.assertEqual(len(self.app.faces), n, "a second later the skies haven't changed")

    def tap(self, name):
        self.app.draw(self.now)
        r = self.app.buttons[name]
        self.app.touch_down(r.center)
        self.app.touch_up(r.center)

    def test_every_city_page_draws_with_and_without_data(self):
        for filled in (False, True):
            for i, c in enumerate(CITIES):
                if filled:
                    self.store.refresh(c)
                self.app.open_city(i)
                for page in world_clock.PAGES:
                    self.app.page = page
                    self.app.draw(self.now)

    def test_tap_a_clock_then_back(self):
        self.tap("city4")
        self.assertEqual(self.app.city, 4)
        self.assertIs(self.store.active, CITIES[4], "fetching starts for that city")
        self.tap("page:News")
        self.assertEqual(self.app.page, "News")
        self.tap("back")
        self.assertIsNone(self.app.city)
        self.assertIsNone(self.store.active, "nothing fetched on the clocks")

    def test_swipe_turns_city_pages(self):
        self.app.open_city(0)
        self.app.draw(self.now)
        self.app.touch_down((400, 400))
        self.app.touch_up((100, 400))
        self.assertEqual(self.app.page, "Weather")

    def test_city_pages_cycle_then_go_back_to_the_clocks(self):
        self.app.open_city(1)
        self.app.last_touch = time.time() - world_clock.CYCLE_IDLE - 1
        self.app.last_cycle = time.time() - world_clock.CYCLE_EVERY - 1
        self.app.tick()
        self.assertEqual(self.app.page, "Weather")
        self.app.tick()
        self.assertEqual(self.app.page, "Weather", "not faster than CYCLE_EVERY")
        self.app.last_touch = time.time() - world_clock.CITY_IDLE - 1
        self.app.tick()
        self.assertIsNone(self.app.city)

    def test_city_pages_redraw_per_minute_not_per_second(self):
        self.app.open_city(2)
        self.assertTrue(self.app.frame(self.now))
        self.assertFalse(self.app.frame(self.now + timedelta(seconds=5)))
        self.store.refresh(CITIES[2])                 # data arrives
        self.assertTrue(self.app.frame(self.now + timedelta(seconds=6)))

    def test_sleep_button(self):
        self.app.draw(self.now)
        self.app.tap(self.app.buttons["sleep"].center)
        self.assertTrue(self.app.sleeping)
        self.app.tap((240, 400))
        self.assertFalse(self.app.sleeping, "any tap wakes it")


if __name__ == "__main__":
    unittest.main()
