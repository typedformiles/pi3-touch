#!/usr/bin/env python3
"""Questions for the Trivia app: fetching, keeping a stock and the bookkeeping (no pygame here).

Questions come from the Open Trivia Database (opentdb.com, free, no key): multiple choice
and true/false, in 24 topics and three difficulties. The API allows one call every 5
seconds, so a stock of questions is kept for the chosen topic - topped up in the background
and saved to disk - and a round can start straight away, even offline. A session token
stops questions coming round again until the topic runs out.

On the Pi, check the API with:
    python3 /opt/pi3-touch/apps/trivia/trivia_data.py --check
"""
import base64
import json
import os
import random
import sys
import threading
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.realpath(__file__))
CONFIG = os.path.join(HERE, "trivia.json")
CACHE_DIR = os.environ.get("STATE_DIRECTORY", "/var/lib/pi3-touch-trivia")
API = "https://opentdb.com"
USER_AGENT = "pi3-touch-trivia/1.0 (+https://github.com/typedformiles/pi3-touch)"
GAP = 5.5                       # seconds between API calls (it allows one per 5)
BATCH = 20                      # questions per call
SMALL_BATCH = 5                 # ...once a topic has fewer than that left
STOCK = 15                      # top the chosen topic up when it has fewer than this
RETRY_AFTER = 60                # after a failed fetch
# Topics: Open Trivia DB's category ids, with the names a button has room for
TOPICS = [(0, "Anything"), (9, "General"), (17, "Science"), (22, "Geography"), (23, "History"),
          (21, "Sport"), (11, "Film"), (12, "Music"), (14, "TV"), (10, "Books"), (27, "Animals"),
          (20, "Mythology"), (25, "Art"), (26, "Celebrities"), (24, "Politics"), (28, "Vehicles"),
          (18, "Computers"), (19, "Maths"), (30, "Gadgets"), (15, "Video Games"), (16, "Board Games"),
          (13, "Theatre"), (29, "Comics"), (32, "Cartoons"), (31, "Anime")]
DIFFICULTIES = ["any", "easy", "medium", "hard"]
SHORT_NAMES = {f"Entertainment: {n}": n for n in ("Books", "Film", "Music", "Television", "Video Games",
                                                  "Board Games", "Comics")}
SHORT_NAMES.update({"Entertainment: Musicals & Theatres": "Theatre", "Science: Computers": "Computers",
                    "Science: Mathematics": "Maths", "Science: Gadgets": "Gadgets",
                    "Entertainment: Japanese Anime & Manga": "Anime",
                    "Entertainment: Cartoon & Animations": "Cartoons", "Entertainment: Television": "TV",
                    "Science & Nature": "Science", "General Knowledge": "General"})


def log(*a):
    print("[trivia]", *a, flush=True)


def load_config(path=CONFIG):
    with open(path) as f:
        return json.load(f)


def topic_name(category):
    return dict(TOPICS).get(category, "?")


def key(category, difficulty):
    return f"{category}-{difficulty}"


# ── Questions ─────────────────────────────────────────────────────────────────

def unpack(raw, rng=random):
    """One API question (base64 fields) -> {"q", "answers" (shuffled; True/False in that
    order), "correct" (the right one), "topic", "difficulty", "type"}."""
    def b(s):
        return base64.b64decode(s).decode("utf-8", "replace").strip()
    right = b(raw["correct_answer"])
    wrong = [b(x) for x in raw["incorrect_answers"]]
    kind = b(raw["type"])
    if kind == "boolean":
        answers = ["True", "False"]
    else:
        answers = [right] + wrong
        rng.shuffle(answers)
    cat = b(raw["category"])
    return {"q": b(raw["question"]), "answers": answers, "correct": right,
            "topic": SHORT_NAMES.get(cat, cat), "difficulty": b(raw["difficulty"]), "type": kind}


def verdict(score, total):
    """A word for how a round went."""
    if not total:
        return ""
    r = score / total
    return ("Perfect round!" if r == 1 else "Brilliant!" if r >= 0.8 else "Well played" if r >= 0.6
            else "Not bad" if r >= 0.4 else "Tough round" if r > 0 else "Better luck next time")


# ── Fetching and the stock ────────────────────────────────────────────────────

def http_json(url, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def explain(err):
    if isinstance(err, urllib.error.HTTPError):
        return {429: "too many requests"}.get(err.code, f"HTTP {err.code}")
    if isinstance(err, urllib.error.URLError):
        return "offline"
    return str(err)[:60] or type(err).__name__


class Store:
    """A stock of unasked questions per topic and difficulty, topped up in the background.

    Set `want` to a key() to keep that topic stocked; take(key, n) hands out questions.
    """

    def __init__(self, cache_dir=CACHE_DIR, fetch=http_json, clock=time.time, rng=random):
        self.cache_dir, self.fetch, self.clock, self.rng = cache_dir, fetch, clock, rng
        self.lock = threading.Lock()
        self.want = None
        self.last_call = 0
        self.err, self.retry_at = None, 0
        self.exhausted = set()                  # topics with nothing new left for this token
        self.low = set()                        # topics with fewer than BATCH left
        saved = self._load()
        self.stock = saved.get("stock", {})
        self.token = saved.get("token")
        self.choice = saved.get("choice")       # the topic and difficulty last played

    def count(self, k):
        with self.lock:
            return len(self.stock.get(k, []))

    def take(self, k, n):
        """Up to n questions from a topic's stock (removed from it)."""
        with self.lock:
            got, self.stock[k] = self.stock.get(k, [])[:n], self.stock.get(k, [])[n:]
        self._save()
        return got

    def remember(self, category, difficulty):
        self.choice = {"category": category, "difficulty": difficulty}
        self._save()

    def error(self):
        with self.lock:
            return self.err

    def stamp(self):
        with self.lock:
            return (self.err, tuple(sorted((k, len(v)) for k, v in self.stock.items())))

    def refresh(self):
        """One pass: top up the wanted topic if it's low. Returns True if it called the API."""
        k = self.want
        now = self.clock()
        if not k or k in self.exhausted or self.count(k) >= STOCK or now < self.retry_at \
                or now - self.last_call < GAP:
            return False
        try:
            if not self.token:
                self.token = self._call(f"{API}/api_token.php?command=request")["token"]
                return True                     # the questions wait for the next gap
            category, difficulty = k.split("-")
            amount = SMALL_BATCH if k in self.low else BATCH
            url = f"{API}/api.php?amount={amount}&encode=base64&token={self.token}"
            if category != "0":
                url += f"&category={category}"
            if difficulty != "any":
                url += f"&difficulty={difficulty}"
            reply = self._call(url)
        except Exception as e:                  # network, HTTP, bad JSON
            with self.lock:
                self.err = explain(e)
            self.retry_at = now + RETRY_AFTER
            log(f"{k}: {explain(e)} ({e})")
            return True
        code = reply.get("response_code")
        if code == 0:
            fresh = [unpack(q, self.rng) for q in reply.get("results", [])]
            with self.lock:
                self.stock[k] = self.stock.get(k, []) + fresh
                self.err = None
            self._save()
        elif code == 1 and k not in self.low:   # fewer than a batch left: ask for a few at a time
            self.low.add(k)
        elif code == 1:                         # not even a few: the topic's used up
            self.exhausted.add(k)
            with self.lock:
                self.err = "seen them all"
        elif code == 3:                         # token unknown (unused for 6 hours): get a new one
            self.token = None
        elif code == 4:                         # this token has seen every question in the topic
            self.exhausted.add(k)
            with self.lock:
                self.err = "seen them all"
        elif code != 5:                         # 5 = too soon; anything else: say so
            with self.lock:
                self.err = f"API said {code}"
            self.retry_at = now + RETRY_AFTER
        return True

    def reset_topic(self, k):
        """Start a topic afresh once every question has been seen (old ones come round again)."""
        if k in self.exhausted and self.token:
            try:
                self._call(f"{API}/api_token.php?command=reset&token={self.token}")
            except Exception as e:
                log("reset:", explain(e))
                return
            self.exhausted.clear()
            self.low.clear()
            with self.lock:
                self.err = None

    def _call(self, url):
        self.last_call = self.clock()
        return self.fetch(url)

    def _load(self):
        try:
            with open(os.path.join(self.cache_dir, "trivia.json")) as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def _save(self):
        path = os.path.join(self.cache_dir, "trivia.json")
        with self.lock:
            data = {"stock": self.stock, "token": self.token, "choice": self.choice}
        try:
            os.makedirs(self.cache_dir, exist_ok=True)
            with open(path + ".tmp", "w") as f:
                json.dump(data, f)
            os.replace(path + ".tmp", path)
        except OSError as e:
            log("can't save:", e)

    def run(self, every=1):
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
    """Fetch a few questions from the live API (run on the Pi or a Mac)."""
    store = Store(cache_dir="/nonexistent")
    store._save = lambda: None
    store.want = key(0, "any")
    store.refresh()                             # a token
    time.sleep(GAP)
    store.refresh()                             # questions
    if store.error():
        print("FAILED -", store.error())
        return 1
    qs = store.take(store.want, 3)
    print(f"token ok, {len(qs) + store.count(store.want)} questions fetched")
    for q in qs:
        print(f"  [{q['topic']}, {q['difficulty']}] {q['q']}")
        print(f"     {' / '.join(q['answers'])}  -> {q['correct']}")
    return 0 if qs else 1


if __name__ == "__main__":
    if sys.argv[1:] == ["--check"]:
        sys.exit(check())
    print(__doc__)
