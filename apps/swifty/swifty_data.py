#!/usr/bin/env python3
"""The songs and the games' memory for the Swifty app (no pygame here).

Everything comes from songs.json, kept in the app: Taylor Swift's albums and their track
lists. There's no network: no Taylor Swift data service exists, and the albums and
dates don't change, so the app always works. A new album means a new entry in songs.json.

Swiftle is a daily puzzle: a song title hidden letter by letter. Everyone gets the same
song on the same day, and none repeats until every song has had its day. Six wrong
letters lose it; clues come as the misses go up. Practice games are unlimited and don't
count towards the streak.

Song Showdown puts two songs up at a time: pick the one you love more, and over many
battles the ratings (Elo, as in chess) rank every song, and every era.

Progress (today's guesses, practice, results, the showdown ratings) is kept in
swifty.json in the state folder, so leaving the app and coming back carries on where it was.

Check the song list with:
    python3 /opt/pi3-touch/apps/swifty/swifty_data.py --check
"""
import hashlib
import json
import os
import random
import sys
from datetime import date, timedelta

HERE = os.path.dirname(os.path.realpath(__file__))
CACHE_DIR = os.environ.get("STATE_DIRECTORY", "/var/lib/pi3-touch-swifty")
SONGS = os.path.join(HERE, "songs.json")
EPOCH = date(2026, 10, 19)              # Swiftle #1
LIVES = 6                               # wrong letters before the game is lost
CLUES = [("colour", 2), ("year", 3), ("album", 4), ("track", 5)]     # (clue, after this many misses)
KEEP_DAYS = 400
RECENT = 30                             # practice songs not to repeat


def log(*a):
    print("[swifty]", *a, flush=True)


# ── The songs ─────────────────────────────────────────────────────────────────

def load_catalogue(path=SONGS):
    """songs.json -> (albums, songs). Each song: {"title", "album", "short", "track", "year",
    "colour", "colour_name", "swatch"}; songs without enough letters to guess are left out.
    colour is the app's colour for the era; swatch is the era's colour as fans know it (the
    same, unless that's too dark or light to draw keys and buttons in - black for reputation)."""
    with open(path) as f:
        data = json.load(f)
    skip = set(data.get("skip", []))
    albums, songs = [], []
    for a in data["albums"]:
        album = {"title": a["title"], "short": a.get("short", a["title"]), "year": a["year"],
                 "colour": tuple(a["colour"]),
                 "colour_name": a.get("colour_name", ""), "swatch": tuple(a.get("swatch", a["colour"]))}
        albums.append(album)
        for n, title in enumerate(a["tracks"], 1):
            if title not in skip and len(letters(title)) >= 2:
                songs.append({"title": title, "album": album["title"], "short": album["short"], "track": n,
                              "year": album["year"], "colour": album["colour"],
                              "colour_name": album["colour_name"], "swatch": album["swatch"]})
    return albums, songs


def letters(title):
    """The letters to guess in a title, as capitals."""
    return {c for c in title.upper() if "A" <= c <= "Z"}


# ── Swiftle ───────────────────────────────────────────────────────────────────

class Swiftle:
    """One puzzle: a song and the letters guessed so far (in order)."""

    def __init__(self, song, guesses="", day=None):
        self.song, self.day = song, day
        self.guesses = ""
        self.hidden = letters(song["title"])
        for c in guesses:
            self.guess(c)

    def guess(self, c):
        """Try a letter. False if it doesn't count (not a letter, already tried, game over)."""
        c = c.upper()
        if not ("A" <= c <= "Z") or c in self.guesses or self.over:
            return False
        self.guesses += c
        return True

    @property
    def misses(self):
        return [c for c in self.guesses if c not in self.hidden]

    @property
    def solved(self):
        return self.hidden <= set(self.guesses)

    @property
    def lost(self):
        return len(self.misses) >= LIVES and not self.solved

    @property
    def over(self):
        return self.solved or self.lost

    @property
    def lives(self):
        return LIVES - len(self.misses)

    def shows(self, ch):
        """Whether a character of the title is on show (anything but an unguessed letter)."""
        u = ch.upper()
        return not ("A" <= u <= "Z") or u in self.guesses

    def clue(self, name):
        """Whether a clue is open: enough misses, or the game is over."""
        return self.over or len(self.misses) >= dict(CLUES)[name]


def song_order(songs):
    """Every song once, in a fixed shuffled order (a song added later slots in somewhere)."""
    return sorted(songs, key=lambda s: hashlib.sha256(("swiftle:" + s["title"]).encode()).hexdigest())


def puzzle_number(day):
    return (day - EPOCH).days + 1


def daily_song(songs, day):
    order = song_order(songs)
    return order[(day - EPOCH).days % len(order)]


class Saves:
    """swifty.json: one section per game, written whole after every change (it's small)."""

    def __init__(self, cache_dir=CACHE_DIR):
        self.path = os.path.join(cache_dir, "swifty.json")
        try:
            with open(self.path) as f:
                self.data = json.load(f)
        except (OSError, ValueError):
            self.data = {}

    def section(self, name):
        return self.data.setdefault(name, {})

    def save(self):
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            with open(self.path + ".tmp", "w") as f:
                json.dump(self.data, f)
            os.replace(self.path + ".tmp", self.path)
        except OSError as e:
            log("can't save:", e)


class SwiftleStore:
    """Today's puzzle, practice, and the results that make the streak."""

    def __init__(self, albums, songs, saves, rng=random):
        self.albums, self.songs, self.saves, self.rng = albums, songs, saves, rng
        self.by_title = {s["title"]: s for s in songs}
        self.mem = saves.section("swiftle")
        self.mem.setdefault("days", {})
        self._today = None
        p = self.mem.get("practice") or {}
        song = self.by_title.get(p.get("song"))
        self._practice = Swiftle(song, p.get("guesses", "")) if song else None

    @property
    def era(self):
        """The album practice songs come from (None: any)."""
        return self.mem.get("era")

    def today(self, day):
        """The day's puzzle, carrying on from any letters already guessed."""
        if self._today is None or self._today.day != day:
            song = daily_song(self.songs, day)
            saved = self.mem["days"].get(day.isoformat()) or {}
            self._today = Swiftle(song, saved.get("guesses", "") if saved.get("song") == song["title"] else "",
                                  day)
        return self._today

    def practice(self):
        return self._practice

    def new_practice(self, era=None, today=None):
        """A practice song from the era (None: any), not today's and not one played lately."""
        self.mem["era"] = era
        pool = [s for s in self.songs if era is None or s["album"] == era]
        recent = set(self.mem.get("recent", []))
        if today:
            recent.add(daily_song(self.songs, today)["title"])
        fresh = [s for s in pool if s["title"] not in recent] or pool
        song = self.rng.choice(fresh)
        self.mem["recent"] = (self.mem.get("recent", []) + [song["title"]])[-RECENT:]
        self._practice = Swiftle(song)
        self.mem["practice"] = {"song": song["title"], "guesses": ""}
        self.saves.save()
        return self._practice

    def guess(self, game, c):
        """A letter for a game (today's or practice); kept on disk straight away."""
        if not game.guess(c):
            return False
        if game.day:
            self.mem["days"][game.day.isoformat()] = {"song": game.song["title"], "guesses": game.guesses}
            for d in sorted(self.mem["days"])[:-KEEP_DAYS]:
                del self.mem["days"][d]
        else:
            self.mem["practice"] = {"song": game.song["title"], "guesses": game.guesses}
        self.saves.save()
        return True

    def results(self):
        """{date: (song, won, misses)} for every finished day."""
        out = {}
        for d, rec in self.mem["days"].items():
            g = Swiftle({"title": rec["song"]}, rec["guesses"])
            if g.over:
                out[d] = (rec["song"], g.solved, len(g.misses))
        return out

    def stats(self, today):
        """Played, won, the streak (today's counts once finished), the best streak, wins by
        number of misses, and the recent days newest first."""
        res = self.results()
        won = {d for d, r in res.items() if r[1]}
        day = today if today.isoformat() in res else today - timedelta(days=1)
        streak = 0
        while day.isoformat() in won:
            streak, day = streak + 1, day - timedelta(days=1)
        best = run = 0
        prev = None
        for d in sorted(res):
            dd = date.fromisoformat(d)
            run = (run + 1 if prev and dd - prev == timedelta(days=1) else 1) if d in won else 0
            best, prev = max(best, run), dd
        dist = [0] * LIVES
        for _, ok, misses in res.values():
            if ok:
                dist[misses] += 1
        recent = [(d, *res[d]) for d in sorted(res, reverse=True)]
        return {"played": len(res), "won": len(won), "streak": streak, "best": best, "dist": dist,
                "recent": recent}


# ── Song Showdown ─────────────────────────────────────────────────────────────

START, K = 1000, 32                     # Elo: every song starts level; K is how far one battle moves it
RECENT_PAIRS = 40                       # pairs not to see again soon
CLOSE = 8                               # a rival comes from the songs rated nearest


class ShowdownStore:
    """Two songs at a time: the one picked takes rating points from the other (Elo, as in
    chess), so over many battles the ratings rank every song. Every song battles once before
    any battles twice (and so on), each against a rival rated near it."""

    def __init__(self, albums, songs, saves, rng=random):
        self.albums, self.songs, self.saves, self.rng = albums, songs, saves, rng
        self.by_title = {s["title"]: s for s in songs}
        self.mem = saves.section("showdown")
        self.mem.setdefault("songs", {})        # title -> [rating, battles, wins]
        self.mem.setdefault("battles", 0)

    def rec(self, title):
        return self.mem["songs"].get(title, [START, 0, 0])

    @property
    def era(self):
        return self.mem.get("era")

    @property
    def battles(self):
        return self.mem["battles"]

    def set_era(self, era):
        self.mem["era"] = era
        self.mem["pair"] = None
        self.pair()

    def pair(self):
        """The two songs on screen, chosen once and kept (on disk) until one is picked."""
        p = self.mem.get("pair")
        if not p or any(t not in self.by_title for t in p):
            p = self.mem["pair"] = self._choose()
            self.saves.save()
        return [self.by_title[t] for t in p]

    def _choose(self):
        pool = [s for s in self.songs if self.era is None or s["album"] == self.era]
        if len(pool) < 2:
            pool = self.songs
        least = min(self.rec(s["title"])[1] for s in pool)
        fresh = [s for s in pool if self.rec(s["title"])[1] == least]    # the songs seen least
        a = self.rng.choice(fresh)
        recent = {frozenset(x) for x in self.mem.get("recent", [])}

        def rivals(group):
            return [s for s in group if s is not a and frozenset((a["title"], s["title"])) not in recent]
        others = rivals(fresh) or rivals([s for s in pool if self.rec(s["title"])[1] == least + 1]) or \
            rivals(pool) or [s for s in pool if s is not a]
        self.rng.shuffle(others)                 # so equal ratings don't favour the first album
        ra = self.rec(a["title"])[0]
        others.sort(key=lambda s: abs(self.rec(s["title"])[0] - ra))
        b = self.rng.choice(others[:CLOSE])
        pair = [a["title"], b["title"]]
        self.rng.shuffle(pair)
        return pair

    def _next(self):
        self.mem["recent"] = (self.mem.get("recent", []) + [self.mem["pair"]])[-RECENT_PAIRS:]
        self.mem["pair"] = self._choose()

    def pick(self, title):
        """The song picked from the pair beats the other. Returns (winner, loser) songs."""
        a, b = self.mem["pair"] if self.mem.get("pair") else [s["title"] for s in self.pair()]
        if title not in (a, b):
            return None
        loser = b if title == a else a
        w, l = list(self.rec(title)), list(self.rec(loser))
        self.mem["undo"] = {"pair": [a, b], "songs": {title: w, loser: l}}
        gain = K * (1 - 1 / (1 + 10 ** ((l[0] - w[0]) / 400)))
        self.mem["songs"][title] = [round(w[0] + gain, 1), w[1] + 1, w[2] + 1]
        self.mem["songs"][loser] = [round(l[0] - gain, 1), l[1] + 1, l[2]]
        self.mem["battles"] += 1
        self._next()
        self.saves.save()
        return self.by_title[title], self.by_title[loser]

    def skip(self):
        """Can't choose: a new pair, no points."""
        self.mem["undo"] = None
        self._next()
        self.saves.save()

    def can_undo(self):
        return bool(self.mem.get("undo"))

    def undo(self):
        """Take back the last pick: both songs' ratings, and the pair back on screen."""
        u = self.mem.get("undo")
        if not u:
            return False
        for title, rec in u["songs"].items():
            if rec[1]:
                self.mem["songs"][title] = rec
            else:
                self.mem["songs"].pop(title, None)
        self.mem["battles"] -= 1
        self.mem["pair"], self.mem["undo"] = u["pair"], None
        self.mem["recent"] = self.mem.get("recent", [])[:-1]
        self.saves.save()
        return True

    def ranking(self):
        """[(song, rating, battles, wins)] for every song that has battled, best first."""
        rows = [(self.by_title[t], *r) for t, r in self.mem["songs"].items() if t in self.by_title and r[1]]
        return sorted(rows, key=lambda x: (-x[1], -x[3], x[0]["title"]))

    def eras(self):
        """[(album, average rating or None, songs rated, top song or None)], favourite era first;
        eras with no songs rated yet at the end, in album order."""
        ranked = self.ranking()
        out = []
        for a in self.albums:
            mine = [r for r in ranked if r[0]["album"] == a["title"]]
            avg = sum(r[1] for r in mine) / len(mine) if mine else None
            out.append((a, avg, len(mine), mine[0][0] if mine else None))
        return sorted(out, key=lambda x: (x[1] is None, -(x[1] or 0)))


def check():
    """Print the catalogue and the next week's songs."""
    albums, songs = load_catalogue()
    print(f"{len(albums)} albums, {len(songs)} songs to guess")
    for a in albums:
        n = sum(s["album"] == a["title"] for s in songs)
        print(f"  {a['year']}  {a['title']:<32} {n:>2} songs  colour {a['colour']}")
    long = max(songs, key=lambda s: len(s["title"]))
    print(f"longest title: {long['title']} ({len(long['title'])})")
    today = date.today()
    for k in range(7):
        d = today + timedelta(days=k)
        print(f"  Swiftle #{puzzle_number(d)} {d}: {len(daily_song(songs, d)['title'])} characters")
    return 0


if __name__ == "__main__":
    if sys.argv[1:] == ["--check"]:
        sys.exit(check())
    print(__doc__)
