"""Data for the World Clock's city pages: fetching, caching and the sums (no pygame here).

Only the city on screen is fetched (set_active), and only what's out of date; everything is
cached to disk so a city opens instantly with what we had. All sources are free, no keys:

  weather, air quality, sea  Open-Meteo (forecast, air-quality and marine APIs)
  news                       each city's public RSS feed (cities.py)
  exchange rates             Frankfurter (European Central Bank reference rates, daily)
  public holidays            Nager.Date
  earthquakes                USGS feed: M2.5+ worldwide, past day
"""
import email.utils
import json
import math
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

UTC = timezone.utc
CACHE_DIR = os.environ.get("STATE_DIRECTORY", "/var/lib/pi3-touch-world-clock")
USER_AGENT = "pi3-touch-world-clock/1.0 (+https://github.com/typedformiles/pi3-touch)"
RETRY_AFTER = 2 * 60
AED_PER_USD = 3.6725            # the dirham is pegged to the dollar (and the ECB doesn't quote it)
AWAKE = (8, 22)                 # hours people are happy to take a call, for "best time to call"
QUAKE_KM, QUAKE_MIN_MAG = 300, 3.5

# kind -> seconds between fetches; "rates" and "quakes" are shared by every city
EVERY = {"weather": 15 * 60, "air": 30 * 60, "sea": 60 * 60, "news": 30 * 60,
         "rates": 6 * 3600, "holidays": 24 * 3600, "quakes": 15 * 60}
SHARED = {"rates", "quakes"}


def log(*a):
    print("[city]", *a, flush=True)


def slug(name):
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


# ── URLs ──────────────────────────────────────────────────────────────────────

def q(base, **params):
    return base + "?" + urllib.parse.urlencode(params, safe=",")


def urls(city, kind):
    if kind == "weather":
        return q("https://api.open-meteo.com/v1/forecast", latitude=city["lat"], longitude=city["lon"],
                 current="temperature_2m,apparent_temperature,weather_code,is_day,wind_speed_10m,wind_direction_10m",
                 hourly="temperature_2m,weather_code,is_day,precipitation_probability",
                 daily="weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,"
                       "sunrise,sunset,daylight_duration,uv_index_max",
                 wind_speed_unit="kn", timezone=city["tz"], forecast_days=6)
    if kind == "air":
        return q("https://air-quality-api.open-meteo.com/v1/air-quality", latitude=city["lat"],
                 longitude=city["lon"], current="us_aqi,pm2_5", timezone=city["tz"])
    if kind == "sea":
        lat, lon = city["sea"]
        return q("https://marine-api.open-meteo.com/v1/marine", latitude=lat, longitude=lon,
                 current="sea_surface_temperature,wave_height")
    if kind == "news":
        return city["news"][1]
    if kind == "rates":
        return q("https://api.frankfurter.app/latest", **{"from": "GBP", "to": "USD,EUR,JPY,AUD"})
    if kind == "holidays":
        return f"https://date.nager.at/api/v3/NextPublicHolidays/{city['holidays'][0]}"
    if kind == "quakes":
        return "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/2.5_day.geojson"
    raise ValueError(kind)


def kinds(city):
    """What a city page needs."""
    out = ["weather", "air", "news", "rates", "quakes"]
    if city.get("sea"):
        out.append("sea")
    if city.get("holidays"):
        out.append("holidays")
    return out


# ── Parsing ───────────────────────────────────────────────────────────────────

def parse_news(raw, limit=8):
    """RSS bytes -> [{"title", "time" (aware datetime or None)}], newest feed order kept."""
    out = []
    for item in ET.fromstring(raw).iter("item"):
        title = " ".join((item.findtext("title") or "").split())
        if not title:
            continue
        try:
            when = email.utils.parsedate_to_datetime(item.findtext("pubDate") or "")
        except (TypeError, ValueError):
            when = None
        if when and when.tzinfo is None:
            when = when.replace(tzinfo=UTC)
        out.append({"title": title, "time": when.isoformat() if when else None})
        if len(out) >= limit:
            break
    return out


LOCAL_NAMES = {("GB", "St. Stephen's Day"): "Boxing Day", ("AU", "St. Stephen's Day"): "Boxing Day"}


def holidays_for(raw, region):
    """Nager.Date holidays that apply: national ones, plus the region's own (if it has one)."""
    return [{"date": h["date"], "name": LOCAL_NAMES.get((h.get("countryCode"), h["name"]), h["name"])}
            for h in raw if h.get("global") or (region and region in (h.get("counties") or []))]


def rate(rates, currency):
    """Units of currency per £1 (None if unknown). AED comes via its peg to the dollar."""
    r = rates.get("rates", {})
    if currency == "GBP":
        return 1.0
    if currency == "AED":
        return r["USD"] * AED_PER_USD if "USD" in r else None
    return r.get(currency)


def km_between(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = (math.sin((p2 - p1) / 2) ** 2 +
         math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2)
    return 6371 * 2 * math.asin(math.sqrt(a))


def nearby_quakes(geojson, lat, lon, km=QUAKE_KM, min_mag=QUAKE_MIN_MAG):
    """Quakes of min_mag+ within km, biggest first: [{"mag", "place", "km", "time"}]."""
    out = []
    for f in geojson.get("features", []):
        p, (qlon, qlat, _) = f["properties"], f["geometry"]["coordinates"]
        if (p.get("mag") or 0) < min_mag:
            continue
        d = km_between(lat, lon, qlat, qlon)
        if d <= km:
            out.append({"mag": p["mag"], "place": p.get("place") or "", "km": round(d),
                        "time": datetime.fromtimestamp(p["time"] / 1000, UTC)})
    return sorted(out, key=lambda e: -e["mag"])


# ── Sums ──────────────────────────────────────────────────────────────────────

SYNODIC = 29.530588853
NEW_MOON = datetime(2000, 1, 6, 18, 14, tzinfo=UTC)
PHASES = ["New moon", "Waxing crescent", "First quarter", "Waxing gibbous",
          "Full moon", "Waning gibbous", "Last quarter", "Waning crescent"]


def moon(when):
    """(age as a fraction of the cycle 0..1, fraction lit 0..1, phase name)."""
    age = ((when - NEW_MOON).total_seconds() / 86400 / SYNODIC) % 1
    lit = (1 - math.cos(2 * math.pi * age)) / 2
    return age, lit, PHASES[int((age * 8) + 0.5) % 8]


def call_window(now, tz, home, awake=AWAKE):
    """When it's daytime (awake hours) in both places today, in home time.

    Returns (good_now, (start, end) as home-time datetimes, or None if the days don't overlap).
    """
    def awake_span(zone, day):
        start = datetime(day.year, day.month, day.day, awake[0], tzinfo=zone)
        return start, start + timedelta(hours=awake[1] - awake[0])

    h0, h1 = awake_span(home, now.astimezone(home).date())
    best = None
    for shift in (-1, 0, 1):                    # their awake day that overlaps ours
        c0, c1 = awake_span(tz, (now.astimezone(tz) + timedelta(days=shift)).date())
        s, e = max(h0, c0), min(h1, c1)
        if s < e and (best is None or e - s > best[1] - best[0]):
            best = (s, e)
    c0, c1 = awake_span(tz, now.astimezone(tz).date())
    good = h0 <= now < h1 and c0 <= now < c1
    return good, (best[0].astimezone(home), best[1].astimezone(home)) if best else None


def exchange_status(now, ex, holiday_today=False):
    """{"state": "open"|"closed"|"break", "until": datetime of the next change (aware, local)}.

    Mon-Fri trading sessions only; exchange holiday calendars differ from public holidays,
    so on a public holiday the caller just says so rather than guessing.
    """
    from zoneinfo import ZoneInfo
    zone = ZoneInfo(ex["tz"])
    local = now.astimezone(zone)

    def at(day, hhmm):
        h, m = map(int, hhmm.split(":"))
        return datetime(day.year, day.month, day.day, h, m, tzinfo=zone)

    if local.weekday() < 5:
        sessions = [(at(local, a), at(local, b)) for a, b in ex["sessions"]]
        for i, (a, b) in enumerate(sessions):
            if a <= local < b:
                return {"state": "open", "until": b}
            if i and sessions[i - 1][1] <= local < a:
                return {"state": "break", "until": a}
        if local < sessions[0][0]:
            return {"state": "closed", "until": sessions[0][0]}
    day = local + timedelta(days=1)
    while day.weekday() >= 5:
        day += timedelta(days=1)
    return {"state": "closed", "until": at(day, ex["sessions"][0][0])}


AQI = [(50, "Good"), (100, "Moderate"), (150, "Unhealthy for some"), (200, "Unhealthy"),
       (300, "Very unhealthy"), (10 ** 6, "Hazardous")]
UV = [(3, "Low"), (6, "Moderate"), (8, "High"), (11, "Very high"), (10 ** 6, "Extreme")]


def band(value, bands):
    return next(name for limit, name in bands if (value or 0) < limit) if value is not None else ""


# ── Fetching and caching ──────────────────────────────────────────────────────

def http_get(url, timeout=25):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def explain(err):
    if isinstance(err, urllib.error.HTTPError):
        return f"HTTP {err.code}"
    if isinstance(err, urllib.error.URLError):
        return "offline"
    return str(err)[:60] or type(err).__name__


class CityStore:
    """Keeps the open city's data fresh, in a background thread, cached on disk.

    get(city, kind) returns {"fetched": epoch seconds, "data": ...} or None.
    """

    def __init__(self, cities, cache_dir=CACHE_DIR, fetch=http_get, clock=time.time):
        self.cities, self.cache_dir, self.fetch, self.clock = cities, cache_dir, fetch, clock
        self.lock = threading.Lock()
        self.wake = threading.Event()
        self.active = None
        self.entries, self.errors, self.retry_at = {}, {}, {}

    def key(self, city, kind):
        return kind if kind in SHARED else f"{kind}-{slug(city['name'])}"

    def get(self, city, kind):
        k = self.key(city, kind)
        with self.lock:
            if k not in self.entries:
                try:
                    with open(os.path.join(self.cache_dir, k + ".json")) as f:
                        self.entries[k] = json.load(f)
                except (OSError, ValueError):
                    self.entries[k] = None
            return self.entries[k]

    def error(self, city, kind):
        with self.lock:
            return self.errors.get(self.key(city, kind))

    def set_active(self, city):
        """The city on screen (or None for the clocks); fetching starts straight away."""
        self.active = city
        self.wake.set()

    def refresh(self, city):
        """Fetch whatever the city needs that's out of date. Returns how many fetches were tried."""
        tried = 0
        for kind in kinds(city):
            k, now = self.key(city, kind), self.clock()
            entry = self.get(city, kind)
            if (entry and now - entry["fetched"] < EVERY[kind]) or now < self.retry_at.get(k, 0):
                continue
            tried += 1
            try:
                data = self.parse(city, kind, self.fetch(urls(city, kind)))
            except Exception as e:              # network, HTTP, bad data: keep what we had
                with self.lock:
                    self.errors[k] = explain(e)
                self.retry_at[k] = now + RETRY_AFTER
                log(f"{city['name']} {kind}: {explain(e)} ({e})")
                continue
            entry = {"fetched": now, "data": data}
            with self.lock:
                self.entries[k] = entry
                self.errors.pop(k, None)
            self._save(k, entry)
        return tried

    @staticmethod
    def parse(city, kind, raw):
        if kind == "news":
            return parse_news(raw)
        data = json.loads(raw)
        if kind == "holidays":
            return holidays_for(data, city["holidays"][1])
        if kind == "quakes":                    # keep only what any city page could show
            return {"features": [f for f in data.get("features", [])
                                 if (f["properties"].get("mag") or 0) >= QUAKE_MIN_MAG]}
        return data

    def _save(self, k, entry):
        try:
            os.makedirs(self.cache_dir, exist_ok=True)
            tmp = os.path.join(self.cache_dir, k + ".json.tmp")
            with open(tmp, "w") as f:
                json.dump(entry, f)
            os.replace(tmp, os.path.join(self.cache_dir, k + ".json"))
        except OSError as e:
            log("can't cache:", e)

    def run(self):
        while True:
            city = self.active
            if city:
                self.refresh(city)
            self.wake.wait(30)
            self.wake.clear()

    def start(self):
        threading.Thread(target=self.run, daemon=True).start()
        return self
