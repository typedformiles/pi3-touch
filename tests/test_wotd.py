"""Off-Pi tests for the Word of the Day app's data side (and its screens, when pygame is installed).

Run on a Mac/PC:  python3 -m unittest discover tests
No network: the Merriam-Webster feed and the Free Dictionary API are faked.
"""
import json
import os
import sys
import tempfile
import unittest
import urllib.error
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "apps", "wotd"))
import wotd_data as wd  # noqa: E402

ITEM = """<item><title>{word}</title><link>https://example.com/{word}</link>
<description><![CDATA[<font size="-1"><p><strong><font color="#000066">Merriam-Webster's Word of the Day
for {long} is:</font></strong></p>
<p><strong>{word}</strong> &#149; \\{say}\\&nbsp; &#149; <em>{pos}</em><br />
<p><em>{word}</em> means something {word}-ish.</p>
<p>// Everyone was rather <em>{word}</em> that day. </p>
{second}
<p><a href="https://example.com">See the entry ></a></p></p>
<p><strong>Examples:</strong><br /><p>“A <em>{word}</em> quote.” — A Writer, <em>The Paper</em>, 1 Oct. 2026</p></p>
<p><strong>Did you know?</strong><br /><p>It comes from the Latin <em>{word}us</em>, “thing”.</p><br /></p>
</font>]]></description>
<pubDate>{pub} 01:00:01 -0400</pubDate>
<mw:shortdef>{word}, briefly</mw:shortdef></item>"""


def feed(days=((18, "zenith", "ZEE-nith", "noun"), (17, "abscond", "ab-SKAHND", "verb"))):
    items = []
    for day, word, say, pos in days:
        d = datetime(2026, 10, day)
        second = "<p>It can also mean more.</p><p>// A second example.</p>" if word == "abscond" else ""
        items.append(ITEM.format(word=word, say=say, pos=pos, second=second, long=f"{d:%B} {d.day}, 2026",
                                 pub=f"{d:%a}, {d.day} {d:%b} 2026"))
    return ('<?xml version="1.0"?><rss xmlns:mw="https://www.merriam-webster.com/word-of-the-day">'
            "<channel>" + "".join(items) + "</channel></rss>").encode()


class FakeNet:
    def __init__(self):
        self.calls, self.feed, self.fail, self.broken = [], feed(), None, set()

    def __call__(self, url, timeout=15):
        self.calls.append(url)
        if self.fail:
            raise self.fail
        if url == wd.FEED:
            return self.feed
        word = url[len(wd.DICTIONARY):]
        if word in self.broken:
            raise urllib.error.HTTPError(url, 502, "bad gateway", {}, None)
        if word == "zenith":
            return json.dumps([{"meanings": [{"synonyms": ["peak", "summit"], "definitions": [
                {"synonyms": ["peak", "acme", "zenith"]}]}]}]).encode()
        raise urllib.error.HTTPError(url, 404, "no definitions", {}, None)


class TestParsing(unittest.TestCase):
    def test_an_item(self):
        w = wd.parse_feed(feed())[0]
        self.assertEqual((w["date"], w["word"], w["say"], w["pos"], w["short"]),
                         ("2026-10-18", "zenith", "ZEE-nith", "noun", "zenith, briefly"))
        self.assertEqual(w["meaning"][0]["runs"], [("zenith", True), (" means something zenith-ish.", False)])
        ex = w["meaning"][1]
        self.assertTrue(ex["example"])
        self.assertEqual(wd.plain(ex["runs"]), "Everyone was rather zenith that day.", "// and spaces gone")
        self.assertEqual(wd.plain(w["quotes"][0]), "“A zenith quote.” — A Writer, The Paper, 1 Oct. 2026")
        self.assertEqual(w["story"][0][1], ("zenithus", True), "italics kept")

    def test_two_senses_keep_their_order(self):
        w = wd.parse_feed(feed())[1]
        self.assertEqual([m["example"] for m in w["meaning"]], [False, True, False, True])
        self.assertEqual(wd.plain(w["meaning"][3]["runs"]), "A second example.")

    def test_newest_first_and_bad_items_skipped(self):
        broken = feed().replace(b"&#149;", b"")          # no bullet: can't find the head line
        self.assertEqual(wd.parse_feed(broken), [])
        self.assertEqual([w["word"] for w in wd.parse_feed(feed(((10, "a", "x", "n"), (12, "b", "y", "n"))))],
                         ["b", "a"])

    def test_similar(self):
        reply = json.loads(FakeNet()(wd.DICTIONARY + "zenith"))
        self.assertEqual(wd.similar(reply, "zenith"), ["peak", "summit", "acme"], "most cited first, not itself")
        self.assertEqual(wd.similar({"title": "No Definitions Found"}, "x"), [])


class TestStore(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.t = 1_800_000_000.0
        self.net = FakeNet()

    def store(self):
        return wd.Store(cache_dir=self.dir, get=self.net, clock=lambda: self.t)

    def test_feed_then_similar_words_then_waits(self):
        s = self.store()
        self.assertTrue(s.refresh())
        self.assertEqual([w["word"] for w in s.words()], ["zenith", "abscond"])
        s.refresh()
        s.refresh()
        self.assertEqual(s.words()[0]["similar"], ["peak", "summit", "acme"])
        self.assertEqual(s.words()[1]["similar"], [], "404: none")
        self.assertFalse(s.refresh(), "nothing to do")
        self.t += wd.FETCH_EVERY
        self.assertTrue(s.refresh())
        self.assertEqual(s.words()[0]["similar"], ["peak", "summit", "acme"], "kept across fetches")

    def test_words_build_up_on_disk(self):
        s = self.store()
        s.refresh()
        self.net.feed = feed(((19, "galore", "guh-LOR", "adjective"),))
        self.t += wd.FETCH_EVERY
        s.refresh()
        self.assertEqual([w["word"] for w in self.store().words()], ["galore", "zenith", "abscond"])

    def test_old_words_go(self):
        s = self.store()
        s.archive = {f"2026-01-{d:02d}": {"date": f"2026-01-{d:02d}", "word": "w"} for d in range(1, 31)}
        s.archive.update({f"2026-02-{d:02d}": {"date": f"2026-02-{d:02d}", "word": "w"} for d in range(1, 29)})
        s.refresh()
        self.assertEqual(len(s.words()), wd.KEEP_DAYS)
        self.assertEqual(s.words()[0]["word"], "zenith")

    def test_a_failing_lookup_doesnt_block_the_rest(self):
        self.net.broken = {"zenith"}
        s = self.store()
        s.refresh()
        s.refresh()                                       # zenith fails...
        s.refresh()                                       # ...so abscond is looked up meanwhile
        self.assertEqual(s.words()[1]["similar"], [])
        self.assertNotIn("similar", s.words()[0])
        for _ in range(wd.SIMILAR_TRIES):
            self.t += wd.SIMILAR_RETRY
            s.refresh()
        self.assertEqual(s.words()[0]["similar"], [], "given up after a few tries")

    def test_offline_keeps_words_and_backs_off(self):
        s = self.store()
        s.refresh()
        self.net.fail = urllib.error.URLError("down")
        self.t += wd.FETCH_EVERY
        s.refresh()
        self.assertEqual(s.error(), "offline")
        self.assertEqual(len(s.words()), 2)
        self.assertFalse(s.refresh(), "waits before trying again")


try:
    os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
    import pygame  # noqa: F401
    import wotd_display
except ImportError:
    wotd_display = None


@unittest.skipUnless(wotd_display, "pygame not installed")
class TestScreens(unittest.TestCase):
    def setUp(self):
        import pygame
        self.t = 1_800_000_000.0
        self.store = wd.Store(cache_dir=tempfile.mkdtemp(), get=FakeNet(), clock=lambda: self.t)
        self.app = wotd_display.WotdDisplay(self.store, screen=pygame.Surface((480, 800)))
        self.now = datetime(2026, 10, 18, 9, 0)

    def test_every_page_draws_with_and_without_words(self):
        for filled in (False, True):
            if filled:
                for _ in range(3):
                    self.store.refresh()
            for page in wotd_display.PAGES:
                self.app.page = page
                self.app.draw(self.now)

    def test_long_words_and_many_days_draw(self):
        self.store.archive = {f"2026-09-{d:02d}": dict(wd.parse_feed(feed())[1], date=f"2026-09-{d:02d}",
                                                       word="antidisestablishmentarianism and more")
                              for d in range(1, 31)}
        self.store.fetched = self.t
        for page in wotd_display.PAGES:
            self.app.page = page
            self.app.draw(self.now)
        self.app.page = "Past"
        self.app.draw(self.now)
        self.app.tap(self.app.buttons["down"].center)
        self.app.draw(self.now)
        self.assertIn("up", self.app.buttons)

    def test_days_by_swipe_arrows_and_the_list(self):
        self.store.refresh()
        self.app.draw(self.now)
        self.assertEqual(self.app.current()["word"], "zenith")
        self.app.touch_down((400, 400))
        self.app.touch_up((100, 410))                     # swipe left: the day before
        self.assertEqual(self.app.current()["word"], "abscond")
        self.app.draw(self.now)
        self.app.tap(self.app.buttons["newer"].center)
        self.assertEqual(self.app.current()["word"], "zenith")
        self.app.page = "Past"
        self.app.draw(self.now)
        self.app.tap(self.app.buttons["word:2026-10-17"].center)
        self.assertEqual((self.app.page, self.app.current()["word"]), ("Word", "abscond"))

    def test_back_to_today_once_left_alone(self):
        self.store.refresh()
        self.app.date = "2026-10-17"
        self.app.last_touch -= wotd_display.DIM_AFTER + 1
        self.app.frame(self.now)
        self.assertIsNone(self.app.date)

    def test_redraws_only_when_something_changes(self):
        self.assertTrue(self.app.frame(self.now))
        self.assertFalse(self.app.frame(self.now))
        self.store.refresh()
        self.assertTrue(self.app.frame(self.now))
        self.assertFalse(self.app.frame(self.now))


if __name__ == "__main__":
    unittest.main()
