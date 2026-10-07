#!/usr/bin/env python3
"""Weather and tide data for the Weather app: fetching, caching and the sums (no pygame here).

Weather comes from Open-Meteo (free, no key). Tides come from the ADMIRALTY UK Tidal API,
Discovery tier (free key, saved on the Pi by mac/set-tide-key.sh): high and low water times
and heights for 7 days. Between them the height follows a cosine curve, as tide tables do.
Everything fetched is cached to disk, so the screen keeps working when the network doesn't.

On the Pi, check the tide key and stations with:
    python3 /opt/pi3-touch/apps/weather/weather_data.py --check
"""
import json
import math
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.realpath(__file__))
LOCATIONS = os.path.join(HERE, "locations.json")
LOCAL = ZoneInfo("Europe/London")
UTC = timezone.utc
OPEN_METEO = "https://api.open-meteo.com/v1/forecast"
ADMIRALTY = "https://admiraltyapi.azure-api.net/uktidalapi/api/V1/Stations"
KEY_FILE = "/etc/pi3-touch/admiralty-key"
CACHE_DIR = os.environ.get("STATE_DIRECTORY", "/var/lib/pi3-touch-weather")
WEATHER_EVERY = 15 * 60         # seconds between weather fetches (per location)
TIDES_EVERY = 6 * 3600          # tide predictions barely change; 7 days come at once
RETRY_AFTER = 2 * 60            # after a failed fetch
KEEP_PAST = timedelta(days=2)   # old tide events kept so today's curve starts at midnight
USER_AGENT = "pi3-touch-weather/1.0 (+https://github.com/typedformiles/pi3-touch)"

CURRENT = ("temperature_2m,apparent_temperature,weather_code,is_day,"
           "wind_speed_10m,wind_direction_10m,wind_gusts_10m,precipitation")
HOURLY = ("temperature_2m,weather_code,is_day,wind_speed_10m,wind_direction_10m,"
          "wind_gusts_10m,precipitation_probability")
DAILY = ("weather_code,temperature_2m_max,temperature_2m_min,wind_speed_10m_max,"
         "wind_gusts_10m_max,wind_direction_10m_dominant,precipitation_probability_max,"
         "sunrise,sunset")


def log(*a):
    print("[weather]", *a, flush=True)


def slug(name):
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def load_config(path=LOCATIONS):
    with open(path) as f:
        return json.load(f)


# ── Weather ───────────────────────────────────────────────────────────────────

def weather_url(loc):
    q = {"latitude": loc["lat"], "longitude": loc["lon"], "current": CURRENT, "hourly": HOURLY,
         "daily": DAILY, "wind_speed_unit": "kn", "timezone": "Europe/London", "forecast_days": 7}
    return OPEN_METEO + "?" + urllib.parse.urlencode(q, safe=",")


def local_time(s):
    """Open-Meteo's local times ("2026-10-07T09:15") as aware datetimes."""
    return datetime.fromisoformat(s).replace(tzinfo=LOCAL)


def rows(block):
    """Open-Meteo's {"time": [...], "x": [...]} columns as a list of dicts, one per row."""
    keys = list(block)
    return [dict(zip(keys, vals)) for vals in zip(*(block[k] for k in keys))]


def parse_weather(data):
    """{"current": {...}, "hours": [...], "days": [...]} with times as datetimes."""
    cur = dict(data["current"], time=local_time(data["current"]["time"]))
    hours = [dict(h, time=local_time(h["time"])) for h in rows(data["hourly"])]
    days = []
    for d in rows(data["daily"]):
        days.append(dict(d, date=datetime.fromisoformat(d["time"]).date(),
                         sunrise=local_time(d["sunrise"]), sunset=local_time(d["sunset"])))
    return {"current": cur, "hours": hours, "days": days}


def hours_ahead(weather, now, n):
    """The n hourly rows starting with the hour we're in."""
    start = now.replace(minute=0, second=0, microsecond=0)
    return [h for h in weather["hours"] if h["time"] >= start][:n]


def today(weather, now):
    return next((d for d in weather["days"] if d["date"] == now.date()), None)


# Beaufort force: upper limits in knots (force 0 is under 1 kn, ... force 12 is 64 kn and over)
BEAUFORT = [(1, "Calm"), (4, "Light air"), (7, "Light breeze"), (11, "Gentle breeze"),
            (17, "Moderate breeze"), (22, "Fresh breeze"), (28, "Strong breeze"),
            (34, "Near gale"), (41, "Gale"), (48, "Severe gale"), (56, "Storm"),
            (64, "Violent storm")]


def beaufort(knots):
    k = round(knots or 0)
    for force, (limit, name) in enumerate(BEAUFORT):
        if k < limit:
            return force, name
    return 12, "Hurricane force"


POINTS = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
          "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]


def compass(degrees):
    return POINTS[round((degrees or 0) % 360 / 22.5) % 16]


# WMO weather codes (as used by Open-Meteo) -> (description, icon)
CODES = {
    0: ("Clear", "clear"), 1: ("Mostly clear", "clear"), 2: ("Partly cloudy", "partly"),
    3: ("Overcast", "cloud"), 45: ("Fog", "fog"), 48: ("Freezing fog", "fog"),
    51: ("Light drizzle", "drizzle"), 53: ("Drizzle", "drizzle"), 55: ("Heavy drizzle", "drizzle"),
    56: ("Freezing drizzle", "drizzle"), 57: ("Freezing drizzle", "drizzle"),
    61: ("Light rain", "rain"), 63: ("Rain", "rain"), 65: ("Heavy rain", "rain"),
    66: ("Freezing rain", "rain"), 67: ("Freezing rain", "rain"),
    71: ("Light snow", "snow"), 73: ("Snow", "snow"), 75: ("Heavy snow", "snow"),
    77: ("Snow grains", "snow"), 80: ("Light showers", "showers"), 81: ("Showers", "showers"),
    82: ("Heavy showers", "showers"), 85: ("Snow showers", "snow"), 86: ("Snow showers", "snow"),
    95: ("Thunderstorm", "thunder"), 96: ("Thunder and hail", "thunder"),
    99: ("Thunder and hail", "thunder"),
}


def describe(code):
    return CODES.get(code, ("", "cloud"))


# ── Tides ─────────────────────────────────────────────────────────────────────

def parse_tides(raw):
    """Admiralty TidalEvents (UTC) -> [{"time", "kind": "high"|"low", "height" (m or None)}]."""
    out = {}
    for e in raw:
        kind = {"HighWater": "high", "LowWater": "low"}.get(e.get("EventType"))
        try:
            t = datetime.fromisoformat(e["DateTime"].rstrip("Z").split(".")[0]).replace(tzinfo=UTC)
        except (KeyError, TypeError, ValueError):
            continue
        if kind:
            out[t] = {"time": t, "kind": kind, "height": e.get("Height")}
    return [out[t] for t in sorted(out)]


def merge_events(old, new, now):
    """New predictions, plus the last couple of days' events from before them."""
    if not new:
        return old
    first = min(e["DateTime"] for e in new)
    cutoff = (now - KEEP_PAST).astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S")
    return [e for e in old if cutoff <= e["DateTime"] < first] + list(new)


def curve_points(events):
    """Events with heights, plus a guessed one at each end so the curve reaches the edges.

    The guess mirrors the neighbouring tide (same interval and height as the one after the
    first event, or before the last) - good to a few minutes for UK semi-diurnal tides.
    """
    pts = [e for e in events if e["height"] is not None]
    if len(pts) < 2:
        return pts
    a, b = pts[0], pts[1]
    y, z = pts[-2], pts[-1]
    return ([{"time": a["time"] - (b["time"] - a["time"]), "kind": b["kind"], "height": b["height"]}]
            + pts
            + [{"time": z["time"] + (z["time"] - y["time"]), "kind": y["kind"], "height": y["height"]}])


def height_at(pts, t):
    """Tide height at time t (cosine between high and low water), or None outside the data."""
    for a, b in zip(pts, pts[1:]):
        if a["time"] <= t <= b["time"]:
            span = (b["time"] - a["time"]).total_seconds()
            f = (t - a["time"]).total_seconds() / span if span else 0.0
            return a["height"] + (b["height"] - a["height"]) * (1 - math.cos(math.pi * f)) / 2
    return None


def next_event(events, now, kind=None):
    return next((e for e in events if e["time"] > now and kind in (None, e["kind"])), None)


def tide_state(events, now):
    """"rising" or "falling" (or None with no future events)."""
    nxt = next_event(events, now)
    return None if nxt is None else ("rising" if nxt["kind"] == "high" else "falling")


def day_events(events, day):
    return [e for e in events if e["time"].astimezone(LOCAL).date() == day]


def find_station(features, name):
    """{"id", "name"} of the Admiralty station called name (exact match first), or None."""
    props = [f.get("properties", {}) for f in features]
    want = name.casefold()
    exact = [p for p in props if p.get("Name", "").casefold() == want]
    part = [p for p in props if want in p.get("Name", "").casefold()]
    best = (exact or part or [None])[0]
    return {"id": best["Id"], "name": best["Name"]} if best else None


def read_key(path=KEY_FILE):
    try:
        with open(path) as f:
            return f.read().strip() or None
    except OSError:
        return None


# ── Fetching and caching ──────────────────────────────────────────────────────

def http_json(url, headers=None, timeout=20):
    req = urllib.request.Request(url, headers=dict({"User-Agent": USER_AGENT}, **(headers or {})))
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def explain(err):
    if isinstance(err, urllib.error.HTTPError):
        return {401: "tide key rejected", 403: "tide quota used up", 429: "too many requests"}.get(
            err.code, f"HTTP {err.code}")
    if isinstance(err, urllib.error.URLError):
        return "offline"
    return str(err)[:60] or type(err).__name__


class Store:
    """Keeps every location's weather (and tides, at the coast) fresh, cached on disk.

    get() returns {"fetched": epoch seconds, ...} or None. Fetching happens in a background
    thread (start()), one location at a time, so switching locations on screen is instant.
    """

    def __init__(self, locations, cache_dir=CACHE_DIR, key_file=KEY_FILE, fetch=http_json, clock=time.time):
        self.locations, self.cache_dir, self.key_file = locations, cache_dir, key_file
        self.fetch, self.clock = fetch, clock
        self.lock = threading.Lock()
        self.entries, self.errors, self.retry_at = {}, {}, {}
        for loc in locations:
            for kind in ("weather", "tides"):
                try:
                    with open(self.path(loc, kind)) as f:
                        self.entries[(slug(loc["name"]), kind)] = json.load(f)
                except (OSError, ValueError):
                    pass

    def path(self, loc, kind):
        return os.path.join(self.cache_dir, f"{kind}-{slug(loc['name'])}.json")

    def get(self, loc, kind):
        with self.lock:
            return self.entries.get((slug(loc["name"]), kind))

    def error(self, loc, kind):
        with self.lock:
            return self.errors.get((slug(loc["name"]), kind))

    def refresh(self):
        """One pass: fetch whatever is due. Returns how many fetches were attempted."""
        tried = 0
        key = read_key(self.key_file)
        for loc in self.locations:
            tried += self._maybe(loc, "weather", WEATHER_EVERY, self._fetch_weather)
            if loc.get("tide_station"):
                if key:
                    tried += self._maybe(loc, "tides", TIDES_EVERY, lambda l: self._fetch_tides(l, key))
                else:
                    with self.lock:
                        self.errors[(slug(loc["name"]), "tides")] = "no tide key"
        return tried

    def _maybe(self, loc, kind, every, fetcher):
        k, now = (slug(loc["name"]), kind), self.clock()
        entry = self.get(loc, kind)
        if (entry and now - entry["fetched"] < every) or now < self.retry_at.get(k, 0):
            return 0
        try:
            entry = dict(fetcher(loc), fetched=now)
        except Exception as e:                  # network, HTTP, bad JSON: keep the old data
            with self.lock:
                self.errors[k] = explain(e)
            self.retry_at[k] = now + RETRY_AFTER
            log(f"{loc['name']} {kind}: {explain(e)} ({e})")
            return 1
        with self.lock:
            self.entries[k] = entry
            self.errors.pop(k, None)
        self._save(loc, kind, entry)
        return 1

    def _save(self, loc, kind, entry):
        try:
            os.makedirs(self.cache_dir, exist_ok=True)
            tmp = self.path(loc, kind) + ".tmp"
            with open(tmp, "w") as f:
                json.dump(entry, f)
            os.replace(tmp, self.path(loc, kind))
        except OSError as e:
            log("can't cache:", e)

    def _fetch_weather(self, loc):
        return {"data": self.fetch(weather_url(loc))}

    def _fetch_tides(self, loc, key):
        headers = {"Ocp-Apim-Subscription-Key": key}
        old = self.get(loc, "tides") or {}
        station = old.get("station")
        if not station or station.get("query") != loc["tide_station"]:
            station = self.lookup_station(loc["tide_station"], headers)
        raw = self.fetch(f"{ADMIRALTY}/{station['id']}/TidalEvents?duration=7", headers)
        now = datetime.fromtimestamp(self.clock(), UTC)
        return {"station": station, "events": merge_events(old.get("events", []), raw, now)}

    def lookup_station(self, name, headers):
        url = ADMIRALTY + "?" + urllib.parse.urlencode({"name": name})
        station = find_station(self.fetch(url, headers).get("features", []), name)
        if not station:                          # the name filter is picky: search the full list
            station = find_station(self.fetch(ADMIRALTY, headers).get("features", []), name)
        if not station:
            raise LookupError(f"no tide station called {name}")
        log(f"tide station for {name}: {station['name']} ({station['id']})")
        return dict(station, query=name)

    def run(self, every=30):
        while True:
            self.refresh()
            time.sleep(every)

    def start(self):
        threading.Thread(target=self.run, daemon=True).start()
        return self


def check():
    """Command-line check of the tide key and stations (run on the Pi)."""
    key = read_key()
    if not key:
        print(f"No tide key in {KEY_FILE} - run bash mac/set-tide-key.sh on the Mac")
        return 1
    store = Store(load_config()["locations"], cache_dir="/nonexistent")
    headers = {"Ocp-Apim-Subscription-Key": key}
    ok = True
    for loc in (l for l in store.locations if l.get("tide_station")):
        try:
            st = store.lookup_station(loc["tide_station"], headers)
            raw = http_json(f"{ADMIRALTY}/{st['id']}/TidalEvents?duration=2", headers)
            evs = parse_tides(raw)[:4]
            print(f"{loc['name']}: {st['name']} ({st['id']}) - " + ", ".join(
                f"{e['kind']} {e['time'].astimezone(LOCAL):%a %H:%M} "
                + (f"{e['height']:.1f}m" if e["height"] is not None else "?") for e in evs))
        except Exception as e:
            print(f"{loc['name']}: FAILED - {explain(e)} ({e})")
            ok = False
    return 0 if ok else 1


if __name__ == "__main__":
    if sys.argv[1:] == ["--check"]:
        sys.exit(check())
    print(__doc__)
