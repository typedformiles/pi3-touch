#!/usr/bin/env python3
"""Pi-hole data for the Pi-hole app: its v6 API, polling, and pausing blocking (no pygame here).

The API needs a password: the Pi-hole's app password (Settings > Web interface / API),
saved on this Pi by mac/set-pihole-key.sh. The app logs in once, keeps that session alive by
using it, and logs out when it stops (Pi-hole only allows a few sessions at once).

On the Pi, check the password and what the Pi-hole says with:
    python3 /opt/pi3-touch/apps/pihole/pihole_data.py --check
"""
import json
import os
import queue
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.realpath(__file__))
CONFIG = os.path.join(HERE, "pihole.json")
PASSWORD_FILE = "/etc/pi3-touch/pihole-password"
USER_AGENT = "pi3-touch-pihole/1.0 (+https://github.com/typedformiles/pi3-touch)"

# What to fetch, and how often (seconds). "queries" only while the Live page is showing.
EVERY = {"summary": 10, "blocking": 10, "history": 300, "top": 60, "queries": 3}
RETRY_AFTER = 30                # after a failure (offline, wrong password)
LIVE_ROWS = 14
TOP_ROWS = 12

# Query statuses (Pi-hole v6) that mean the query was blocked, or answered from the cache
BLOCKED = {"GRAVITY", "REGEX", "DENYLIST", "EXTERNAL_BLOCKED_IP", "EXTERNAL_BLOCKED_NULL",
           "EXTERNAL_BLOCKED_NXRA", "EXTERNAL_BLOCKED_EDE15", "GRAVITY_CNAME", "REGEX_CNAME",
           "DENYLIST_CNAME", "SPECIAL_DOMAIN", "DBBUSY"}
CACHED = {"CACHE", "CACHE_STALE"}


def log(*a):
    print("[pihole]", *a, flush=True)


def load_config(path=CONFIG):
    with open(path) as f:
        return json.load(f)


def read_password(path=PASSWORD_FILE):
    try:
        with open(path) as f:
            return f.read().strip() or None
    except OSError:
        return None


# ── Small sums for the screen ─────────────────────────────────────────────────

def kind(status):
    """"blocked", "cached" or "allowed" for a query's status."""
    return "blocked" if status in BLOCKED else "cached" if status in CACHED else "allowed"


def client_name(client):
    """A client's short name ("tims-iphone" from "tims-iphone.lan"), else its IP."""
    name = (client or {}).get("name") or ""
    ip = (client or {}).get("ip") or "?"
    return name.split(".")[0] if name and name != ip else ip


def via_router(clients):
    """The one client making nearly all the lookups (90%+), or None.

    That's a router passing every device's lookups on to the Pi-hole: they all arrive from
    the router's address, so the Pi-hole can't tell the devices apart.
    """
    total = sum(c.get("count", 0) for c in clients or [])
    if total and clients[0].get("count", 0) >= 0.9 * total:
        return client_name(clients[0])
    return None


def blocking_state(reply, now):
    """{"state": "enabled"|"disabled"|..., "until": epoch seconds or None} from /api/dns/blocking."""
    timer = reply.get("timer")
    return {"state": reply.get("blocking", "unknown"), "until": now + timer if timer else None}


def history_bars(history):
    """[(epoch, allowed, blocked)] from /api/history, oldest first."""
    out = []
    for h in history:
        total, blocked = h.get("total") or 0, h.get("blocked") or 0
        out.append((h["timestamp"], max(0, total - blocked), blocked))
    return sorted(out)


def ago(seconds):
    """"3 d" / "5 h" / "12 min" / "now"."""
    s = max(0, int(seconds))
    if s >= 86400:
        return f"{s // 86400} d"
    if s >= 3600:
        return f"{s // 3600} h"
    if s >= 60:
        return f"{s // 60} min"
    return "now"


def short_count(n):
    """1234567 -> "1.2M", 45678 -> "45.7k", 950 -> "950"."""
    n = n or 0
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 10_000:
        return f"{n / 1000:.1f}k"
    return f"{n:,}"


# ── The API ───────────────────────────────────────────────────────────────────

class ApiError(Exception):
    """Something to show on screen: "offline", "password needed", "wrong password"..."""


def http(method, url, headers, body=None, timeout=8):
    """(status, parsed JSON or None) - HTTP errors are returned, not raised."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers=dict({"User-Agent": USER_AGENT, "Accept": "application/json",
                                               "Content-Type": "application/json"}, **headers))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            return r.status, json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.load(e)
        except ValueError:
            return e.code, None


class Api:
    """The Pi-hole's /api with one login session, renewed when it lapses."""

    def __init__(self, url, password_file=PASSWORD_FILE, send=http):
        self.base = url.rstrip("/") + "/api"
        self.password_file, self.send = password_file, send
        self.sid = None
        self.logged_in = False
        self.lock = threading.Lock()            # the poller and the Pause button share the session

    def _send(self, method, path, body=None):
        headers = {"X-FTL-SID": self.sid} if self.sid else {}
        try:
            return self.send(method, self.base + path, headers, body)
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise ApiError("offline") from e

    def login(self):
        password = read_password(self.password_file)
        if not password:
            raise ApiError("password needed")
        self.sid, self.logged_in = None, False
        status, reply = self._send("POST", "/auth", {"password": password})
        session = (reply or {}).get("session") or {}
        if status == 429:
            raise ApiError("too many logins - waiting")
        if (reply or {}).get("error", {}).get("key") == "api_seats_exceeded":
            raise ApiError("too many sessions")
        if status != 200 or not session.get("valid"):
            raise ApiError("wrong password")
        self.sid, self.logged_in = session.get("sid"), True   # no sid: the Pi-hole has no password

    def call(self, method, path, body=None):
        with self.lock:
            if not self.logged_in:
                self.login()
            status, reply = self._send(method, path, body)
            if status == 401:                    # session expired (or the Pi-hole restarted)
                self.login()
                status, reply = self._send(method, path, body)
            if status != 200:
                raise ApiError(f"HTTP {status}")
            return reply

    def get(self, path, **params):
        q = ("?" + urllib.parse.urlencode(params)) if params else ""
        return self.call("GET", path + q)

    def logout(self):
        with self.lock:
            if self.sid:
                try:
                    self._send("DELETE", "/auth")
                except ApiError:
                    pass
            self.sid, self.logged_in = None, False


# ── Polling ───────────────────────────────────────────────────────────────────

class Monitor:
    """Polls the Pi-hole in a background thread and runs Pause / Resume.

    data(name) gives the latest summary, blocking, history, top, queries (or None);
    version goes up whenever any of them changes, so the screen redraws only then.
    """

    def __init__(self, api, clock=time.time):
        self.api, self.clock = api, clock
        self.lock = threading.Lock()
        self.values, self.fetched = {}, {}
        self.version = 0
        self.error = None
        self.retry_at = 0
        self.live = False                        # poll the query log (the Live page is showing)
        self.actions = queue.Queue()
        self.stopped = False

    def data(self, name):
        with self.lock:
            return self.values.get(name)

    def _set(self, name, value):
        with self.lock:
            self.fetched[name] = self.clock()
            if self.values.get(name) != value:
                self.values[name] = value
                self.version += 1

    def _set_error(self, err):
        with self.lock:
            if self.error != err:
                self.error = err
                self.version += 1

    def fetch(self, name):
        a = self.api
        if name == "summary":
            return a.get("/stats/summary")
        if name == "blocking":
            return blocking_state(a.get("/dns/blocking"), self.clock())
        if name == "history":
            return history_bars(a.get("/history").get("history", []))
        if name == "top":
            return {"blocked": a.get("/stats/top_domains", blocked="true", count=TOP_ROWS).get("domains", []),
                    "allowed": a.get("/stats/top_domains", count=TOP_ROWS).get("domains", []),
                    "clients": a.get("/stats/top_clients", count=TOP_ROWS).get("clients", [])}
        if name == "queries":
            return a.get("/queries", length=LIVE_ROWS).get("queries", [])
        raise KeyError(name)

    def refresh(self):
        """One pass: run any Pause / Resume, then fetch whatever is due. Returns names fetched."""
        while not self.actions.empty():
            self._act(self.actions.get())
        now = self.clock()
        if now < self.retry_at:
            return []
        done = []
        for name, every in EVERY.items():
            if name == "queries" and not self.live:
                continue
            if now - self.fetched.get(name, -1e9) < every:
                continue
            try:
                self._set(name, self.fetch(name))
            except ApiError as e:
                self._fail(str(e))
                return done
            except (KeyError, TypeError, AttributeError, ValueError) as e:
                self._fail(f"odd reply ({name})")
                log(f"{name}: {e!r}")
                return done
            done.append(name)
        self._set_error(None)
        return done

    def _fail(self, err):
        if err != self.error:
            log(err)
        self._set_error(err)
        self.retry_at = self.clock() + RETRY_AFTER

    def _act(self, action):
        seconds = action[1]
        body = {"blocking": True, "timer": None} if seconds is None else {"blocking": False, "timer": seconds}
        try:
            self._set("blocking", blocking_state(self.api.call("POST", "/dns/blocking", body), self.clock()))
            log("blocking resumed" if seconds is None else f"blocking paused for {seconds} s")
            self.retry_at = 0
        except ApiError as e:
            self._fail(f"couldn't {'resume' if seconds is None else 'pause'}: {e}")

    def pause(self, seconds):
        self.actions.put(("pause", seconds))

    def resume(self):
        self.actions.put(("resume", None))

    def run(self):
        while not self.stopped:
            self.refresh()
            try:                                 # wake at once for Pause / Resume
                self._act(self.actions.get(timeout=0.5))
            except queue.Empty:
                pass

    def start(self):
        threading.Thread(target=self.run, daemon=True).start()
        return self

    def stop(self):
        self.stopped = True
        self.api.logout()


def check():
    """Command-line check of the password and what the Pi-hole reports (run on the Pi)."""
    cfg = load_config()
    api = Api(cfg["url"])
    try:
        s = api.get("/stats/summary")
        b = api.get("/dns/blocking")
        clients = api.get("/stats/top_clients", count=5).get("clients", [])
    except ApiError as e:
        print(f"{cfg['url']}: FAILED - {e}" + (" (on the Mac: bash mac/set-pihole-key.sh)"
                                              if "password" in str(e) else ""))
        return 1
    finally:
        api.logout()
    q = s["queries"]
    print(f"{cfg['url']}: blocking {b.get('blocking')}; {q['total']:,} queries in 24 h, "
          f"{q['blocked']:,} blocked ({q['percent_blocked']:.1f}%); "
          f"{s['gravity']['domains_being_blocked']:,} domains on the blocklist")
    print("busiest devices: " + ", ".join(f"{client_name(c)} ({c['count']:,})" for c in clients))
    return 0


if __name__ == "__main__":
    import sys
    if sys.argv[1:] == ["--check"]:
        sys.exit(check())
    print(__doc__)
