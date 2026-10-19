#!/usr/bin/env python3
"""NASA's Astronomy Picture of the Day for the Space Picture app: fetching and keeping (no pygame here).

APOD moved to science.nasa.gov in 2026, and api.nasa.gov's APOD API has answered with the
same placeholder for every day since. The new site's RSS feed (free, no key) is used
instead: the last 30 days, each with title, explanation, credit, a description of the
picture, and the picture on NASA's image server - which resizes on request, so the Pi
fetches a screen-sized copy (~50 KB) and a thumbnail rather than the original (often
several MB). A day that's a video has a still from it, or a YouTube thumbnail, or none.

Everything is kept on disk (KEEP_DAYS), so it works offline and old days stay.

On the Pi, check the feed with:
    python3 /opt/pi3-touch/apps/apod/apod_data.py --check
"""
import gzip
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime
from html.parser import HTMLParser

CACHE_DIR = os.environ.get("STATE_DIRECTORY", "/var/lib/pi3-touch-apod")
FEED = "https://science.nasa.gov/feed/apod-basic/"
USER_AGENT = "pi3-touch-apod/1.0 (+https://github.com/typedformiles/pi3-touch)"
NS = "{https://science.nasa.gov/apod/}"
CONTENT = "{http://purl.org/rss/1.0/modules/content/}encoded"
FETCH_EVERY = 3 * 3600          # a new picture once a day (around 05:00 UK time)
RETRY_AFTER = 10 * 60
KEEP_DAYS = 45
PICTURE = "w=960&h=960&fit=clip&fm=jpg&q=82"        # fits the screen either way up
THUMB = "w=120&h=120&fit=crop&fm=jpg&q=70"
MONTHS = {m: i for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july",
                                      "august", "september", "october", "november", "december"], 1)}


def log(*a):
    print("[apod]", *a, flush=True)


# ── Parsing the feed ──────────────────────────────────────────────────────────

class Text(HTMLParser):
    """HTML -> plain text (links and emphasis dropped, paragraphs as blank lines)."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out = []

    def handle_starttag(self, tag, attrs):
        if tag in ("p", "br"):
            self.out.append("\0")              # a break; newlines in the source are just spaces

    def handle_data(self, data):
        self.out.append(data)

    def text(self):
        s = re.sub(r"\s+", " ", "".join(self.out))
        return re.sub(r"\s*\0[\s\0]*", "\n", s).strip()


def plain(html_text):
    p = Text()
    p.feed(html_text or "")
    p.close()
    return p.text()


def resized(url, query):
    """NASA's dynamic image URL with our own size (other image URLs as they are)."""
    if "/dynamicimage/" not in url:
        return url
    return url.split("?")[0] + "?" + query


def item_date(link, pub):
    """The day from the link ("…/apod-2026-october-9-stickney-crater/"), else from pubDate."""
    m = re.search(r"apod-(\d{4})-([a-z]+)-(\d{1,2})-", link or "")
    if m and m.group(2) in MONTHS:
        return f"{m.group(1)}-{MONTHS[m.group(2)]:02d}-{int(m.group(3)):02d}"
    return datetime.strptime(pub[5:16], "%d %b %Y").strftime("%Y-%m-%d")


def parse_item(item):
    """One feed item -> {"date", "title", "explanation", "credit", "alt", "picture" (URL
    or None), "thumb", "video" (bool), "link"}."""
    content = item.findtext(CONTENT) or ""
    hd = (item.findtext(NS + "hdurl") or "").strip()
    # Only an embedded player makes it a video: explanations link to YouTube too
    yt = re.search(r"<iframe[^>]+youtube(?:-nocookie)?\.com/embed/([\w-]{6,})", content)
    video = bool(yt or re.search(r"<video\b|<iframe[^>]+player\.vimeo\.com", content))
    picture = thumb = None
    if hd:
        picture, thumb = resized(hd, PICTURE), resized(hd, THUMB)
    elif yt:
        picture = thumb = f"https://img.youtube.com/vi/{yt.group(1)}/hqdefault.jpg"
    explanation = plain(item.findtext(NS + "explanation") or item.findtext("description"))
    explanation = re.sub(r"^Explanation:\s*", "", explanation)
    explanation = re.split(r"\s*(Tomorrow's picture:|Jump Around the Universe:|Explore Your Universe)",
                           explanation)[0].strip()
    credit = plain(item.findtext(NS + "credit") or item.findtext(NS + "copyright"))
    credit = re.sub(r"\s+([,;)])", r"\1", re.sub(r"\s+", " ", credit)).strip()
    return {"date": item_date(item.findtext("link"), item.findtext("pubDate") or ""),
            "title": (item.findtext("title") or "?").strip(), "explanation": explanation, "credit": credit,
            "alt": (item.findtext(NS + "alt") or "").strip(), "picture": picture, "thumb": thumb,
            "video": video, "link": item.findtext("link") or ""}


def parse_feed(xml_bytes):
    """The RSS feed -> its days, newest first (items that won't parse are skipped)."""
    out = {}
    for item in ET.fromstring(xml_bytes).iter("item"):
        try:
            day = parse_item(item)
            out.setdefault(day["date"], day)
        except (ValueError, TypeError) as e:
            log(f"skipping {item.findtext('title')!r}: {e}")
    return [out[d] for d in sorted(out, reverse=True)]


def credit_line(credit):
    """"Image Credit: NASA, ESA & … ; Sonification: …" -> "NASA, ESA & …" for a caption."""
    c = re.sub(r"^(Image|Video|Illustration)?\s*Credits?( & Copyright| and Copyright)?\s*:\s*", "", credit,
               flags=re.I)
    return c.split(";")[0].strip()


# ── Fetching and keeping ──────────────────────────────────────────────────────

def http_get(url, timeout=30):
    url = urllib.parse.quote(url, safe=":/?&=%#+,;@!$'()*~")     # some file names aren't ASCII
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept-Encoding": "gzip"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = r.read()
        if r.headers.get("Content-Encoding") == "gzip":
            body = gzip.decompress(body)
    return body


def explain(err):
    if isinstance(err, urllib.error.HTTPError):
        return f"HTTP {err.code}"
    if isinstance(err, urllib.error.URLError):
        return "offline"
    return str(err)[:60] or type(err).__name__


class Store:
    """The days (newest first) and their pictures, kept on disk.

    days() is the list; picture(date) / thumb(date) are paths to the files once fetched
    (None before). The feed is checked every few hours; pictures come one per pass, the
    newest first, then thumbnails.
    """

    def __init__(self, cache_dir=CACHE_DIR, get=http_get, clock=time.time):
        self.cache_dir, self.get, self.clock = cache_dir, get, clock
        self.lock = threading.Lock()
        self.err, self.retry_at = None, 0
        self.failed = {}                        # url -> when it last failed
        self.downloads = 0                      # pictures fetched since starting (for redraws)
        saved = self._load()
        self.archive = saved.get("days", {})
        self.fetched = saved.get("fetched")

    def days(self):
        with self.lock:
            return [self.archive[d] for d in sorted(self.archive, reverse=True)]

    def path(self, date, kind):
        return os.path.join(self.cache_dir, "pictures", f"{date}-{kind}")   # JPEG or PNG

    def picture(self, date):
        p = self.path(date, "picture")
        return p if os.path.exists(p) else None

    def thumb(self, date):
        p = self.path(date, "thumb")
        return p if os.path.exists(p) else None

    def error(self):
        with self.lock:
            return self.err

    def stamp(self):
        with self.lock:
            return (self.fetched, self.err, len(self.archive), self.downloads)

    def refresh(self):
        """One pass: the feed if due, else one picture. True if it fetched anything."""
        now = self.clock()
        if now < self.retry_at:
            return False
        if not self.fetched or now - self.fetched >= FETCH_EVERY:
            try:
                days = parse_feed(self.get(FEED))
                if not days:
                    raise ValueError("no days in the feed")
            except Exception as e:              # network, HTTP, a feed that won't parse
                with self.lock:
                    self.err = explain(e)
                self.retry_at = now + RETRY_AFTER
                log(f"feed: {explain(e)} ({e})")
                return True
            with self.lock:
                for d in days:
                    self.archive[d["date"]] = d
                gone = sorted(self.archive)[:-KEEP_DAYS]
                for d in gone:
                    del self.archive[d]
                self.fetched, self.err = now, None
            for d in gone:
                for kind in ("picture", "thumb"):
                    try:
                        os.remove(self.path(d, kind))
                    except OSError:
                        pass
            self._save()
            return True
        return self._next_picture(now)

    def _next_picture(self, now):
        """Fetch the newest missing picture, then the newest missing thumbnail."""
        days = self.days()
        todo = [(d, "picture", d["picture"]) for d in days if d["picture"] and not self.picture(d["date"])] + \
               [(d, "thumb", d["thumb"]) for d in days if d["thumb"] and not self.thumb(d["date"])]
        todo = [t for t in todo if now - self.failed.get(t[2], -RETRY_AFTER) >= RETRY_AFTER]
        if not todo:
            return False
        d, kind, url = todo[0]
        try:
            data = self.get(url)
            if not data.startswith((b"\xff\xd8", b"\x89PNG")):   # JPEG (PNGs stay PNG); else an error page
                raise ValueError("not a picture")
            os.makedirs(os.path.dirname(self.path(d["date"], kind)), exist_ok=True)
            with open(self.path(d["date"], kind) + ".tmp", "wb") as f:
                f.write(data)
            os.replace(self.path(d["date"], kind) + ".tmp", self.path(d["date"], kind))
            with self.lock:
                self.downloads += 1
        except Exception as e:
            self.failed[url] = now
            log(f"{kind} {d['date']}: {explain(e)}")
        return True

    def _load(self):
        try:
            with open(os.path.join(self.cache_dir, "days.json")) as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def _save(self):
        path = os.path.join(self.cache_dir, "days.json")
        with self.lock:
            data = {"days": self.archive, "fetched": self.fetched}
        try:
            os.makedirs(self.cache_dir, exist_ok=True)
            with open(path + ".tmp", "w") as f:
                json.dump(data, f)
            os.replace(path + ".tmp", path)
        except OSError as e:
            log("can't save:", e)

    def run(self, every=5):
        while True:
            try:
                self.refresh()
            except Exception as e:              # never let the fetcher die
                log("refresh failed:", e)
            time.sleep(every)

    def start(self):
        threading.Thread(target=self.run, daemon=True).start()
        return self


def check():
    """Fetch and parse the live feed, and today's picture (run on the Pi or a Mac)."""
    import tempfile
    store = Store(cache_dir=tempfile.mkdtemp())
    store.refresh()
    if store.error():
        print("FAILED -", store.error())
        return 1
    days = store.days()
    d = days[0]
    store.refresh()
    pic = store.picture(d["date"])
    print(f"{len(days)} days in the feed, newest {d['date']}: {d['title']}"
          f"{' (video)' if d['video'] else ''}")
    print(f"  credit: {credit_line(d['credit'])}")
    print(f"  {d['explanation'][:160]}…")
    print(f"  picture: {os.path.getsize(pic) // 1024} KB" if pic else "  picture: none")
    print(f"  without a picture: {', '.join(x['date'] for x in days if not x['picture']) or 'none'}")
    return 0


if __name__ == "__main__":
    if sys.argv[1:] == ["--check"]:
        sys.exit(check())
    print(__doc__)
