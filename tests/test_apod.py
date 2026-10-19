"""Off-Pi tests for the Space Picture app's data side (and its screens, when pygame is installed).

Run on a Mac/PC:  python3 -m unittest discover tests
No network: the science.nasa.gov APOD feed and its image server are faked.
"""
import os
import sys
import tempfile
import unittest
import urllib.error
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "apps", "apod"))
import apod_data as ad  # noqa: E402

IMG = "https://assets.science.nasa.gov/dynamicimage/assets/science/cds/apod/apod/2026/october/{}?w=3048&amp;h=1708&amp;fit=clip"  # as XML, escaped
ITEM = """<item><title>{title}</title>
<link>https://science.nasa.gov/image-article/apod-2026-october-{day}-{slug}/</link>
<pubDate>{pub} 04:05:00 +0000</pubDate>
{hd}
<apod:credit><![CDATA[<em>Image Credit:</em><strong> </strong><a href="x">NASA</a> , <a href="y">ESA</a>; <em>Sonification:</em> CXC]]></apod:credit>
<apod:alt>{title}, described.</apod:alt>
<apod:explanation><![CDATA[<strong>Explanation:</strong> The <a href="z">{title}</a> is <em>lovely</em>.
See <a href="https://www.youtube.com/watch?v=notthisone">this</a>. <p>More.</p> Tomorrow's picture: what could it be?]]></apod:explanation>
<content:encoded><![CDATA[<html><p>2026 October {day}<br>{media}</p><iframe name="gform_ajax_frame_45"></iframe></html>]]></content:encoded>
</item>"""


def feed(days=(18, 17, 16, 15)):
    media = {18: '<iframe width="960" src="//www.youtube.com/embed/DzrCEm1ZBRY?rel=0"></iframe>',
             16: '<video controls><source src="https://assets.science.nasa.gov/x.mp4"></video>'}
    items = []
    for day in days:
        d = datetime(2026, 10, day)
        hd = "" if day == 16 else f"<apod:hdurl>{IMG.format(f'pic{day}.jpg')}</apod:hdurl>"
        items.append(ITEM.format(title=f"Picture {day}", day=day, slug=f"picture-{day}", hd=hd,
                                 media=media.get(day, '<img src="a.jpg">'), pub=f"{d:%a}, {day} {d:%b} 2026"))
    return ('<?xml version="1.0"?><rss xmlns:apod="https://science.nasa.gov/apod/" '
            'xmlns:content="http://purl.org/rss/1.0/modules/content/"><channel>' + "".join(items) +
            "</channel></rss>").encode()


def png_bytes(w=64, h=40):
    import struct
    import zlib
    raw = b"".join(b"\x00" + bytes([30, 60, 120]) * w for _ in range(h))

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) +
            chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


class FakeNasa:
    def __init__(self):
        self.calls, self.feed, self.fail, self.broken = [], feed(), None, set()

    def __call__(self, url, timeout=30):
        self.calls.append(url)
        if self.fail:
            raise self.fail
        if url == ad.FEED:
            return self.feed
        if any(b in url for b in self.broken):
            return b"<html>error</html>"
        return png_bytes()


class TestParsing(unittest.TestCase):
    def test_an_item(self):
        d = ad.parse_feed(feed())[1]
        self.assertEqual((d["date"], d["title"], d["video"]), ("2026-10-17", "Picture 17", False))
        self.assertEqual(d["explanation"], "The Picture 17 is lovely. See this.\nMore.",
                         "markup, the label and the trailer gone")
        self.assertEqual(d["picture"], IMG.format("pic17.jpg").split("?")[0] + "?" + ad.PICTURE, "resized")
        self.assertEqual(ad.credit_line(d["credit"]), "NASA, ESA")

    def test_videos(self):
        days = {d["date"]: d for d in ad.parse_feed(feed())}
        self.assertTrue(days["2026-10-18"]["video"], "a YouTube player, with a still")
        self.assertTrue(days["2026-10-18"]["picture"])
        self.assertTrue(days["2026-10-16"]["video"])
        self.assertIsNone(days["2026-10-16"]["picture"], "an mp4 and no still")
        self.assertFalse(days["2026-10-15"]["video"], "a link to YouTube in the text isn't a video")

    def test_youtube_thumbnail_when_no_still(self):
        f = feed((18,)).replace(b"<apod:hdurl>", b"<apod:x>").replace(b"</apod:hdurl>", b"</apod:x>")
        self.assertEqual(ad.parse_feed(f)[0]["picture"], "https://img.youtube.com/vi/DzrCEm1ZBRY/hqdefault.jpg")

    def test_dates(self):
        self.assertEqual(ad.item_date("https://x/apod-2026-september-9-a-b/", ""), "2026-09-09")
        self.assertEqual(ad.item_date("https://x/other/", "Fri, 09 Oct 2026 04:05:00 +0000"), "2026-10-09")
        self.assertEqual(ad.resized("https://example.com/a.jpg?x=1", ad.THUMB), "https://example.com/a.jpg?x=1")


class TestStore(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.t = 1_800_000_000.0
        self.net = FakeNasa()

    def store(self):
        return ad.Store(cache_dir=self.dir, get=self.net, clock=lambda: self.t)

    def run_out(self, s):
        for _ in range(20):
            if not s.refresh():
                return

    def test_feed_then_pictures_newest_first_then_thumbs(self):
        s = self.store()
        s.refresh()
        self.assertEqual([d["date"] for d in s.days()], ["2026-10-18", "2026-10-17", "2026-10-16", "2026-10-15"])
        s.refresh()
        self.assertTrue(s.picture("2026-10-18"))
        self.assertIsNone(s.thumb("2026-10-18"), "pictures first")
        self.run_out(s)
        self.assertEqual([bool(s.picture(d)) for d in ("2026-10-17", "2026-10-16", "2026-10-15")], [True, False, True])
        self.assertTrue(s.thumb("2026-10-15"))
        self.assertFalse(s.refresh(), "nothing left to do")

    def test_kept_and_old_days_go(self):
        s = self.store()
        self.run_out(s)
        self.net.feed = feed((19,))
        self.t += ad.FETCH_EVERY
        self.run_out(s)
        self.assertEqual(len(self.store().days()), 5, "the days build up on disk")
        s.archive.update({f"2026-08-{d:02d}": {"date": f"2026-08-{d:02d}", "picture": None, "thumb": None}
                          for d in range(1, 31)})
        s.archive.update({f"2026-09-{d:02d}": {"date": f"2026-09-{d:02d}", "picture": None, "thumb": None}
                          for d in range(1, 31)})
        self.t += ad.FETCH_EVERY
        s.refresh()
        self.assertEqual(len(s.days()), ad.KEEP_DAYS)
        self.assertTrue(s.picture("2026-10-19"))

    def test_an_error_page_isnt_kept_and_is_tried_later(self):
        self.net.broken = {"pic17"}
        s = self.store()
        self.run_out(s)
        self.assertIsNone(s.picture("2026-10-17"))
        self.net.broken = set()
        self.assertFalse(s.refresh(), "not straight away")
        self.t += ad.RETRY_AFTER
        self.run_out(s)
        self.assertTrue(s.picture("2026-10-17"))

    def test_offline(self):
        s = self.store()
        self.run_out(s)
        self.net.fail = urllib.error.URLError("down")
        self.t += ad.FETCH_EVERY
        s.refresh()
        self.assertEqual(s.error(), "offline")
        self.assertEqual(len(s.days()), 4)


try:
    os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
    import pygame  # noqa: F401
    import apod_display
except ImportError:
    apod_display = None


@unittest.skipUnless(apod_display, "pygame not installed")
class TestScreens(unittest.TestCase):
    def setUp(self):
        import pygame
        pygame.display.init()
        pygame.display.set_mode((1, 1))
        self.t = 1_800_000_000.0
        self.store = ad.Store(cache_dir=tempfile.mkdtemp(), get=FakeNasa(), clock=lambda: self.t)
        self.app = apod_display.ApodDisplay(self.store, screen=pygame.Surface((480, 800)))
        self.now = datetime(2026, 10, 18, 9, 0)

    def fill_store(self):
        for _ in range(20):
            if not self.store.refresh():
                return

    def test_every_page_and_day_draws(self):
        for filled in (False, True):
            if filled:
                self.fill_store()
            for page in apod_display.PAGES:
                self.app.page = page
                for day in [None] + [d["date"] for d in self.store.days()]:
                    self.app.date = day
                    for fill in (False, True):
                        self.app.fill = fill
                        self.app.draw(self.now)

    def test_tap_fills_swipe_changes_day_list_opens_one(self):
        self.fill_store()
        self.app.draw(self.now)
        self.app.tap((240, 380))
        self.assertTrue(self.app.fill)
        self.app.touch_down((400, 400))
        self.app.touch_up((100, 410))
        self.assertEqual(self.app.current()["date"], "2026-10-17")
        self.app.page = "Days"
        self.app.draw(self.now)
        self.app.tap(self.app.buttons["day:2026-10-15"].center)
        self.assertEqual((self.app.page, self.app.current()["date"]), ("Picture", "2026-10-15"))

    def test_back_to_today_once_left_alone(self):
        self.fill_store()
        self.app.date, self.app.fill = "2026-10-15", True
        self.app.last_touch -= apod_display.DIM_AFTER + 1
        self.app.frame(self.now)
        self.assertEqual((self.app.date, self.app.fill), (None, False))

    def test_redraws_only_when_something_changes(self):
        self.assertTrue(self.app.frame(self.now))
        self.assertFalse(self.app.frame(self.now))
        self.store.refresh()
        self.assertTrue(self.app.frame(self.now))
        self.store.refresh()                             # a picture arrives
        self.assertTrue(self.app.frame(self.now))
        self.assertFalse(self.app.frame(self.now))


if __name__ == "__main__":
    unittest.main()
