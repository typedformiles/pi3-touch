"""Off-Pi tests for the Pi-hole app's data side (and its screens, when pygame is installed).

Run on a Mac/PC:  python3 -m unittest discover tests
No network: the Pi-hole's v6 API is faked below (FakePihole), sessions and all.
"""
import os
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.parse
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "apps", "pihole"))
import pihole_data as pd  # noqa: E402

NOW = 1_791_394_200.0           # Wed 7 Oct 2026 17:30 BST


def history(now=NOW):
    """24 hours of 10-minute buckets: busier in the evening, a steady share blocked."""
    out = []
    for i in range(144):
        t = now - (144 - i) * 600
        total = 200 + 150 * (i % 36 > 24)
        out.append({"timestamp": t, "total": total, "cached": total // 4, "blocked": total // 6,
                    "forwarded": total - total // 4 - total // 6})
    return out


class FakePihole:
    """Answers like Pi-hole v6's /api: a password login, a session id, blocking with a timer."""

    def __init__(self, password="app-pass", down=False):
        self.password, self.down = password, down
        self.sids, self.calls = set(), []
        self.blocking, self.timer = "enabled", None
        self.next_sid = 0

    def expire_sessions(self):
        self.sids.clear()

    def __call__(self, method, url, headers, body=None, timeout=8):
        self.calls.append((method, url, headers.get("X-FTL-SID"), body))
        if self.down:
            raise urllib.error.URLError("no route to host")
        u = urllib.parse.urlparse(url)
        path, q = u.path, dict(urllib.parse.parse_qsl(u.query))
        if path == "/api/auth" and method == "POST":
            if body.get("password") != self.password:
                return 401, {"session": {"valid": False, "sid": None, "validity": -1, "message": "password incorrect"}}
            self.next_sid += 1
            sid = f"sid{self.next_sid}"
            self.sids.add(sid)
            return 200, {"session": {"valid": True, "sid": sid, "validity": 1800, "message": "app-password correct"}}
        if headers.get("X-FTL-SID") not in self.sids:
            return 401, {"error": {"key": "unauthorized", "message": "Unauthorized"}}
        if path == "/api/auth" and method == "DELETE":
            self.sids.discard(headers["X-FTL-SID"])
            return 204, None
        if path == "/api/stats/summary":
            return 200, {"queries": {"total": 41203, "blocked": 7512, "percent_blocked": 18.23, "frequency": 0.48,
                                     "unique_domains": 2210, "forwarded": 20000, "cached": 13691},
                         "clients": {"active": 14, "total": 22},
                         "gravity": {"domains_being_blocked": 1234567, "last_update": int(NOW) - 3 * 86400}}
        if path == "/api/dns/blocking":
            if method == "POST":
                self.blocking = "enabled" if body["blocking"] else "disabled"
                self.timer = body.get("timer")
            return 200, {"blocking": self.blocking, "timer": self.timer}
        if path == "/api/history":
            return 200, {"history": history()}
        if path == "/api/stats/top_domains":
            n = int(q.get("count", 10))
            if q.get("blocked") == "true":
                doms = ["googleads.g.doubleclick.net", "app-measurement.com", "browser.events.data.msn.com",
                        "graph.facebook.com", "ads.tiktok.com", "metrics.icloud.com", "settings-win.data.microsoft.com",
                        "sb.scorecardresearch.com", "pagead2.googlesyndication.com", "notify.bugsnag.com",
                        "analytics.google.com", "pixel.wp.com"]
            else:
                doms = ["gateway.icloud.com", "www.google.com", "api.spotify.com", "moode.local", "time.apple.com",
                        "outlook.office365.com", "github.com", "api.carbonintensity.org.uk", "bbc.co.uk",
                        "i.ytimg.com", "weather.apple.com", "open-meteo.com"]
            return 200, {"domains": [{"domain": d, "count": 900 - 70 * i} for i, d in enumerate(doms[:n])]}
        if path == "/api/stats/top_clients":
            names = [("10.0.0.9", "tims-macbook-pro.lan"), ("10.0.0.12", "tims-iphone.lan"), ("10.0.0.35", "mooderemote.lan"),
                     ("10.0.0.20", "moode.lan"), ("10.0.0.41", ""), ("10.0.0.22", "living-room-tv.lan")]
            return 200, {"clients": [{"ip": ip, "name": nm, "count": 9000 - 1300 * i} for i, (ip, nm) in enumerate(names)]}
        if path == "/api/queries":
            doms = [("gateway.icloud.com", "FORWARDED"), ("googleads.g.doubleclick.net", "GRAVITY"),
                    ("www.google.com", "CACHE"), ("app-measurement.com", "GRAVITY"), ("api.spotify.com", "FORWARDED"),
                    ("graph.facebook.com", "DENYLIST"), ("moode.local", "CACHE")]
            qs = [{"time": NOW - 4 * i, "type": "A", "domain": d, "status": s,
                   "client": {"ip": "10.0.0.12", "name": "tims-iphone.lan"}} for i, (d, s) in
                  enumerate(doms * 3)]
            return 200, {"queries": qs[:int(q.get("length", 100))]}
        return 404, {"error": {"key": "not_found"}}


def password_file(text="app-pass\n"):
    path = os.path.join(tempfile.mkdtemp(), "pihole-password")
    if text is not None:
        with open(path, "w") as f:
            f.write(text)
    return path


class TestSums(unittest.TestCase):
    def test_kinds(self):
        self.assertEqual(pd.kind("GRAVITY"), "blocked")
        self.assertEqual(pd.kind("REGEX_CNAME"), "blocked")
        self.assertEqual(pd.kind("CACHE_STALE"), "cached")
        self.assertEqual(pd.kind("FORWARDED"), "allowed")
        self.assertEqual(pd.kind(None), "allowed")

    def test_client_names(self):
        self.assertEqual(pd.client_name({"ip": "10.0.0.12", "name": "tims-iphone.lan"}), "tims-iphone")
        self.assertEqual(pd.client_name({"ip": "10.0.0.41", "name": ""}), "10.0.0.41")
        self.assertEqual(pd.client_name({"ip": "10.0.0.41", "name": "10.0.0.41"}), "10.0.0.41")
        self.assertEqual(pd.client_name(None), "?")

    def test_via_router(self):
        self.assertEqual(pd.via_router([{"ip": "10.0.0.1", "name": "", "count": 47966},
                                        {"ip": "127.0.0.1", "name": "localhost", "count": 83}]), "10.0.0.1")
        self.assertIsNone(pd.via_router([{"ip": "10.0.0.9", "count": 60}, {"ip": "10.0.0.12", "count": 40}]))
        self.assertIsNone(pd.via_router([]))
        self.assertIsNone(pd.via_router(None))

    def test_blocking_state_and_timer(self):
        self.assertEqual(pd.blocking_state({"blocking": "enabled", "timer": None}, 100), {"state": "enabled", "until": None})
        self.assertEqual(pd.blocking_state({"blocking": "disabled", "timer": 287.5}, 100)["until"], 387.5)

    def test_history_bars(self):
        bars = pd.history_bars(list(reversed(history())))
        self.assertEqual(len(bars), 144)
        self.assertLess(bars[0][0], bars[-1][0], "oldest first")
        t, allowed, blocked = bars[0]
        self.assertEqual((allowed, blocked), (200 - 33, 33))

    def test_counts_and_ages(self):
        self.assertEqual(pd.short_count(1234567), "1.2M")
        self.assertEqual(pd.short_count(45678), "45.7k")
        self.assertEqual(pd.short_count(950), "950")
        self.assertEqual(pd.ago(3 * 86400 + 5), "3 d")
        self.assertEqual(pd.ago(125), "2 min")
        self.assertEqual(pd.ago(-5), "now")


class TestApi(unittest.TestCase):
    def test_logs_in_once_and_sends_the_session(self):
        fake = FakePihole()
        api = pd.Api("http://pi.hole/", password_file=password_file(), send=fake)
        api.get("/stats/summary")
        api.get("/dns/blocking")
        logins = [c for c in fake.calls if c[1].endswith("/api/auth")]
        self.assertEqual(len(logins), 1)
        self.assertEqual(fake.calls[-1][2], "sid1")
        self.assertEqual(logins[0][3], {"password": "app-pass"})

    def test_logs_in_again_when_the_session_lapses(self):
        fake = FakePihole()
        api = pd.Api("http://pi.hole", password_file=password_file(), send=fake)
        api.get("/stats/summary")
        fake.expire_sessions()
        self.assertEqual(api.get("/stats/summary")["clients"]["active"], 14)
        self.assertEqual(api.sid, "sid2")

    def test_password_problems_are_explained(self):
        with self.assertRaisesRegex(pd.ApiError, "password needed"):
            pd.Api("http://pi.hole", password_file=password_file(None), send=FakePihole()).get("/stats/summary")
        with self.assertRaisesRegex(pd.ApiError, "wrong password"):
            pd.Api("http://pi.hole", password_file=password_file("nope"), send=FakePihole()).get("/stats/summary")
        with self.assertRaisesRegex(pd.ApiError, "offline"):
            pd.Api("http://pi.hole", password_file=password_file(), send=FakePihole(down=True)).get("/stats/summary")

    def test_too_many_sessions(self):
        def full(method, url, headers, body=None, timeout=8):
            return 429, {"error": {"key": "api_seats_exceeded", "message": "API seats exceeded"}}
        with self.assertRaisesRegex(pd.ApiError, "too many"):
            pd.Api("http://pi.hole", password_file=password_file(), send=full).login()

    def test_logout_frees_the_session(self):
        fake = FakePihole()
        api = pd.Api("http://pi.hole", password_file=password_file(), send=fake)
        api.get("/stats/summary")
        api.logout()
        self.assertEqual(fake.sids, set())
        self.assertEqual(fake.calls[-1][0], "DELETE")
        api.logout()                                  # twice is harmless


class TestMonitor(unittest.TestCase):
    def setUp(self):
        self.t = NOW
        self.fake = FakePihole()
        self.mon = pd.Monitor(pd.Api("http://pi.hole", password_file=password_file(), send=self.fake),
                              clock=lambda: self.t)

    def test_polls_each_thing_on_its_own_schedule(self):
        self.assertEqual(self.mon.refresh(), ["summary", "blocking", "history", "top"])
        self.assertEqual(self.mon.refresh(), [], "nothing due yet")
        self.t += 11
        self.assertEqual(self.mon.refresh(), ["summary", "blocking"])
        self.t += 60
        self.assertEqual(self.mon.refresh(), ["summary", "blocking", "top"])
        self.assertEqual(len(self.mon.data("top")["blocked"]), pd.TOP_ROWS)

    def test_query_log_only_while_live(self):
        self.mon.refresh()
        self.mon.live = True
        self.assertEqual(self.mon.refresh(), ["queries"])
        self.assertEqual(len(self.mon.data("queries")), pd.LIVE_ROWS)

    def test_version_moves_only_when_data_changes(self):
        self.mon.refresh()
        v = self.mon.version
        self.t += 11
        self.mon.refresh()
        self.assertEqual(self.mon.version, v, "same answers - no redraw needed")
        self.mon.pause(300)
        self.mon.refresh()
        self.assertGreater(self.mon.version, v)

    def test_pause_and_resume(self):
        self.mon.refresh()
        self.mon.pause(300)
        self.mon.refresh()
        posts = [c for c in self.fake.calls if c[0] == "POST" and c[1].endswith("/dns/blocking")]
        self.assertEqual(posts[-1][3], {"blocking": False, "timer": 300})
        self.assertEqual(self.mon.data("blocking"), {"state": "disabled", "until": NOW + 300})
        self.mon.resume()
        self.mon.refresh()
        posts = [c for c in self.fake.calls if c[0] == "POST" and c[1].endswith("/dns/blocking")]
        self.assertEqual(posts[-1][3], {"blocking": True, "timer": None})
        self.assertEqual(self.mon.data("blocking")["state"], "enabled")

    def test_offline_backs_off_and_recovers(self):
        self.fake.down = True
        self.mon.refresh()
        self.assertEqual(self.mon.error, "offline")
        self.fake.down = False
        self.t += 5
        self.assertEqual(self.mon.refresh(), [], "waits before retrying")
        self.t += pd.RETRY_AFTER
        self.assertIn("summary", self.mon.refresh())
        self.assertIsNone(self.mon.error)

    def test_stop_logs_out(self):
        self.mon.refresh()
        self.mon.stop()
        self.assertEqual(self.fake.sids, set())


class TestConfig(unittest.TestCase):
    def test_pihole_json(self):
        cfg = pd.load_config()
        self.assertTrue(cfg["url"].startswith("http"))
        self.assertTrue(all(isinstance(s, int) and s > 0 for s in cfg["pause_choices"]))


try:
    os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
    import pygame  # noqa: F401
    import pihole_display
except ImportError:
    pihole_display = None


@unittest.skipUnless(pihole_display, "pygame not installed")
class TestScreens(unittest.TestCase):
    """Draw every page (with and without data, paused or not) and poke the controls."""

    def setUp(self):
        import pygame
        self.fake = FakePihole()
        self.mon = pd.Monitor(pd.Api("http://pi.hole", password_file=password_file(), send=self.fake))
        self.app = pihole_display.PiholeDisplay(pd.load_config(), self.mon, screen=pygame.Surface((480, 800)))
        self.now = datetime.fromtimestamp(NOW)

    def fill(self):
        self.mon.live = True
        self.mon.refresh()

    def test_every_page_draws(self):
        for step in ("empty", "filled", "paused", "choosing", "no password"):
            if step == "filled":
                self.fill()
            elif step == "paused":
                self.mon.pause(300)
                self.mon.refresh()
            elif step == "choosing":
                self.app.choosing = True
            elif step == "no password":
                self.mon.error = "password needed"
                self.mon.values.clear()
            for page in pihole_display.PAGES:
                self.app.page = page
                for lst, _ in pihole_display.LISTS:
                    self.app.top_list = lst
                    self.app.draw(self.now)

    def tap_button(self, name):
        self.app.draw(self.now)
        r = self.app.buttons[name]
        self.app.touch_down(r.center)
        self.app.touch_up(r.center)

    def test_pause_choose_then_resume(self):
        self.fill()
        self.tap_button("pause")
        self.assertTrue(self.app.choosing)
        self.app.draw(self.now)
        self.assertNotIn("page:Top", self.app.buttons, "the page underneath can't be tapped")
        self.tap_button("pause:300")
        self.assertFalse(self.app.choosing)
        self.mon.refresh()                          # the poller runs the queued pause
        self.assertEqual(self.fake.blocking, "disabled")
        self.assertEqual(self.app.blocking()[0], "paused")
        self.assertAlmostEqual(self.app.blocking()[1], 300, delta=5)
        self.tap_button("resume")
        self.mon.refresh()
        self.assertEqual((self.fake.blocking, self.app.blocking()[0]), ("enabled", "on"))

    def test_cancel_and_tapping_outside_close_the_choices(self):
        self.fill()
        self.tap_button("pause")
        self.tap_button("cancel")
        self.assertFalse(self.app.choosing)
        self.tap_button("pause")
        self.app.touch_down((240, 760))
        self.app.touch_up((240, 760))
        self.assertFalse(self.app.choosing)
        self.assertEqual(self.app.page, "Now", "the tap only closed the choices")
        self.assertEqual(self.fake.blocking, "enabled")

    def test_top_lists_and_live_polling(self):
        self.fill()
        self.tap_button("page:Top")
        self.tap_button("list:clients")
        self.assertEqual(self.app.top_list, "clients")
        self.app.tick()
        self.assertFalse(self.mon.live)
        self.tap_button("page:Live")
        self.app.tick()
        self.assertTrue(self.mon.live)

    def test_behind_a_router(self):
        """All lookups from the router: count domains, not devices, and say why on Devices."""
        self.fill()
        self.mon.values["top"]["clients"] = [{"ip": "10.0.0.1", "name": "", "count": 47966},
                                             {"ip": "127.0.0.1", "name": "localhost", "count": 83}]
        self.assertEqual(self.app.router(), "10.0.0.1")
        self.app.page, self.app.top_list = "Top", "clients"
        self.app.draw(self.now)
        self.app.page = "Now"
        self.app.draw(self.now)

    def test_redraws_each_second_only_while_paused(self):
        self.fill()
        self.assertTrue(self.app.frame(self.now))
        self.assertFalse(self.app.frame(self.now), "nothing changed")
        self.mon.pause(300)
        self.mon.refresh()
        self.assertTrue(self.app.frame(self.now))
        self.app.drawn = self.app.state(self.now)
        orig = time.time
        try:
            time.time = lambda: orig() + 1.2         # the countdown moves on
            self.assertTrue(self.app.frame(self.now))
        finally:
            time.time = orig


if __name__ == "__main__":
    unittest.main()
