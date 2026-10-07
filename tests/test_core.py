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
os.environ["PI3_ROTATE"] = "0"                  # tests draw upright unless they say otherwise
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


class TestRotation(unittest.TestCase):
    """A Pi mounted upside down: "display": {"rotate": 180} turns drawing and touch over."""

    def setUp(self):
        pitouch._rotation = 180

    def tearDown(self):
        pitouch._rotation = None

    def test_reads_the_setting(self):
        pitouch._rotation = None
        with mock.patch.dict(os.environ, {"PI3_ROTATE": "180"}):
            self.assertEqual(pitouch.rotation(), 180)
        pitouch._rotation = None
        with mock.patch.dict(os.environ, {"PI3_ROTATE": "90"}):
            self.assertEqual(pitouch.rotation(), 0, "only upright or upside down")
        pitouch._rotation = None
        os.environ.pop("PI3_ROTATE")
        try:
            with open(os.path.join(ROOT, "launcher/apps.json")) as f:
                want = json.load(f).get("display", {}).get("rotate", 0)
            self.assertEqual(pitouch.rotation(), want)
        finally:
            os.environ["PI3_ROTATE"] = "0"

    def test_images_and_boxes_turned_over(self):
        for bpp in (16, 32):
            fake = FakeFramebuffer(bpp=bpp)
            try:
                screen = pitouch.Screen()
                art = os.path.join(ROOT, "apps/booth-display/art/panel-paused.rgb.gz")
                screen.blit(art)
                rgb = gzip.open(art).read()
                for x, y in ((0, 0), (240, 400), (479, 799), (10, 700)):
                    i = (y * 480 + x) * 3
                    self.assertEqual(fake.pixel(479 - x, 799 - y), pitouch.convert(rgb[i:i + 3], bpp), (bpp, x, y))
                screen.fill(10, 700, 5, 3, (255, 0, 0))      # bottom-left upright -> top-right on the panel
                red = pitouch.convert(bytes([255, 0, 0]), bpp)
                self.assertEqual(fake.pixel(480 - 10 - 5, 800 - 700 - 3), red)
                self.assertNotEqual(fake.pixel(12, 701), red)
            finally:
                fake.close()

    def test_touches_turned_over(self):
        t = pitouch.Touch.__new__(pitouch.Touch)
        t.xr, t.yr = (0, 799), (0, 479)
        nx, ny = t.norm(0, 0)                          # panel's top-left = the upright bottom-right
        self.assertAlmostEqual((nx, ny), (1.0, 1.0))
        nx, ny = t.norm(799 * 0.25, 479 * 0.1)
        self.assertAlmostEqual(nx, 0.75)
        self.assertAlmostEqual(ny, 0.9)


try:
    os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
    import pygame
    import pgscreen
except ImportError:
    pgscreen = None


@unittest.skipUnless(pgscreen, "pygame not installed")
class TestPygameRotation(unittest.TestCase):
    def tearDown(self):
        pitouch._rotation = None

    def test_upside_down_frames_and_touches(self):
        pitouch._rotation = 180
        d = pgscreen.Display(480, 800, "test")
        self.assertIsNot(d.surface, d.window, "draws off-screen, turned over on present()")
        d.window = pygame.Surface((480, 800))           # the panel's size (the test display isn't)
        d.surface.fill((0, 0, 0))
        d.surface.fill((255, 0, 0), (0, 0, 10, 10))     # upright top-left...
        d.present()
        self.assertEqual(tuple(d.window.get_at((475, 795)))[:3], (255, 0, 0))  # ...shown bottom-right
        finger = pygame.event.Event(pygame.FINGERDOWN, x=0.0, y=0.0)
        self.assertEqual(d.point(finger), (479, 799))
        click = pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=(479, 0), button=1)
        self.assertEqual(d.point(click), (0, 799))

    def test_upright_draws_straight_to_the_window(self):
        pitouch._rotation = 0
        d = pgscreen.Display(480, 800, "test")
        self.assertIs(d.surface, d.window)
        self.assertEqual(d.point(pygame.event.Event(pygame.FINGERDOWN, x=0.5, y=0.25)), (240, 200))


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
    """Feeds scripted touches: (at_seconds, nx, ny) taps, or (at_seconds, nx, ny, start_nx, start_ny)
    swipes. Set .stopped to end the launcher loop."""

    def __init__(self, script):
        self.script, self.t0, self.stopped = sorted(script), time.time(), False

    def touches(self, timeout):
        if self.stopped:
            raise Stop
        time.sleep(min(timeout, 0.02))
        now = time.time() - self.t0
        due = [s for s in self.script if s[0] <= now]
        self.script = [s for s in self.script if s[0] > now]
        return [(s[1], s[2], 0.1, *(s[3:] or s[1:3])) for s in due]

    def taps(self, timeout):
        return [t[:3] for t in self.touches(timeout)]


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
                self.launcher = launcher.Launcher(self.cfg, pitouch.Screen())
                self.launcher.run(touch)
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
        _, x, y, w, h = launcher.tile_rects(self.cfg)[i]
        return (x + w / 2) / pitouch.PW, (y + h / 2) / pitouch.PH

    def more_apps(self, n):
        """One page of real apps, then n extra ones so the menu needs more pages (which have no art)."""
        for name in ("draw_menu", "draw_banner"):
            p = mock.patch.object(launcher.Launcher, name, lambda self, *a: None)
            p.start()
            self.patches.append(p)
        del self.cfg["apps"][launcher.per_page(self.cfg):]
        for i in range(n):
            self.cfg["apps"].append({"id": f"extra-{i}", "name": f"Extra {i}", "blurb": "", "target": f"extra-{i}.target"})

    def test_tiles_hit_test(self):
        for i, app in enumerate(self.cfg["apps"]):
            page = launcher.tile_rects(self.cfg)[i][0]
            self.assertEqual(launcher.app_at(self.cfg, page, *self.card_centre(i))["id"], app["id"])
        self.assertIsNone(launcher.app_at(self.cfg, 0, 0.5, 0.05), "title area isn't a tile")
        self.assertIsNone(launcher.app_at(self.cfg, 0, 0.5, launcher.tile_rects(self.cfg)[0][2] / pitouch.PH + 0.14),
                          "gap between tiles")

    def test_tiles_fit_above_the_banner(self):
        self.more_apps(8)
        L = self.cfg["layout"]
        for _, x, y, w, h in launcher.tile_rects(self.cfg):
            self.assertTrue(x >= 0 and x + w <= pitouch.PW and y + h < L["dots_y"] < L["banner_top"])

    def test_pages_and_dots(self):
        self.more_apps(0)
        self.assertEqual(launcher.page_count(self.cfg), 1)
        self.assertEqual(launcher.dot_centres(self.cfg), [], "no dots for one page")
        self.more_apps(4)
        self.assertEqual(launcher.page_count(self.cfg), 2)
        self.assertEqual([p for p, *_ in launcher.tile_rects(self.cfg)], [0, 0, 0, 0, 1, 1, 1, 1])
        (x0, y), (x1, _) = launcher.dot_centres(self.cfg)
        self.assertEqual(launcher.dot_at(self.cfg, x1 / pitouch.PW, y / pitouch.PH), 1)
        self.assertEqual(launcher.dot_at(self.cfg, x0 / pitouch.PW, (y + 20) / pitouch.PH), 0)
        self.assertIsNone(launcher.dot_at(self.cfg, 0.5, 0.2))

    def test_swipe_detection(self):
        self.assertEqual(launcher.swipe(0.2, 0.5, 0.8, 0.5), 1, "right to left: next page")
        self.assertEqual(launcher.swipe(0.8, 0.5, 0.2, 0.52), -1, "left to right: previous page")
        self.assertEqual(launcher.swipe(0.52, 0.5, 0.5, 0.5), 0, "a tap that wobbled")
        self.assertEqual(launcher.swipe(0.3, 0.9, 0.5, 0.2), 0, "mostly vertical")

    def test_swipe_turns_page_and_taps_hit_that_page(self):
        self.more_apps(4)
        nx, ny = self.card_centre(4)                    # first tile on page 2
        self.assertTrue(self.run_launcher([(0.05, 0.1, 0.5, 0.9, 0.5), (0.2, nx, ny)]))
        self.assertEqual(self.started[-1][-1], "extra-0.target")

    def test_swipe_never_launches(self):
        nx, ny = self.card_centre(0)
        self.assertFalse(self.run_launcher([(0.05, nx - 0.4, ny, nx, ny)], max_s=1.0))
        self.assertEqual(self.started, [])

    def test_countdown_shows_the_last_apps_page(self):
        self.more_apps(4)
        with open(self.dir + "/last-app", "w") as f:
            f.write("extra-2")
        self.assertFalse(self.run_launcher([(0.05, 0.5, 0.05)], max_s=1.0), "tap cancels the countdown")
        self.assertEqual(self.launcher.page, 1)

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
        for page in range(launcher.page_count(self.cfg)):
            self.assertTrue(os.path.exists(os.path.join(ROOT, "launcher/art", f"menu-{page + 1}.rgb.gz")))
        for app in self.cfg["apps"]:
            self.assertTrue(os.path.exists(os.path.join(ROOT, "launcher/art", f"banner-{app['id']}.rgb.gz")))
            self.assertTrue(os.path.exists(os.path.join(ROOT, "pi/systemd", app["target"])))


if __name__ == "__main__":
    unittest.main()
