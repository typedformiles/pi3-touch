"""Off-Pi tests for the Moode Remote: its API client, and (when pygame is installed) its screens
driven by taps against a fake Moode web app - no network, nothing played.

Run on a Mac/PC:  python3 -m unittest discover tests
"""
import json
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "apps", "moode-remote"))
import moode_api  # noqa: E402

SONG = {"file": "A/one.flac", "title": "Hideaway", "artist": "Small Brown Bike", "album": "Dead Reckoning",
        "date": "2001", "id": "275", "pos": 0, "duration": 169}
QUEUE = [dict(SONG, by={"auto": False, "pid": 1})] + [
    {"file": f"B/{i}.flac", "title": f"Track {i}", "artist": "Artist", "album": "Album", "id": str(300 + i),
     "pos": i, "duration": 200, "by": {"auto": i % 2 == 0, "pid": 2}} for i in range(1, 15)]
PROFILES = [{"id": 1, "name": "Tim", "color": "#9a6bff", "avatar": None},
            {"id": 2, "name": "Joss", "color": "#ff6b9a", "avatar": None}]


class FakeApi:
    """Stands in for moode_api.Api: answers reads, records every action."""

    base = "http://moode.local:5005"

    def __init__(self):
        self.profile = None
        self.calls = []
        self.state = {"state": "play", "elapsed": 28.0, "duration": 169, "random": False, "repeat": False,
                      "single": False, "station": True, "station_owner": 2, "songid": "275", "version": "1",
                      "song": SONG}

    def status(self):
        return dict(self.state)

    def queue(self):
        return QUEUE

    def profiles(self):
        return PROFILES

    def likes(self):
        return ["A/one.flac"] if self.profile == 1 else []

    def moods(self):
        return ["Late night", "Chill", "Energetic"]

    def decades(self):
        return [{"decade": "2000s", "count": 1637}, {"decade": "1990s", "count": 1062}]

    def genres(self):
        return [{"genre": "Indie", "count": 587}, {"genre": "Emo", "count": 425}]

    def art(self, file):
        raise OSError("no art in tests")

    def __getattr__(self, name):                 # control, queue_op, bliss, station, takeover, quickplay, like
        def record(*a, **kw):
            self.calls.append((name, a, kw))
            return {"ok": True, "on": True, "count": 42, "added": 20}
        return record


class TestApiClient(unittest.TestCase):
    def test_requests_carry_the_listener_and_json(self):
        api = moode_api.Api("http://moode.test:5005/")
        api.profile = 2
        seen = []

        class Resp:
            def __init__(self, body):
                self.body = body

            def read(self):
                return self.body

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def urlopen(req, timeout):
            seen.append((req.full_url, req.get_method(), dict(req.header_items()), req.data, timeout))
            return Resp(b'{"ok": true}')

        with mock.patch.object(moode_api.urllib.request, "urlopen", urlopen):
            self.assertEqual(api.quickplay("genre", ["Indie"]), {"ok": True})
            api.control("repeatmode", mode="one")
            api.status()
        url, method, headers, data, _ = seen[0]
        self.assertEqual((url, method), ("http://moode.test:5005/api/quickplay", "POST"))
        self.assertEqual(headers.get("X-profile"), "2")
        self.assertEqual(json.loads(data), {"type": "genre", "value": ["Indie"]})
        self.assertEqual(json.loads(seen[1][3]), {"mode": "one", "cmd": "repeatmode"})
        self.assertEqual((seen[2][1], seen[2][4]), ("GET", 3), "status polls time out fast")

    def test_bliss_defaults_to_twenty(self):
        api = moode_api.Api()
        with mock.patch.object(api, "post", lambda path, body: (path, body)):
            self.assertEqual(api.bliss("append", file="x"), ("/api/bliss", {"n": 20, "file": "x", "action": "append"}))


try:
    os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
    import pygame
    import moode_display as md
except ImportError:
    md = None


@unittest.skipUnless(md, "pygame not installed")
class TestScreens(unittest.TestCase):
    def setUp(self):
        self.state_dir = tempfile.mkdtemp()
        p = mock.patch.object(md, "STATE_DIR", self.state_dir)
        p.start()
        self.addCleanup(p.stop)
        self.api = FakeApi()
        self.app = md.MoodeDisplay(api=self.api, screen=pygame.Surface((480, 800)), start_threads=False)
        self.app.poll_once()

    def tap(self, name):
        self.app._drawn = None
        self.app.frame()
        r = self.app.buttons[name]
        self.app.touch_down(r.center)
        self.app.touch_up(r.center)
        self.app.frame()                              # the loop redraws before the next touch

    def run_jobs(self):
        while not self.app.jobs.empty():
            self.app.run_job(*self.app.jobs.get())

    def test_first_run_listens_as_the_first_profile(self):
        self.assertEqual(self.api.profile, 1)
        self.assertEqual(self.app.liked, {"A/one.flac"})

    def test_every_tab_and_overlay_draws(self):
        for tab in md.TABS:
            self.app.tab = tab
            self.app.draw()
        self.app.bliss_sheet()
        self.app.draw()
        self.app.sheet = None
        self.app.picker = True
        self.app.draw()

    def test_offline_draws(self):
        self.app.connected = False
        self.app.draw()

    def test_transport_and_modes(self):
        self.tap("play")
        self.tap("next")
        self.tap("shuffle")
        self.tap("repeat")
        self.run_jobs()
        self.assertEqual([c[0] for c in self.api.calls], ["control"] * 4)
        self.assertEqual([c[2] for c in self.api.calls],
                         [{}, {}, {"on": True}, {"mode": "all"}])
        self.assertEqual([c[1] for c in self.api.calls][:2], [("toggle",), ("next",)])

    def test_like_shows_at_once_then_tells_moode(self):
        self.tap("like")                              # already liked -> unlike
        self.assertNotIn("A/one.flac", self.app.liked)
        self.run_jobs()
        self.assertEqual(self.api.calls[-1], ("like", ("A/one.flac", False), {}))

    def test_bliss_sheet_offers_takeover_of_someone_elses_station(self):
        self.tap("bliss")
        labels = [a[1] for a in self.app.sheet["actions"]]
        self.assertIn("Take over the station (now Joss's)", labels)
        self.tap("act:1")                             # add 20 similar
        self.assertIsNone(self.app.sheet)
        self.run_jobs()
        self.assertEqual(self.api.calls[-1][0], "bliss")
        self.assertEqual(self.app.toast[0], "Added 20 similar ✦")

    def test_tapping_outside_a_sheet_closes_it(self):
        self.tap("bliss")
        self.app.touch_down((240, 100))
        self.app.touch_up((240, 100))
        self.assertIsNone(self.app.sheet)
        self.assertEqual(self.api.calls, [])

    def test_up_next_rows_paging_and_track_actions(self):
        self.tap("tab:Next")
        self.assertIn("track:275", self.app.buttons, "the current song leads")
        self.tap("page-down")
        self.assertNotIn("track:275", self.app.buttons)
        self.tap("page-up")
        self.tap("track:303")
        self.assertEqual([a[1] for a in self.app.sheet["actions"]],
                         ["Play now", "Play next", "Like", "More like this", "Remove from the queue"])
        self.tap("act:1")                             # play next -> after the current song
        self.run_jobs()
        self.assertEqual(self.api.calls[-1], ("queue_op", ("move",), {"id": "303", "to": 1}))

    def test_clear_needs_two_taps(self):
        self.tap("tab:Next")
        self.tap("clear")
        self.run_jobs()
        self.assertEqual(self.api.calls, [])
        self.tap("clear")
        self.run_jobs()
        self.assertEqual(self.api.calls[-1], ("queue_op", ("clearupnext",), {}))

    def test_4u_tiles_play_and_go_to_now(self):
        self.tap("tab:4U")
        self.tap("mood:Chill")
        self.assertEqual(self.app.tab, "Now")
        self.tap("tab:4U")
        self.tap("genre:Indie")
        self.run_jobs()
        self.assertEqual([c[:2] for c in self.api.calls],
                         [("quickplay", ("mood", "Chill")), ("quickplay", ("genre", ["Indie"]))])
        self.assertEqual(self.app.toast[0], "Playing Indie · 42 tracks")

    def test_switching_listener_is_remembered(self):
        self.tap("who")
        self.tap("pick:2")
        self.run_jobs()
        self.assertEqual(self.api.profile, 2)
        self.assertEqual(self.app.liked, set(), "Joss's likes now")
        again = md.MoodeDisplay(api=FakeApi(), screen=pygame.Surface((480, 800)), start_threads=False)
        self.assertEqual(again.api.profile, 2)

    def test_swipes_change_tab(self):
        self.app.frame()
        self.app.touch_down((400, 400))
        self.app.touch_up((100, 400))
        self.assertEqual(self.app.tab, "Next")

    def test_redraws_only_on_change(self):
        self.assertTrue(self.app.frame())
        self.assertFalse(self.app.frame())
        self.api.state["elapsed"] = 29.0              # a second on
        self.app.poll_once()
        self.assertTrue(self.app.frame())
        self.app.say("hello")
        self.assertTrue(self.app.frame())
        self.app.toast = ("hello", time.time() - 1)   # message gone
        self.assertTrue(self.app.frame())


if __name__ == "__main__":
    unittest.main()
