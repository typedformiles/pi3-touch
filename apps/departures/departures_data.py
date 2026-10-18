#!/usr/bin/env python3
"""Live train times for the Departures app: fetching and the sums (no pygame here).

Everything comes from TfL's Unified API (api.tfl.gov.uk, free, no key needed at this rate):
the predicted arrivals at each station in departures.json, and the line's status. A train
keeps the same vehicle id all along its route, so a westbound train at Paddington can be
found again in Taplow's predictions - that gives the time it gets home.

Predictions are kept in memory only: after a restart, old times would be wrong times.

On the Pi, check the stations and what TfL says now with:
    python3 /opt/pi3-touch/apps/departures/departures_data.py --check
"""
import gzip
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.realpath(__file__))
CONFIG = os.path.join(HERE, "departures.json")
LOCAL = ZoneInfo("Europe/London")
UTC = timezone.utc
API = "https://api.tfl.gov.uk"
USER_AGENT = "pi3-touch-departures/1.0 (+https://github.com/typedformiles/pi3-touch)"
ARRIVALS_EVERY = 30             # seconds; TfL refreshes predictions about this often
STATUS_EVERY = 2 * 60
RETRY_AFTER = 60                # after a failed fetch
GONE_AFTER = 30                 # seconds past its time a train drops off the board
GOOD_SERVICE = 10               # TfL's statusSeverity for "Good Service"
GHOST_KEPT = 10 * 60            # see ghost()


def log(*a):
    print("[departures]", *a, flush=True)


def load_config(path=CONFIG):
    with open(path) as f:
        return json.load(f)


def api_time(s):
    """TfL's "2026-10-18T20:14:00Z" (sometimes with fractions of a second) as aware UTC."""
    return datetime.strptime(s[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=UTC)


def place(name):
    """A destination as a board shows it: "Reading Rail Station" -> "Reading",
    "Heathrow Terminal 4 Rail Station" -> "Heathrow T4", "London Paddington..." -> "Paddington"."""
    name = re.sub(r" (Rail|Underground|DLR|Elizabeth line) Station$", "", name or "").strip()
    name = re.sub(r"^London ", "", name)
    name = re.sub(r"^Heathrow Terminals? ", "Heathrow T", name).replace(" & ", "&")
    return name or "?"


def platform(name):
    """"Platform 4" -> "4", "B" -> "B", "Platform Unknown" -> ""."""
    name = re.sub(r"^Platform\s*", "", name or "").strip()
    return "" if name.lower() in ("unknown", "") else name


# ── The sums ──────────────────────────────────────────────────────────────────

def ghost(p, t):
    """TfL sometimes lists a train that isn't coming: due now, no platform, and kept for an
    hour or more (a real prediction expires a minute before its time)."""
    try:
        keep = api_time(p["timeToLive"])
    except (KeyError, TypeError, ValueError):
        return False
    return not platform(p.get("platformName")) and (keep - t).total_seconds() > GHOST_KEPT


def trains(raw, station, now):
    """TfL predictions at a station -> its departures, soonest first: [{"vehicle", "time",
    "dest", "platform", "direction"}]. Trains ending here, long gone or (with the station's
    "direction" set) going the other way are left out; repeats of a train are merged."""
    here = place(station["name"])
    out = {}
    for p in raw:
        try:
            t = api_time(p["expectedArrival"])
        except (KeyError, TypeError, ValueError):
            continue
        dest = place(p.get("destinationName"))
        if p.get("destinationNaptanId") == station["stop"] or dest == here:
            continue
        if (now - t).total_seconds() > GONE_AFTER or ghost(p, t):
            continue
        if station.get("direction") and p.get("direction") != station["direction"]:
            continue
        v = p.get("vehicleId") or p.get("id")
        if v in out and out[v]["time"] <= t:
            continue
        out[v] = {"vehicle": v, "time": t, "dest": dest, "platform": platform(p.get("platformName")),
                  "direction": p.get("direction") or ""}
    return sorted(out.values(), key=lambda d: d["time"])


def journeys(deps, home_deps, home):
    """Which departures get home, and when. home is the station's "to" settings:
    {"name", "via" (destinations beyond home), "minutes" (usual journey)}. Each departure
    gains "home": the arrival time there (exact when TfL has the same train in home's
    predictions, else estimated from the usual journey time), "estimated" and "calls".
    Returns the departures; the usual time is learned from the exact ones."""
    longest = timedelta(minutes=2 * home.get("minutes", 35))

    def arrival(d):
        """The same train at home: same direction, and not hours later (a train's id stays
        with it on its way back, and on its next trip out)."""
        for h in home_deps:
            if h["vehicle"] == d["vehicle"] and (h["direction"] == d["direction"] or not d["direction"]) \
                    and timedelta(0) < h["time"] - d["time"] <= longest:
                return h["time"]
        return None

    at_home = {d["vehicle"]: arrival(d) for d in deps}
    exact = [at_home[d["vehicle"]] - d["time"] for d in deps if at_home[d["vehicle"]]]
    usual = sorted(exact)[len(exact) // 2] if exact else timedelta(minutes=home.get("minutes", 35))
    via = {v.lower() for v in home.get("via", [])} | {place(home["name"]).lower()}
    for d in deps:
        t = at_home[d["vehicle"]]
        if t:
            d.update(home=t, estimated=False, calls=True)
        elif d["dest"].lower() in via:
            d.update(home=d["time"] + usual, estimated=True, calls=True)
        else:
            d.update(home=None, estimated=False, calls=False)
    return deps


def countdown(t, now):
    """"Due", "1 min", "12 min", "1h 56m" until a train's time."""
    mins = int((t - now).total_seconds() // 60)
    if mins >= 60:
        return f"{mins // 60}h {mins % 60:02d}m"
    return "Due" if mins < 1 else f"{mins} min"


def parse_status(raw):
    """The line's status -> [{"severity", "text", "reason"}], worst first ([] if nothing)."""
    out = []
    for line in raw or []:
        for s in line.get("lineStatuses", []):
            reason = re.sub(r"^[A-Z ]+LINE:\s*", "", (s.get("reason") or "").strip())
            out.append({"severity": s.get("statusSeverity", GOOD_SERVICE),
                        "text": s.get("statusSeverityDescription") or "?", "reason": reason})
    return sorted(out, key=lambda s: s["severity"])


# ── Fetching ──────────────────────────────────────────────────────────────────

def http_json(url, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json",
                                               "Accept-Encoding": "gzip"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = r.read()
        if r.headers.get("Content-Encoding") == "gzip":
            body = gzip.decompress(body)
    return json.loads(body)


def explain(err):
    if isinstance(err, urllib.error.HTTPError):
        return {404: "no such stop?", 429: "too many requests"}.get(err.code, f"HTTP {err.code}")
    if isinstance(err, urllib.error.URLError):
        return "offline"
    return str(err)[:60] or type(err).__name__


class Store:
    """Keeps every station's predictions (and the line status) fresh, in memory.

    get(name) returns {"fetched": epoch seconds, "data": TfL's reply} for a station's stop
    id or "status", or None. Fetching happens in a background thread (start()).
    """

    def __init__(self, config, fetch=http_json, clock=time.time):
        self.config, self.fetch, self.clock = config, fetch, clock
        self.lock = threading.Lock()
        self.entries, self.errors, self.retry_at = {}, {}, {}

    def parts(self):
        """{name: (url, seconds between fetches)}"""
        out = {s["stop"]: (f"{API}/StopPoint/{s['stop']}/Arrivals", ARRIVALS_EVERY) for s in self.config["stations"]}
        out["status"] = (f"{API}/Line/{self.config.get('line', 'elizabeth')}/Status", STATUS_EVERY)
        return out

    def get(self, name):
        with self.lock:
            return self.entries.get(name)

    def error(self):
        """The most recent fetch problem still unresolved, or None."""
        with self.lock:
            return next(iter(sorted(self.errors.values(), reverse=True)), (0, None))[1]

    def stamp(self):
        """Changes whenever any data arrives - for knowing when to redraw."""
        with self.lock:
            return tuple(sorted((k, v["fetched"]) for k, v in self.entries.items()))

    def refresh(self):
        """One pass: fetch whatever is due. Returns how many parts were fetched (or tried)."""
        n = 0
        for name, (url, every) in self.parts().items():
            now = self.clock()
            entry = self.get(name)
            if (entry and now - entry["fetched"] < every) or now < self.retry_at.get(name, 0):
                continue
            n += 1
            try:
                data = self.fetch(url)
            except Exception as e:              # network, HTTP, bad JSON: keep the old data
                with self.lock:
                    self.errors[name] = (now, explain(e))
                self.retry_at[name] = now + RETRY_AFTER
                log(f"{name}: {explain(e)} ({e})")
                continue
            with self.lock:
                self.entries[name] = {"fetched": now, "data": data}
                self.errors.pop(name, None)
        return n

    def run(self, every=5):
        while True:
            self.refresh()
            time.sleep(every)

    def start(self):
        threading.Thread(target=self.run, daemon=True).start()
        return self

    # ── What the screens show ────────────────────────────────────────────────

    def departures(self, station, now):
        """A station's departures (with "home" times if it has a "to"), or None before data."""
        entry = self.get(station["stop"])
        if not entry:
            return None
        deps = trains(entry["data"], station, now)
        to = station.get("to")
        if to:
            home = next((s for s in self.config["stations"] if s["name"] == to["name"]), None)
            home_entry = self.get(home["stop"]) if home else None
            journeys(deps, trains(home_entry["data"], home, now) if home_entry else [], to)
        return deps

    def status(self):
        entry = self.get("status")
        return parse_status(entry["data"]) if entry else None


def check():
    """Command-line check of every station against the live API (run on the Pi or a Mac)."""
    cfg = load_config()
    store = Store(cfg)
    store.refresh()
    if store.error():
        print("FAILED -", store.error())
    now = datetime.now(UTC)
    for st in store.status() or []:
        print(f"{cfg.get('line', 'elizabeth')}: {st['text']}" + (f" - {st['reason'][:100]}" if st["reason"] else ""))
    for station in cfg["stations"]:
        deps = store.departures(station, now) or []
        print(f"{station['name']}: {len(deps)} trains")
        for d in deps[:4]:
            home = ""
            if "calls" in d:
                home = f" -> {station['to']['name']} {d['home'].astimezone(LOCAL):%H:%M}" \
                       f"{' (est)' if d['estimated'] else ''}" if d["calls"] else " (not stopping)"
            print(f"  {d['time'].astimezone(LOCAL):%H:%M} {d['dest']} [{d['platform'] or '?'}] "
                  f"{countdown(d['time'], now)}{home}")
    return 1 if store.error() else 0


if __name__ == "__main__":
    if sys.argv[1:] == ["--check"]:
        sys.exit(check())
    print(__doc__)
