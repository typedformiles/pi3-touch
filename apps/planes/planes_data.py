#!/usr/bin/env python3
"""Aircraft overhead for the Planes app: fetching, caching and the sums (no pygame here).

Positions come from community ADS-B networks (free, no key), every aircraft within 25 nm:
adsb.fi's open data API (one call a second allowed) and, if that fails, adsb.lol (500 calls
a day, so it's only a fallback). Routes, airline names, aircraft details and photos come
from adsbdb.com, looked up a couple at a time and cached on disk - a callsign's route for a
day, an aircraft for a month.

On the Pi, check what's overhead now with:
    python3 /opt/pi3-touch/apps/planes/planes_data.py --check
"""
import json
import math
import os
import re
import sys
import threading
import time
import gzip
import http.client
import urllib.error
import urllib.parse

HERE = os.path.dirname(os.path.realpath(__file__))
CONFIG = os.path.join(HERE, "planes.json")
CACHE_DIR = os.environ.get("STATE_DIRECTORY", "/var/lib/pi3-touch-planes")
USER_AGENT = "pi3-touch-planes/1.0 (+https://github.com/typedformiles/pi3-touch)"
SOURCES = [
    ("adsb.fi", "https://opendata.adsb.fi/api/v2/lat/{lat}/lon/{lon}/dist/{nm}", None),
    ("adsb.lol", "https://api.adsb.lol/v2/point/{lat}/{lon}/{nm}", 400),    # (name, url, calls a day)
]
ADSBDB = "https://api.adsbdb.com/v0"
QUERY_NM = 25                   # always fetch this far, so zooming out is instant
EVERY_ACTIVE = 5                # seconds between position fetches while someone's looking
EVERY_IDLE = 30                 # ...and once the screen has dimmed
RETRY_AFTER = 30
LOOKUPS_PER_PASS = 2            # adsbdb calls per fetch, nearest aircraft first
ROUTE_KEEP = 24 * 3600
AIRCRAFT_KEEP = 30 * 24 * 3600
TRAIL_SECONDS = 180             # how much of each aircraft's track to remember
STALE_POSITION = 60             # seconds since a position report before it's dropped
PHOTOS_KEPT = 30
SAVE_EVERY = 60                 # seconds between writes of the lookup caches
NM_PER_DEG = 60.0
TRANSITION = 6000               # ft: UK transition altitude
FT_PER_HPA = 27.3
EMERGENCY = {"7500": "Hijack", "7600": "Radio failure", "7700": "Emergency"}
POINTS = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]


def log(*a):
    print("[planes]", *a, flush=True)


def load_config(path=CONFIG):
    with open(path) as f:
        return json.load(f)


# ── The sums ──────────────────────────────────────────────────────────────────

def offset(home, lat, lon):
    """(east, north) in nautical miles from home - flat-earth, fine within 25 nm."""
    return ((lon - home["lon"]) * NM_PER_DEG * math.cos(math.radians(home["lat"])),
            (lat - home["lat"]) * NM_PER_DEG)


def distance_bearing(home, lat, lon):
    e, n = offset(home, lat, lon)
    return math.hypot(e, n), math.degrees(math.atan2(e, n)) % 360


def compass(bearing):
    return POINTS[round(bearing / 45) % 8]


def callsign(flight):
    """The callsign, or "" for none or a placeholder (some transponders send "@@@@@@@@")."""
    flight = (flight or "").strip().upper()
    return flight if re.fullmatch(r"[A-Z0-9]{2,8}", flight) else ""


def local_qnh(aircraft):
    """Today's sea-level pressure (hPa) here: the middle of what the aircraft around have
    set on their altimeters, or None."""
    sets = sorted(a["nav_qnh"] for a in aircraft if isinstance(a.get("nav_qnh"), (int, float))
                  and 950 < a["nav_qnh"] < 1060)
    return sets[len(sets) // 2] if sets else None


def height(alt_baro, qnh):
    """A transponder's altitude (ft, against the standard 1013.25 hPa) as height above sea
    level: below the UK's 6,000 ft transition altitude, corrected by ~27 ft per hPa of the
    day's pressure; above it, flight levels are what everyone uses, so it's left alone."""
    if qnh and alt_baro < TRANSITION:
        alt_baro += (qnh - 1013.25) * FT_PER_HPA
    return max(0, round(alt_baro / 25) * 25)


def parse(raw, home, now):
    """An ADS-B reply -> {hex: aircraft} with: hex, callsign, reg, type, desc, alt (ft, or
    None on the ground), ground, speed (kt), track, rate (ft/min), lat, lon, dist, bearing,
    squawk, emergency. Aircraft without a recent position are left out."""
    out = {}
    aircraft = raw.get("ac") or raw.get("aircraft") or []
    qnh = local_qnh(aircraft)
    for a in aircraft:
        if a.get("lat") is None or a.get("lon") is None or (a.get("seen_pos") or 0) > STALE_POSITION:
            continue
        alt = a.get("alt_baro")
        ground = alt == "ground"
        dist, bearing = distance_bearing(home, a["lat"], a["lon"])
        squawk = a.get("squawk") or ""
        out[a["hex"]] = {
            "hex": a["hex"], "callsign": callsign(a.get("flight")), "reg": a.get("r") or "",
            "type": a.get("t") or "", "desc": a.get("desc") or "",
            "alt": None if ground or not isinstance(alt, (int, float)) else height(alt, qnh), "ground": ground,
            "speed": a.get("gs"), "track": a.get("track", a.get("true_heading")),
            "rate": a.get("baro_rate", a.get("geom_rate")), "lat": a["lat"], "lon": a["lon"],
            "dist": dist, "bearing": bearing, "squawk": squawk,
            "emergency": EMERGENCY.get(squawk) or ((a.get("emergency") or "none") != "none" and "Emergency") or None,
            "seen": now}
    return out


def nice_type(desc, code):
    """"AIRBUS A-320neo" -> "Airbus A320neo", "BOEING 777-300ER" -> "Boeing 777-300ER"."""
    if not desc:
        return code or ""
    words = [w.title() if w.isalpha() and w.isupper() and len(w) > 3 else w for w in desc.split()]
    return re.sub(r"\b([A-Z]+)-(?=\d)", r"\1", " ".join(words))       # A-320neo -> A320neo


def climb(rate):
    """"up", "down" or "" from a vertical rate in ft/min."""
    if rate is None:
        return ""
    return "up" if rate > 300 else "down" if rate < -300 else ""


def place(airport):
    """An adsbdb airport -> "Amsterdam (AMS)"."""
    if not airport:
        return ""
    town = airport.get("municipality") or airport.get("name") or "?"
    code = airport.get("iata_code") or airport.get("icao_code") or ""
    return f"{town} ({code})" if code else town


def visible(planes, range_nm, show_ground=False):
    """The aircraft within range, nearest first."""
    return sorted((p for p in planes.values() if p["dist"] <= range_nm and (show_ground or not p["ground"])),
                  key=lambda p: p["dist"])


# ── Fetching and caching ──────────────────────────────────────────────────────

_connections = {}


def http_get(url, timeout=12):
    """GET over a kept-open connection per server: a fresh TLS handshake for every call
    costs a Pi 3 more than everything else this app does. Raises urllib's errors."""
    u = urllib.parse.urlsplit(url)
    path = u.path + (f"?{u.query}" if u.query else "")
    for attempt in (1, 2):                      # a kept connection may have been closed
        conn = _connections.get(u.netloc)
        if conn is None:
            conn = _connections[u.netloc] = http.client.HTTPSConnection(u.netloc, timeout=timeout)
        try:
            conn.request("GET", path, headers={"User-Agent": USER_AGENT, "Accept-Encoding": "gzip"})
            r = conn.getresponse()
            body = r.read()
        except (OSError, http.client.HTTPException) as e:
            conn.close()
            _connections.pop(u.netloc, None)
            if attempt == 2:
                raise urllib.error.URLError(e)
            continue
        if r.status >= 400:
            raise urllib.error.HTTPError(url, r.status, r.reason, r.headers, None)
        if r.status >= 300:                     # not expected from these APIs
            raise urllib.error.HTTPError(url, r.status, "redirect", r.headers, None)
        return gzip.decompress(body) if r.getheader("Content-Encoding") == "gzip" else body


def explain(err):
    if isinstance(err, urllib.error.HTTPError):
        return {429: "too many requests", 403: "refused"}.get(err.code, f"HTTP {err.code}")
    if isinstance(err, urllib.error.URLError):
        return "offline"
    return str(err)[:60] or type(err).__name__


class Store:
    """Keeps the aircraft around home fresh, plus their routes, details and photos.

    planes() is {hex: aircraft} from the latest fetch; trail(hex) the positions it has been
    seen at; route(callsign) / aircraft(hex) the adsbdb lookups (None until looked up, {}
    when adsbdb doesn't know); photo(hex) the photo's bytes once fetched. Set `idle` to
    fetch less often, and `want_photo` to an aircraft's hex to fetch its photo.
    """

    def __init__(self, config, cache_dir=CACHE_DIR, get=http_get, clock=time.time):
        self.home = config["home"]
        self.cache_dir, self.get, self.clock = cache_dir, get, clock
        self.lock = threading.Lock()
        self.idle = False
        self.want_photo = None
        self._planes, self.fetched, self.source = {}, None, None
        self.trails = {}
        self.err, self.retry_at = None, 0
        self.used = {}                          # source -> (day, calls)
        self.routes = self._load("routes")
        self.craft = self._load("aircraft")
        self.photos = {}
        self.dirty = set()
        self.saved = 0

    # ── reading ──
    def planes(self):
        with self.lock:
            return self._planes

    def trail(self, hex_):
        with self.lock:
            return list(self.trails.get(hex_, []))

    def route(self, callsign):
        with self.lock:
            return (self.routes.get(callsign) or {}).get("data")

    def aircraft(self, hex_):
        with self.lock:
            return (self.craft.get(hex_) or {}).get("data")

    def photo(self, hex_):
        with self.lock:
            return self.photos.get(hex_)

    def error(self):
        with self.lock:
            return self.err

    def stamp(self):
        """Changes whenever anything new arrives - for knowing when to redraw."""
        with self.lock:
            return (self.fetched, len(self.routes), len(self.craft), len(self.photos), self.err)

    # ── fetching ──
    def refresh(self):
        """One pass: positions if due, then a few lookups. Returns True if it fetched positions."""
        now = self.clock()
        every = EVERY_IDLE if self.idle else EVERY_ACTIVE
        if (self.fetched and now - self.fetched < every) or now < self.retry_at:
            self._photo()                       # a tapped aircraft's photo shouldn't wait
            return False
        raw, source, err = None, None, None
        for name, url, per_day in SOURCES:
            if per_day and not self._spend(name, per_day, now):
                continue
            try:
                raw = json.loads(self.get(url.format(lat=self.home["lat"], lon=self.home["lon"], nm=QUERY_NM)))
                source = name
                break
            except Exception as e:              # network, HTTP, bad JSON: try the next source
                err = f"{name}: {explain(e)}"
                log(f"{name}: {explain(e)} ({e})")
        if raw is None:
            with self.lock:
                self.err = err or "no source left today"
            self.retry_at = now + RETRY_AFTER
            return False
        planes = parse(raw, self.home, now)
        with self.lock:
            self._planes, self.fetched, self.source, self.err = planes, now, source, None
            for h, p in planes.items():
                t = self.trails.setdefault(h, [])
                if not t or (t[-1][1], t[-1][2]) != (p["lat"], p["lon"]):
                    t.append((now, p["lat"], p["lon"]))
            for h in list(self.trails):
                self.trails[h] = [x for x in self.trails[h] if now - x[0] <= TRAIL_SECONDS]
                if not self.trails[h]:
                    del self.trails[h]
        self._lookups(now)
        return True

    def _spend(self, name, per_day, now):
        """Count a call against a source's daily allowance (UTC days). False when used up."""
        day = int(now // 86400)
        d, n = self.used.get(name, (day, 0))
        if d != day:
            n = 0
        if n >= per_day:
            return False
        self.used[name] = (day, n + 1)
        return True

    def _lookups(self, now):
        """Look up the nearest aircraft's route and details, and the wanted photo."""
        done = 0
        for p in sorted(self.planes().values(), key=lambda p: p["dist"]):
            if done >= LOOKUPS_PER_PASS:
                break
            if p["callsign"] and self._due(self.routes, p["callsign"], ROUTE_KEEP, now):
                self._lookup(self.routes, p["callsign"], f"{ADSBDB}/callsign/{p['callsign']}", "flightroute", now)
                done += 1
            if done < LOOKUPS_PER_PASS and self._due(self.craft, p["hex"], AIRCRAFT_KEEP, now):
                self._lookup(self.craft, p["hex"], f"{ADSBDB}/aircraft/{p['hex']}", "aircraft", now)
                done += 1
        self._photo()
        if now - self.saved >= SAVE_EVERY:
            self._save()
            self.saved = now

    @staticmethod
    def _due(cache, key, keep, now):
        hit = cache.get(key)
        return not hit or now - hit["t"] > keep

    def _lookup(self, cache, key, url, field, now):
        try:
            data = json.loads(self.get(url)).get("response")
            data = data.get(field) if isinstance(data, dict) else {}
        except urllib.error.HTTPError as e:
            if e.code not in (400, 404):        # adsbdb doesn't know it (or won't): remember that
                log(f"adsbdb {key}: {explain(e)}")
                return
            data = {}
        except Exception as e:
            log(f"adsbdb {key}: {explain(e)} ({e})")
            return
        with self.lock:
            cache[key] = {"t": now, "data": data or {}}
        self.dirty.add("routes" if cache is self.routes else "aircraft")

    def _photo(self):
        h = self.want_photo
        if not h or h in self.photos:
            return
        url = (self.aircraft(h) or {}).get("url_photo_thumbnail")
        if self.aircraft(h) is None:
            return                              # details not looked up yet
        data = b""
        if url:
            try:
                data = self.get(url)
            except Exception as e:
                log(f"photo {h}: {explain(e)}")
        with self.lock:
            self.photos[h] = data
            while len(self.photos) > PHOTOS_KEPT:
                self.photos.pop(next(iter(self.photos)))

    def _path(self, name):
        return os.path.join(self.cache_dir, f"{name}.json")

    def _load(self, name):
        try:
            with open(self._path(name)) as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def _save(self):
        for name in self.dirty:
            cache = self.routes if name == "routes" else self.craft
            now = self.clock()
            keep = ROUTE_KEEP if name == "routes" else AIRCRAFT_KEEP
            with self.lock:
                fresh = {k: v for k, v in cache.items() if now - v["t"] <= keep}
            try:
                os.makedirs(self.cache_dir, exist_ok=True)
                with open(self._path(name) + ".tmp", "w") as f:
                    json.dump(fresh, f)
                os.replace(self._path(name) + ".tmp", self._path(name))
            except OSError as e:
                log("can't cache:", e)
        self.dirty = set()

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
    """Command-line check against the live APIs (run on the Pi or a Mac)."""
    cfg = load_config()
    store = Store(cfg, cache_dir="/nonexistent")
    store._save = lambda: None
    store.refresh()
    if store.error():
        print("FAILED -", store.error())
        return 1
    planes = visible(store.planes(), max(cfg["ranges_nm"]), cfg.get("show_ground", False))
    print(f"{len(store.planes())} aircraft within {QUERY_NM} nm of {cfg['home']['name']} (from {store.source}), "
          f"{len(planes)} airborne within {max(cfg['ranges_nm'])} nm")
    for _ in range(3):
        store._lookups(store.clock())
    for p in planes[:5]:
        r = store.route(p["callsign"]) or {}
        route = f"{place(r.get('origin'))} -> {place(r.get('destination'))}" if r else "route unknown"
        print(f"  {p['callsign'] or p['hex']:8} {nice_type(p['desc'], p['type']):18} {p['alt'] or 0:>6} ft "
              f"{p['dist']:4.1f} nm {compass(p['bearing']):2}  {route}")
    return 0


if __name__ == "__main__":
    if sys.argv[1:] == ["--check"]:
        sys.exit(check())
    print(__doc__)
