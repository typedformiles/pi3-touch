#!/usr/bin/env python3
"""Grid carbon data for the Energy & Grid app: fetching, caching and the sums (no pygame here).

Everything comes from the Carbon Intensity API (NESO, free, no key): half-hourly carbon
intensity (gCO2/kWh) and generation mix, now and forecast 48 hours ahead - for a region
(looked up by postcode district) or for Great Britain as a whole. Fetched data is cached
to disk, so the screen keeps working when the network doesn't.

On the Pi, check the places and what the API says now with:
    python3 /opt/pi3-touch/apps/energy/energy_data.py --check
"""
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
PLACES = os.path.join(HERE, "places.json")
LOCAL = ZoneInfo("Europe/London")
UTC = timezone.utc
API = "https://api.carbonintensity.org.uk"
CACHE_DIR = os.environ.get("STATE_DIRECTORY", "/var/lib/pi3-touch-energy")
FETCH_EVERY = 30 * 60           # the API works in half hours
RETRY_AFTER = 2 * 60            # after a failed fetch
HALF_HOUR = timedelta(minutes=30)
USER_AGENT = "pi3-touch-energy/1.0 (+https://github.com/typedformiles/pi3-touch)"

# The API's bands, cleanest first. It sets each half hour's band itself (the limits fall a
# little every year); averages take the band of the half hour closest in value.
BANDS = ["very low", "low", "moderate", "high", "very high"]
# Fuels cleanest first; "zero carbon" is how NESO groups wind, solar, hydro and nuclear
FUELS = ["wind", "solar", "hydro", "nuclear", "biomass", "imports", "other", "gas", "coal"]
ZERO_CARBON = {"wind", "solar", "hydro", "nuclear"}
FOSSIL = {"gas", "coal"}


def log(*a):
    print("[energy]", *a, flush=True)


def slug(name):
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def load_config(path=PLACES):
    with open(path) as f:
        return json.load(f)


# ── The sums ──────────────────────────────────────────────────────────────────

def api_time(s):
    """The API's "2026-10-07T15:30Z" as an aware UTC datetime."""
    return datetime.strptime(s, "%Y-%m-%dT%H:%MZ").replace(tzinfo=UTC)


def half_hour_start(t):
    return t.replace(minute=t.minute - t.minute % 30, second=0, microsecond=0)


def parse_periods(raw):
    """API half hours -> [{"start", "end", "forecast", "actual", "index", "mix"}] in time order.

    mix is {fuel: percent} (regional data has one per half hour) or None.
    """
    out = {}
    for p in raw:
        try:
            start, end = api_time(p["from"]), api_time(p["to"])
            i = p["intensity"]
        except (KeyError, TypeError, ValueError):
            continue
        if i.get("forecast") is None:
            continue
        mix = {m["fuel"]: m["perc"] for m in p.get("generationmix") or []} or None
        out[start] = {"start": start, "end": end, "forecast": i["forecast"], "actual": i.get("actual"),
                      "index": i.get("index"), "mix": mix}
    return [out[t] for t in sorted(out)]


def current(periods, now):
    """The half hour we're in, or None."""
    return next((p for p in periods if p["start"] <= now < p["end"]), None)


def value(p):
    """A half hour's intensity: measured if the API has it yet, else forecast."""
    return p["actual"] if p.get("actual") is not None else p["forecast"]


def band(g, periods):
    """The band for an intensity: that of the half hour closest in value."""
    named = [p for p in periods if p.get("index") in BANDS]
    if not named:
        return None
    return min(named, key=lambda p: abs(p["forecast"] - g))["index"]


def ahead(periods, now):
    """The half hours from the one we're in onwards."""
    start = half_hour_start(now)
    return [p for p in periods if p["start"] >= start]


def windows(periods, now, hours):
    """Every run of `hours` back-to-back half hours from now on: [{"start", "end", "avg"}]."""
    n = max(1, round(hours * 2))
    ps = ahead(periods, now)
    out = []
    for i in range(len(ps) - n + 1):
        run = ps[i:i + n]
        if all(b["start"] == a["end"] for a, b in zip(run, run[1:])):
            out.append({"start": run[0]["start"], "end": run[-1]["end"],
                        "avg": sum(p["forecast"] for p in run) / n})
    return out


def best_window(periods, now, hours, within=timedelta(hours=24), worst=False):
    """The cleanest (or dirtiest) `hours` that finish within `within` from now, or None."""
    limit = half_hour_start(now) + within
    ws = [w for w in windows(periods, now, hours) if w["end"] <= limit]
    if not ws:
        return None
    pick = (max if worst else min)(ws, key=lambda w: w["avg"])
    return dict(pick, band=band(pick["avg"], periods))


def best_by_day(periods, now, hours):
    """[(local date, cleanest window starting that day)] for today onwards, as far as the data goes."""
    by_day = {}
    for w in windows(periods, now, hours):
        day = w["start"].astimezone(LOCAL).date()
        if day not in by_day or w["avg"] < by_day[day]["avg"]:
            by_day[day] = w
    return [(d, dict(w, band=band(w["avg"], periods))) for d, w in sorted(by_day.items())]


def trend(periods, now, hours=3):
    """("falling" | "rising" | "steady", average over the next `hours`) or None.

    Compares the next few hours with the half hour we're in; within 10% counts as steady.
    """
    cur = current(periods, now)
    nxt = [p for p in periods if p["start"] >= (cur or {}).get("end", now)][:round(hours * 2)]
    if not cur or not nxt:
        return None
    avg = sum(p["forecast"] for p in nxt) / len(nxt)
    change = (avg - cur["forecast"]) / max(cur["forecast"], 1)
    return ("falling" if change < -0.1 else "rising" if change > 0.1 else "steady"), avg


def groups(mix):
    """{"zero carbon", "fossil", "other"} percentages of a generation mix."""
    z = sum(v for f, v in mix.items() if f in ZERO_CARBON)
    fossil = sum(v for f, v in mix.items() if f in FOSSIL)
    return {"zero carbon": z, "fossil": fossil, "other": max(0.0, sum(mix.values()) - z - fossil)}


def by_share(mix):
    """[(fuel, percent)] biggest first, ties in FUELS order; fuels the API adds later go last."""
    order = {f: i for i, f in enumerate(FUELS)}
    return sorted(mix.items(), key=lambda kv: (-kv[1], order.get(kv[0], len(order))))


# ── Fetching and caching ──────────────────────────────────────────────────────

def urls(place, now):
    """What to fetch for a place: its 48-hour forecast (+ the mix now, for Great Britain)."""
    t = half_hour_start(now.astimezone(UTC)).strftime("%Y-%m-%dT%H:%MZ")
    if place.get("postcode"):
        return {"forecast": f"{API}/regional/intensity/{t}/fw48h/postcode/{place['postcode']}"}
    return {"forecast": f"{API}/intensity/{t}/fw48h", "mix": f"{API}/generation"}


def http_json(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def explain(err):
    if isinstance(err, urllib.error.HTTPError):
        return {400: "bad postcode?", 429: "too many requests"}.get(err.code, f"HTTP {err.code}")
    if isinstance(err, urllib.error.URLError):
        return "offline"
    return str(err)[:60] or type(err).__name__


class Store:
    """Keeps every place's carbon data fresh, cached on disk.

    get(place) returns {"fetched": epoch seconds, "region", "periods" (raw), "mix"} or None.
    Fetching happens in a background thread (start()), so switching places is instant.
    """

    def __init__(self, places, cache_dir=CACHE_DIR, fetch=http_json, clock=time.time):
        self.places, self.cache_dir, self.fetch, self.clock = places, cache_dir, fetch, clock
        self.lock = threading.Lock()
        self.entries, self.errors, self.retry_at = {}, {}, {}
        for place in places:
            try:
                with open(self.path(place)) as f:
                    self.entries[slug(place["name"])] = json.load(f)
            except (OSError, ValueError):
                pass

    def path(self, place):
        return os.path.join(self.cache_dir, f"carbon-{slug(place['name'])}.json")

    def get(self, place):
        with self.lock:
            return self.entries.get(slug(place["name"]))

    def error(self, place):
        with self.lock:
            return self.errors.get(slug(place["name"]))

    def refresh(self):
        """One pass: fetch whatever is due. Returns how many places were fetched (or tried)."""
        return sum(self._maybe(place) for place in self.places)

    def _maybe(self, place):
        k, now = slug(place["name"]), self.clock()
        entry = self.get(place)
        if (entry and now - entry["fetched"] < FETCH_EVERY) or now < self.retry_at.get(k, 0):
            return 0
        try:
            entry = dict(self._fetch(place, datetime.fromtimestamp(now, UTC)), fetched=now)
        except Exception as e:                  # network, HTTP, bad JSON: keep the old data
            with self.lock:
                self.errors[k] = explain(e)
            self.retry_at[k] = now + RETRY_AFTER
            log(f"{place['name']}: {explain(e)} ({e})")
            return 1
        with self.lock:
            self.entries[k] = entry
            self.errors.pop(k, None)
        self._save(place, entry)
        return 1

    def _fetch(self, place, now):
        u = urls(place, now)
        data = self.fetch(u["forecast"])["data"]
        if place.get("postcode"):                # regional: one region, its half hours inside
            data = data[0] if isinstance(data, list) else data
            entry = {"region": data.get("shortname") or place["name"], "periods": data["data"], "mix": None}
        else:
            entry = {"region": "Great Britain", "periods": data, "mix": self.fetch(u["mix"])["data"]}
        if not parse_periods(entry["periods"]):
            raise ValueError("no half hours in the reply")
        return entry

    def _save(self, place, entry):
        try:
            os.makedirs(self.cache_dir, exist_ok=True)
            tmp = self.path(place) + ".tmp"
            with open(tmp, "w") as f:
                json.dump(entry, f)
            os.replace(tmp, self.path(place))
        except OSError as e:
            log("can't cache:", e)

    def run(self, every=30):
        while True:
            self.refresh()
            time.sleep(every)

    def start(self):
        threading.Thread(target=self.run, daemon=True).start()
        return self


def parse_entry(entry):
    """A cached entry -> {"region", "periods", "mix" (now, {fuel: %}), "mix_time"}."""
    periods = parse_periods(entry["periods"])
    mix, mix_time = None, None
    if entry.get("mix"):                         # Great Britain: the separate mix-now reply
        mix = {m["fuel"]: m["perc"] for m in entry["mix"]["generationmix"]}
        mix_time = api_time(entry["mix"]["from"])
    return {"region": entry.get("region"), "periods": periods, "mix": mix, "mix_time": mix_time}


def mix_now(parsed, now):
    """(mix, start of its half hour) for now: the region's own, or Great Britain's latest."""
    cur = current(parsed["periods"], now)
    if cur and cur["mix"]:
        return cur["mix"], cur["start"]
    return parsed["mix"], parsed["mix_time"]


def check():
    """Command-line check of every place against the live API (run on the Pi or a Mac)."""
    ok = True
    now = datetime.now(UTC)
    for place in load_config()["places"]:
        try:
            store = Store([place], cache_dir="/nonexistent")
            data = parse_entry(dict(store._fetch(place, now), fetched=0))
            cur = current(data["periods"], now)
            mix, _ = mix_now(data, now)
            best = best_window(data["periods"], now, 3)
            print(f"{place['name']} ({data['region']}): {value(cur)} g {cur['index']} now, "
                  f"zero carbon {groups(mix)['zero carbon']:.0f}%, "
                  f"greenest 3 h from {best['start'].astimezone(LOCAL):%a %H:%M} ({best['avg']:.0f} g)")
        except Exception as e:
            print(f"{place['name']}: FAILED - {explain(e)} ({e})")
            ok = False
    return 0 if ok else 1


if __name__ == "__main__":
    if sys.argv[1:] == ["--check"]:
        sys.exit(check())
    print(__doc__)
