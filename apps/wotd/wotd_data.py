#!/usr/bin/env python3
"""Words for the Word of the Day app: fetching, parsing and keeping them (no pygame here).

The word comes from Merriam-Webster's Word of the Day RSS feed (free, no key; a new word
every day at 06:00 UK time). Each item has the word, how to say it, part of speech, its
meaning, an example sentence, a quote from the press and a "Did you know?" note on where
it comes from - with italics kept, as runs of (text, italic). The feed holds the last ten
days; every word seen is kept on disk, up to KEEP_DAYS, so older ones stay to look back
on. Similar words come from the Free Dictionary API (dictionaryapi.dev, free, no key).

On the Pi, check the feed with:
    python3 /opt/pi3-touch/apps/wotd/wotd_data.py --check
"""
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

CACHE_DIR = os.environ.get("STATE_DIRECTORY", "/var/lib/pi3-touch-wotd")
FEED = "https://www.merriam-webster.com/wotd/feed/rss2"
DICTIONARY = "https://api.dictionaryapi.dev/api/v2/entries/en/"
USER_AGENT = "pi3-touch-wotd/1.0 (+https://github.com/typedformiles/pi3-touch)"
MW = "{https://www.merriam-webster.com/word-of-the-day}"
FETCH_EVERY = 3 * 3600          # the feed changes once a day
RETRY_AFTER = 10 * 60           # after a failed fetch
KEEP_DAYS = 60
SIMILAR = 6                     # similar words to keep per word
SIMILAR_RETRY = 3600            # seconds before looking a word up again after a failure
SIMILAR_TRIES = 3               # ...and how many times before giving up on it


def log(*a):
    print("[wotd]", *a, flush=True)


# ── Parsing the feed ──────────────────────────────────────────────────────────

class Paragraphs(HTMLParser):
    """HTML -> paragraphs, each a list of (text, italic) runs."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.paras, self.runs, self.em = [], [], 0

    def flush(self):
        text = "".join(t for t, _ in self.runs).strip()
        if text:
            self.paras.append(tidy(self.runs))
        self.runs = []

    def handle_starttag(self, tag, attrs):
        if tag == "p":
            self.flush()
        elif tag in ("em", "i"):
            self.em += 1

    def handle_endtag(self, tag):
        if tag == "p":
            self.flush()
        elif tag in ("em", "i"):
            self.em = max(0, self.em - 1)

    def handle_data(self, data):
        self.runs.append((data, self.em > 0))

    def close(self):
        super().close()
        self.flush()
        return self.paras


def tidy(runs):
    """Runs with whitespace collapsed (and none at the ends), neighbours of a kind joined."""
    out = []
    for text, it in runs:
        text = re.sub(r"\s+", " ", text)
        if out and out[-1][1] == it:
            out[-1] = (out[-1][0] + text, it)
        elif text:
            out.append((text, it))
    if out:
        out[0] = (out[0][0].lstrip(), out[0][1])
        out[-1] = (out[-1][0].rstrip(), out[-1][1])
    return [r for r in out if r[0]]


def plain(runs):
    return "".join(t for t, _ in runs)


def parse_item(item):
    """One feed item -> {"date" (YYYY-MM-DD), "word", "say", "pos", "meaning" ([{"runs",
    "example"}] in the feed's order: a sense, then its example), "quotes" ([runs]),
    "story" ([runs]), "short", "link"}."""
    paras = Paragraphs()
    paras.feed(item.findtext("description") or "")
    paras = paras.close()
    texts = [plain(p) for p in paras]
    head = next(i for i, t in enumerate(texts) if "•" in t)
    bits = [b.strip() for b in texts[head].split("•")]
    say = bits[1].strip("\\ ") if len(bits) > 1 else ""
    pos = bits[2] if len(bits) > 2 else ""
    sections, name = {"meaning": [], "quotes": [], "story": []}, "meaning"
    for p, t in zip(paras[head + 1:], texts[head + 1:]):
        if t.startswith("Examples:"):
            name = "quotes"
        elif t.startswith("Did you know?"):
            name = "story"
        elif not t.startswith("See the entry"):
            sections[name].append(p)
    meaning = [{"runs": tidy([(p[0][0][2:], p[0][1])] + p[1:]), "example": True} if plain(p).startswith("//")
               else {"runs": p, "example": False} for p in sections["meaning"]]
    when = re.search(r"for (\w+ \d+, \d{4})", texts[0] if texts else "")
    date = datetime.strptime(when.group(1), "%B %d, %Y") if when else \
        datetime.strptime(item.findtext("pubDate")[5:16], "%d %b %Y")
    return {"date": date.strftime("%Y-%m-%d"), "word": (item.findtext("title") or bits[0]).strip(),
            "say": say, "pos": pos, "meaning": meaning, "quotes": sections["quotes"],
            "story": sections["story"], "short": (item.findtext(f"{MW}shortdef") or "").strip(),
            "link": item.findtext("link") or ""}


def parse_feed(xml_bytes):
    """The RSS feed -> its words, newest first (items that won't parse are skipped)."""
    out = []
    for item in ET.fromstring(xml_bytes).iter("item"):
        try:
            out.append(parse_item(item))
        except (StopIteration, ValueError, TypeError, IndexError) as e:
            log(f"skipping {item.findtext('title')!r}: {e}")
    return sorted(out, key=lambda w: w["date"], reverse=True)


def similar(entries, word):
    """Free Dictionary API reply -> up to SIMILAR synonyms (not the word itself), most cited first."""
    seen = {}
    for e in entries if isinstance(entries, list) else []:
        for m in e.get("meanings", []):
            for s in m.get("synonyms", []) + [s for d in m.get("definitions", []) for s in d.get("synonyms", [])]:
                if s.lower() != word.lower():
                    seen[s] = seen.get(s, 0) + 1
    return [s for s, _ in sorted(seen.items(), key=lambda kv: -kv[1])][:SIMILAR]


# ── Fetching and keeping ──────────────────────────────────────────────────────

def http_get(url, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def explain(err):
    if isinstance(err, urllib.error.HTTPError):
        return f"HTTP {err.code}"
    if isinstance(err, urllib.error.URLError):
        return "offline"
    return str(err)[:60] or type(err).__name__


class Store:
    """Every word seen, newest first, kept on disk; the feed checked every few hours.

    words() is the list; each word gains "similar" once looked up.
    """

    def __init__(self, cache_dir=CACHE_DIR, get=http_get, clock=time.time):
        self.cache_dir, self.get, self.clock = cache_dir, get, clock
        self.lock = threading.Lock()
        self.err, self.retry_at = None, 0
        saved = self._load()
        self.archive = saved.get("words", {})
        self.fetched = saved.get("fetched")
        self.tries = {}                         # word -> (failed lookups, when last)

    def words(self):
        with self.lock:
            return [self.archive[d] for d in sorted(self.archive, reverse=True)]

    def error(self):
        with self.lock:
            return self.err

    def stamp(self):
        with self.lock:
            return (self.fetched, self.err, len(self.archive),
                    sum(1 for w in self.archive.values() if "similar" in w))

    def refresh(self):
        """One pass: the feed if due, else one word's similar words. True if it fetched anything."""
        now = self.clock()
        if now < self.retry_at:
            return False
        if not self.fetched or now - self.fetched >= FETCH_EVERY:
            try:
                words = parse_feed(self.get(FEED))
                if not words:
                    raise ValueError("no words in the feed")
            except Exception as e:              # network, HTTP, a feed that won't parse
                with self.lock:
                    self.err = explain(e)
                self.retry_at = now + RETRY_AFTER
                log(f"feed: {explain(e)} ({e})")
                return True
            with self.lock:
                for w in words:
                    w["similar"] = self.archive.get(w["date"], {}).get("similar", w.get("similar"))
                    if w["similar"] is None:
                        w.pop("similar")
                    self.archive[w["date"]] = w
                for d in sorted(self.archive)[:-KEEP_DAYS]:
                    del self.archive[d]
                self.fetched, self.err = now, None
            self._save()
            return True
        todo = next((w for w in self.words() if "similar" not in w
                     and now - self.tries.get(w["word"], (0, 0))[1] >= SIMILAR_RETRY), None)
        if not todo:
            return False
        try:
            found = similar(json.loads(self.get(DICTIONARY + urllib.parse.quote(todo["word"].lower()))),
                            todo["word"])
        except Exception as e:
            if not (isinstance(e, urllib.error.HTTPError) and e.code == 404):
                n = self.tries.get(todo["word"], (0, 0))[0] + 1
                self.tries[todo["word"]] = (n, now)
                log(f"similar {todo['word']}: {explain(e)} (try {n})")
                if n < SIMILAR_TRIES:           # try the next word meanwhile; this one later
                    return True
            found = []                          # 404 (not in that dictionary) or given up
        with self.lock:
            self.archive[todo["date"]]["similar"] = found
        self._save()
        return True

    def _load(self):
        try:
            with open(os.path.join(self.cache_dir, "words.json")) as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def _save(self):
        path = os.path.join(self.cache_dir, "words.json")
        with self.lock:
            data = {"words": self.archive, "fetched": self.fetched}
        try:
            os.makedirs(self.cache_dir, exist_ok=True)
            with open(path + ".tmp", "w") as f:
                json.dump(data, f)
            os.replace(path + ".tmp", path)
        except OSError as e:
            log("can't save:", e)

    def run(self, every=20):
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
    """Fetch and parse the live feed (run on the Pi or a Mac)."""
    store = Store(cache_dir="/nonexistent")
    store._save = lambda: None
    store.refresh()
    store.refresh()
    if store.error():
        print("FAILED -", store.error())
        return 1
    words = store.words()
    w = words[0]
    print(f"{len(words)} words in the feed, newest {w['date']}:")
    print(f"  {w['word']}  \\{w['say']}\\  {w['pos']}")
    for m in w["meaning"]:
        print(f"  {'example: ' if m['example'] else ''}{plain(m['runs'])}")
    print(f"  story: {plain(w['story'][0])[:90] if w['story'] else '-'}…")
    print(f"  similar: {', '.join(w.get('similar', [])) or '-'}")
    print("  earlier: " + ", ".join(x["word"] for x in words[1:]))
    return 0


if __name__ == "__main__":
    if sys.argv[1:] == ["--check"]:
        sys.exit(check())
    print(__doc__)
