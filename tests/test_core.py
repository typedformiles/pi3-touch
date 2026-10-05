"""Off-Pi tests for the shared library, booth touch panel and launcher.

Run on a Mac/PC:  python3 -m unittest discover tests
Uses a fake framebuffer (a plain file) and fake touch events - no hardware needed.
"""
import gzip
import importlib.util
import json
import os
import socket
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "common"))
import pitouch  # noqa: E402


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


showtouch = load("showtouch", "apps/booth-display/showtouch.py")
launcher = load("launcher", "launcher/launcher.py")


class FakeFramebuffer:
    """A file standing in for /dev/fb0 plus matching sysfs attributes."""

    def __init__(self, bpp=16, w=1920, h=1080):
        self.dir = tempfile.mkdtemp()
        self.bpp, self.w, self.h, self.stride = bpp, w, h, w * bpp // 8
        os.makedirs(self.dir + "/sys")
        for n, v in (("virtual_size", f"{w},{h}"), ("bits_per_pixel", str(bpp)), ("stride", str(self.stride))):
            with open(f"{self.dir}/sys/{n}", "w") as f:
                f.write(v)
        self.fb = self.dir + "/fb0"
        with open(self.fb, "wb") as f:
            f.write(bytes(self.stride * h))
        self.patch = mock.patch.multiple(pitouch, FB=self.fb, FBSYS=self.dir + "/sys/")
        self.patch.start()

    def pixel(self, x, y):
        with open(self.fb, "rb") as f:
            f.seek(y * self.stride + x * self.bpp // 8)
            return f.read(self.bpp // 8)

    def close(self):
        self.patch.stop()


class TestPitouch(unittest.TestCase):
    def test_convert(self):
        self.assertEqual(pitouch.convert(bytes([10, 20, 30]), 32), bytes([30, 20, 10, 255]))
        self.assertEqual(pitouch.convert(bytes([255, 0, 0]), 16), bytes([0x00, 0xF8]))
        self.assertEqual(pitouch.convert(bytes([0, 0, 255]), 16), bytes([0x1F, 0x00]))

    def test_blit_and_fill_16bpp_wide_framebuffer(self):
        fake = FakeFramebuffer(bpp=16)
        try:
            screen = pitouch.Screen()
            art = os.path.join(ROOT, "apps/booth-display/art/panel-paused.rgb.gz")
            screen.blit(art)
            rgb = gzip.open(art).read()
            x, y = 240, 400
            want = pitouch.convert(rgb[(y * 480 + x) * 3:(y * 480 + x) * 3 + 3], 16)
            self.assertEqual(fake.pixel(x, y), want)
            self.assertEqual(fake.pixel(480, 0), bytes(2), "nothing drawn right of the panel")
            screen.fill(10, 700, 5, 3, (255, 0, 0))
            self.assertEqual(fake.pixel(12, 701), bytes([0x00, 0xF8]))
            self.assertNotEqual(fake.pixel(15, 701), bytes([0x00, 0xF8]), "fill stays inside its box")
        finally:
            fake.close()

    def test_touch_taps_and_hold_time(self):
        r, w = os.pipe()
        t = pitouch.Touch.__new__(pitouch.Touch)
        t.fd, t.xr, t.yr, t._x, t._y, t._down = r, (0, 799), (0, 479), 0, 0, None

        def ev(typ, code, val):
            return pitouch.EVENT.pack(0, 0, typ, code, val)

        os.write(w, ev(3, pitouch.ABS_MT_X, 400) + ev(3, pitouch.ABS_MT_Y, 240) + ev(1, pitouch.BTN_TOUCH, 1))
        self.assertEqual(t.taps(0.1), [])
        t._down -= 2.0                                  # pretend the finger was held 2 s
        os.write(w, ev(1, pitouch.BTN_TOUCH, 0))
        (nx, ny, held), = t.taps(0.1)
        self.assertAlmostEqual(nx, 0.5, places=2)
        self.assertAlmostEqual(ny, 0.5, places=2)
        self.assertGreaterEqual(held, 2.0)
        self.assertEqual(t.taps(0.05), [], "times out cleanly with no input")


class TestBoothTouch(unittest.TestCase):
    def test_zones(self):
        self.assertEqual(showtouch.zone(0.02), "home")
        self.assertEqual(showtouch.zone(0.20), "prev")
        self.assertEqual(showtouch.zone(0.55), "toggle")
        self.assertEqual(showtouch.zone(0.95), "next")

    def test_real_taps_from_the_booth(self):
        # Raw Goodix y readings logged at G2E: top / middle / bottom of the portrait panel
        for y, want in ((91, "prev"), (261, "toggle"), (425, "next")):
            self.assertEqual(showtouch.zone(y / 479), want, y)

    def test_send_talks_to_mpv(self):
        d = tempfile.mkdtemp()
        path = d + "/mpv.sock"
        srv = socket.socket(socket.AF_UNIX)
        srv.bind(path)
        srv.listen(1)
        got = []

        def serve():
            c, _ = srv.accept()
            got.append(c.recv(4096))
            c.sendall(b'{"error":"success"}\n')
            c.close()

        th = threading.Thread(target=serve)
        th.start()
        with mock.patch.object(showtouch, "SOCK", path):
            self.assertTrue(showtouch.send("next"))
        th.join(2)
        self.assertEqual(json.loads(got[0]), {"command": ["script-message", "rotate", "next"]})

    def test_home_request_file(self):
        d = tempfile.mkdtemp()
        with mock.patch.object(showtouch, "HOME_REQUEST", d + "/request-home"):
            showtouch.request_home()
        self.assertTrue(os.path.exists(d + "/request-home"))


class Stop(Exception):
    pass


class FakeTouch:
    """Feeds scripted taps: list of (at_seconds, nx, ny). Set .stopped to end the launcher loop."""

    def __init__(self, script):
        self.script, self.t0, self.stopped = sorted(script), time.time(), False

    def taps(self, timeout):
        if self.stopped:
            raise Stop
        time.sleep(min(timeout, 0.02))
        now = time.time() - self.t0
        due = [s for s in self.script if s[0] <= now]
        self.script = [s for s in self.script if s[0] > now]
        return [(nx, ny, 0.1) for _, nx, ny in due]


class TestLauncher(unittest.TestCase):
    def setUp(self):
        self.fake = FakeFramebuffer(bpp=16)
        self.dir = tempfile.mkdtemp()
        self.cfg = launcher.load_config()
        self.cfg["countdown"] = 0.6                     # keep tests fast
        self.commands = []
        self.patches = [
            mock.patch.object(launcher, "LAST_APP", self.dir + "/last-app"),
            mock.patch.object(launcher.subprocess, "run", lambda cmd, check: self.commands.append(cmd)),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.fake.close()

    @property
    def started(self):
        return [c for c in self.commands if c[0] == "systemctl"]

    @property
    def wifi(self):
        return [c[-1] for c in self.commands if c[0] == "systemd-run"]

    def run_launcher(self, script, max_s=3):
        result = {}
        touch = FakeTouch(script)

        def go():
            try:
                launcher.Launcher(self.cfg, pitouch.Screen()).run(touch)
            except SystemExit:
                result["exited"] = True
            except Stop:
                pass

        th = threading.Thread(target=go, daemon=True)
        th.start()
        th.join(max_s)
        touch.stopped = True
        th.join(1)
        return result.get("exited", False)

    def card_centre(self, i):
        x, y, w, h = launcher.card_rects(self.cfg)[i]
        return (x + w / 2) / pitouch.PW, (y + h / 2) / pitouch.PH

    def test_cards_hit_test(self):
        for i, app in enumerate(self.cfg["apps"]):
            self.assertEqual(launcher.app_at(self.cfg, *self.card_centre(i))["id"], app["id"])
        self.assertIsNone(launcher.app_at(self.cfg, 0.5, 0.05), "title area isn't a card")

    def test_tap_starts_app_and_remembers_it(self):
        nx, ny = self.card_centre(1)
        self.assertTrue(self.run_launcher([(0.1, nx, ny)]))
        self.assertEqual(self.started, [["systemctl", "start", "--no-block", "pi3-app-moode-remote.target"]])
        with open(self.dir + "/last-app") as f:
            self.assertEqual(f.read(), "moode-remote")

    def test_countdown_starts_last_app(self):
        with open(self.dir + "/last-app", "w") as f:
            f.write("booth-display")
        self.assertTrue(self.run_launcher([]))
        self.assertEqual(self.started[0][-1], "pi3-app-booth-display.target")

    def test_tap_outside_cards_cancels_countdown(self):
        with open(self.dir + "/last-app", "w") as f:
            f.write("booth-display")
        self.assertFalse(self.run_launcher([(0.1, 0.5, 0.05)], max_s=1.5), "should stay on the menu")
        self.assertEqual(self.started, [])

    def test_no_last_app_no_countdown(self):
        self.assertFalse(self.run_launcher([], max_s=1.0))
        self.assertEqual(self.started, [])

    def test_wifi_menu_then_app_network(self):
        nx, ny = self.card_centre(0)                     # Booth Display has its own network
        self.run_launcher([(0.1, nx, ny)])
        self.assertEqual(self.wifi, ["ORBI61", "timiphone"])

    def test_wifi_app_without_network_uses_default(self):
        nx, ny = self.card_centre(1)                     # Moode Remote
        self.run_launcher([(0.1, nx, ny)])
        self.assertEqual(self.wifi, ["ORBI61", "ORBI61"])

    def test_every_app_has_art_and_systemd_target(self):
        for app in self.cfg["apps"]:
            self.assertTrue(os.path.exists(os.path.join(ROOT, "launcher/art", f"banner-{app['id']}.rgb.gz")))
            self.assertTrue(os.path.exists(os.path.join(ROOT, "pi/systemd", app["target"])))


if __name__ == "__main__":
    unittest.main()
