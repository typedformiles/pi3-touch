#!/usr/bin/env python3
"""Fantasy Premier League data for the FPL Live app: fetching, caching and the sums (no pygame here).

Everything comes from FPL's public API (fantasy.premierleague.com/api, no key or login; it
only reads): the game's players, teams and gameweeks, one team's picks and history, the
live points of every player, the fixtures and that team's mini-league tables. Each reply is
cut down to what the screens need and cached to disk, so the screen keeps working when the
network doesn't. How often each part is fetched follows the matches: every minute while a
game is on, every few minutes while bonus points are due, hourly otherwise.

On the Pi, check the team and what the API says now with:
    python3 /opt/pi3-touch/apps/fpl/fpl_data.py --check
"""
import gzip
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.realpath(__file__))
CONFIG = os.path.join(HERE, "fpl.json")
LOCAL = ZoneInfo("Europe/London")
UTC = timezone.utc
API = "https://fantasy.premierleague.com/api"
CACHE_DIR = os.environ.get("STATE_DIRECTORY", "/var/lib/pi3-touch-fpl")
USER_AGENT = "pi3-touch-fpl/1.0 (+https://github.com/typedformiles/pi3-touch)"
RETRY_AFTER = 2 * 60            # after a failed fetch
MINUTE, HOUR = 60, 3600
MATCH_LENGTH = 2 * HOUR + 15 * 60   # kick-off to final whistle, with time to spare
MAX_LEAGUES = 4                 # buttons across the Leagues page
POSITIONS = {1: "GKP", 2: "DEF", 3: "MID", 4: "FWD"}
# Fewest players per position in a starting XI (an auto-sub must keep to these)
MIN_XI = {1: 1, 2: 3, 3: 2, 4: 1}
BONUS = {1: 3, 2: 2, 3: 1}     # bonus points by BPS rank in a match (ties share the rank)


def log(*a):
    print("[fpl]", *a, flush=True)


def load_config(path=CONFIG):
    with open(path) as f:
        return json.load(f)


def api_time(s):
    """The API's "2026-10-17T10:00:00Z" as an aware UTC datetime (None stays None)."""
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC) if s else None


# ── Cutting the API's replies down to what the screens use ────────────────────

def slim_static(raw):
    """bootstrap-static (1.8 MB) -> gameweeks, teams and players."""
    return {
        "events": [{"id": e["id"], "name": e["name"], "deadline": e["deadline_time"],
                    "finished": e["finished"], "checked": e["data_checked"],
                    "average": e.get("average_entry_score"), "highest": e.get("highest_score")}
                   for e in raw["events"]],
        "teams": {str(t["id"]): {"name": t["name"], "short": t["short_name"]} for t in raw["teams"]},
        "players": {str(p["id"]): {"name": p["web_name"], "team": p["team"], "pos": p["element_type"],
                                   "cost": p["now_cost"], "change": p.get("cost_change_event", 0),
                                   "chance": p.get("chance_of_playing_next_round"),
                                   "status": p.get("status", "a"), "news": p.get("news", "")}
                    for p in raw["elements"]},
        "managers": raw.get("total_players"),
    }


def slim_entry(raw):
    return {"name": raw["name"], "manager": f"{raw['player_first_name']} {raw['player_last_name']}",
            "total": raw.get("summary_overall_points"), "rank": raw.get("summary_overall_rank"),
            "gw_points": raw.get("summary_event_points"), "gw_rank": raw.get("summary_event_rank"),
            "leagues": [{"id": lg["id"], "name": lg["name"], "type": lg.get("league_type"),
                         "rank": lg.get("entry_rank"), "last_rank": lg.get("entry_last_rank"),
                         "size": lg.get("rank_count")}
                        for lg in raw.get("leagues", {}).get("classic", [])]}


def slim_history(raw):
    return [{"event": h["event"], "points": h["points"], "total": h["total_points"],
             "rank": h.get("overall_rank"), "cost": h.get("event_transfers_cost", 0)}
            for h in raw.get("current", [])]


def slim_picks(raw, gw):
    h = raw.get("entry_history") or {}
    return {"event": gw, "chip": raw.get("active_chip"),
            "picks": [{"id": p["element"], "slot": p["position"], "mult": p["multiplier"],
                       "c": p["is_captain"], "v": p["is_vice_captain"]} for p in raw["picks"]],
            "cost": h.get("event_transfers_cost", 0), "bank": h.get("bank"), "value": h.get("value"),
            "transfers": h.get("event_transfers", 0)}


def slim_fixture(f):
    stats = {s["identifier"]: s for s in f.get("stats") or []}
    bonus = stats.get("bonus") or {}
    bps = stats.get("bps") or {}
    return {"id": f["id"], "event": f.get("event"), "kickoff": f.get("kickoff_time"),
            "h": f["team_h"], "a": f["team_a"], "hs": f.get("team_h_score"), "as": f.get("team_a_score"),
            "started": bool(f.get("started")), "ended": bool(f.get("finished_provisional")),
            "minutes": f.get("minutes") or 0, "hd": f.get("team_h_difficulty"), "ad": f.get("team_a_difficulty"),
            "bonus_in": bool(bonus.get("h") or bonus.get("a")),
            "bps": [[s["element"], s["value"]] for s in (bps.get("h") or []) + (bps.get("a") or [])]}


def slim_live(raw, fixtures, gw):
    """event/<gw>/live (470 KB) + that gameweek's fixtures -> points and minutes per player."""
    players = {}
    for e in raw.get("elements", []):
        st = e.get("stats") or {}
        players[str(e["id"])] = {"pts": st.get("total_points", 0), "min": st.get("minutes", 0),
                                 "fx": [x["fixture"] for x in e.get("explain") or []]}
    return {"event": gw, "players": players, "fixtures": [slim_fixture(f) for f in fixtures]}


def slim_future(raw):
    """fixtures/?future=1 -> the fixtures still to come, for the difficulty grid."""
    return [{k: v for k, v in slim_fixture(f).items() if k not in ("bps", "bonus_in")}
            for f in raw if f.get("event")]


def slim_league(pages, team_id):
    """leagues-classic/<id>/standings pages -> the table, with the team's own row marked."""
    first = pages[0]
    rows = {}
    for page in pages:
        for r in page["standings"]["results"]:
            rows[r["rank_sort"]] = {"rank": r["rank"], "last": r.get("last_rank"), "team": r["entry_name"],
                                    "manager": r["player_name"], "gw": r.get("event_total"),
                                    "total": r["total"], "me": r["entry"] == team_id}
    return {"id": first["league"]["id"], "name": first["league"]["name"],
            "updated": first.get("last_updated_data"), "rows": [rows[k] for k in sorted(rows)]}


# ── The sums ──────────────────────────────────────────────────────────────────

def gameweeks(static, now):
    """(current gameweek id or None, next gameweek or None) from the deadlines: the current
    one is the last whose deadline has passed, whatever the cached flags say."""
    cur, nxt = None, None
    for e in static["events"]:
        if api_time(e["deadline"]) <= now:
            cur = e["id"]
        elif nxt is None:
            nxt = e
    return cur, nxt


def event(static, gw):
    return next((e for e in static["events"] if e["id"] == gw), None)


def phase(fixtures, now):
    """Where a gameweek's matches are: "live" (a game on), "bonus" (a game over, its bonus
    points not in yet), "waiting" (more kick-offs to come), "done", or None (no fixtures)."""
    if not fixtures:
        return None
    if any(not f["ended"] and (f["started"] or kicked_off(f, now)) for f in fixtures):
        return "live"
    if any(f["ended"] and not f["bonus_in"] for f in fixtures):
        return "bonus"
    if any(not f["started"] for f in fixtures):
        return "waiting"
    return "done"


def kicked_off(f, now):
    """Kick-off time passed in the last couple of hours (the API can be slow to say "started")."""
    k = api_time(f["kickoff"])
    return bool(k) and 0 <= (now - k).total_seconds() < MATCH_LENGTH


def next_kickoff(fixtures, now):
    times = [api_time(f["kickoff"]) for f in fixtures if not f["started"] and f["kickoff"]]
    times = [t for t in times if t > now]
    return min(times) if times else None


def provisional_bonus(fixture):
    """{player id: bonus} for a match whose bonus isn't in yet, if it ended now: the top three
    BPS score 3, 2, 1; ties share the higher rank and push the next player down."""
    if fixture["bonus_in"] or not fixture["started"]:
        return {}
    out = {}
    scores = [v for _, v in fixture["bps"]]
    for pid, v in fixture["bps"]:
        rank = 1 + sum(1 for s in scores if s > v)
        if rank in BONUS and v > 0:
            out[pid] = BONUS[rank]
    return out


def player_state(fixtures):
    """"playing", "to play", "done" or "blank" (no game) for a player's fixtures this week."""
    if not fixtures:
        return "blank"
    if any(f["started"] and not f["ended"] for f in fixtures):
        return "playing"
    if any(not f["started"] for f in fixtures):
        return "to play"
    return "done"


def auto_subs(squad, chip=None):
    """Who comes off the bench, as FPL would do it if the gameweek ended now: a starter whose
    games are all over without him playing a minute is replaced by the first bench player,
    in bench order, who has played and keeps the formation legal (a keeper only by the bench
    keeper). Returns [(out id, in id)]. Bench Boost: nobody."""
    if chip == "bboost":
        return []
    xi = [p for p in squad if p["slot"] <= 11]
    bench = sorted((p for p in squad if p["slot"] > 11), key=lambda p: p["slot"])
    on = {p["id"] for p in xi}
    used, subs = set(), []
    for p in xi:
        if not (p["state"] in ("done", "blank") and p["minutes"] == 0):
            continue
        for b in bench:
            if b["id"] in used or b["minutes"] == 0 or (b["pos"] == 1) != (p["pos"] == 1):
                continue
            after = [q for q in squad if q["id"] in on and q["id"] != p["id"]] + [b]
            if all(sum(1 for q in after if q["pos"] == pos) >= n for pos, n in MIN_XI.items()):
                used.add(b["id"])
                on = (on - {p["id"]}) | {b["id"]}
                subs.append((p["id"], b["id"]))
                break
    return subs


def fixture_label(f, team, teams, now):
    """How a player's game looks from his side: "NFO 16:30", "nfo 67'", "IPS FT" - the
    opponent's short name, in capitals at home and lower case away (FPL's convention)."""
    home = f["h"] == team
    opp = teams.get(str(f["a"] if home else f["h"]), {}).get("short", "?")
    opp = opp.upper() if home else opp.lower()
    if f["ended"]:
        return f"{opp} FT"
    if f["started"]:
        return f"{opp} {f['minutes']}'"
    k = api_time(f["kickoff"]).astimezone(LOCAL) if f["kickoff"] else None
    if not k:
        return f"{opp} tbc"
    return f"{opp} {k:%H:%M}" if k.date() == now.astimezone(LOCAL).date() else f"{opp} {k:%a}"


def squad(static, picks, live, now):
    """The team's 15 for the gameweek, with live points: a list of dicts in squad order -
    id, name, team, short (team), pos, slot, captain/vice, mult (after auto-subs and the
    captain passing to the vice), pts (points so far incl. confirmed bonus), bonus (bonus
    expected but not in yet), shown (what counts: (pts + bonus) x mult), minutes, state,
    label (fixture), sub ("in"/"out"/None)."""
    players, teams = static["players"], static["teams"]
    fixtures = {f["id"]: f for f in (live or {}).get("fixtures", [])}
    by_team = {}
    for f in fixtures.values():
        by_team.setdefault(f["h"], []).append(f)
        by_team.setdefault(f["a"], []).append(f)
    prov = {}
    for f in fixtures.values():
        prov.update(provisional_bonus(f))
    out = []
    for pk in picks["picks"]:
        info = players.get(str(pk["id"]), {"name": "?", "team": 0, "pos": 3})
        lv = (live or {}).get("players", {}).get(str(pk["id"]), {"pts": 0, "min": 0})
        fx = sorted(by_team.get(info["team"], []), key=lambda f: f["kickoff"] or "")
        state = player_state(fx)
        current = next((f for f in fx if f["started"] and not f["ended"]), None) \
            or next((f for f in fx if not f["started"]), None) or (fx[-1] if fx else None)
        out.append({"id": pk["id"], "name": info["name"], "team": info["team"],
                    "short": teams.get(str(info["team"]), {}).get("short", "?"), "pos": info["pos"],
                    "slot": pk["slot"], "captain": pk["c"], "vice": pk["v"], "mult": pk["mult"],
                    "pts": lv["pts"], "bonus": prov.get(pk["id"], 0), "minutes": lv["min"], "state": state,
                    "label": fixture_label(current, info["team"], teams, now) if current else "no game",
                    "sub": None})
    cap = next((p for p in out if p["captain"]), None)
    vice = next((p for p in out if p["vice"]), None)
    cap_mult = cap["mult"] if cap else 0          # 2, or 3 with Triple Captain
    by_id = {p["id"]: p for p in out}
    for gone, came in auto_subs(out, picks.get("chip")):
        by_id[came]["mult"], by_id[gone]["mult"] = 1, 0
        by_id[gone]["sub"], by_id[came]["sub"] = "out", "in"
    if cap and vice and cap["state"] in ("done", "blank") and cap["minutes"] == 0 and vice["minutes"] > 0:
        vice["mult"], cap["mult"] = cap_mult, 0
    for p in out:
        p["shown"] = (p["pts"] + p["bonus"]) * p["mult"]
    return out


def totals(team, picks):
    """{"points" (gross, as FPL shows the gameweek), "hit" (transfer cost), "net", "to_play"
    (counting players with a game still to finish), "bench" (points left on the bench)}."""
    counting = [p for p in team if p["mult"] > 0]
    points = sum(p["shown"] for p in counting)
    hit = picks.get("cost") or 0
    return {"points": points, "hit": hit, "net": points - hit,
            "to_play": sum(1 for p in counting if p["state"] in ("to play", "playing")),
            "bench": sum(p["pts"] + p["bonus"] for p in team if p["mult"] == 0 and p["sub"] != "out")}


def flagged(static, picks):
    """Squad players with a doubt over the next game: [(player, chance or None)] worst first."""
    out = []
    for pk in picks["picks"]:
        p = static["players"].get(str(pk["id"]))
        if p and (p["status"] != "a" or (p["chance"] is not None and p["chance"] < 100)):
            chance = p["chance"] if p["chance"] is not None else (0 if p["status"] in ("i", "s", "u", "n") else None)
            out.append((p, chance))
    return sorted(out, key=lambda pc: (pc[1] if pc[1] is not None else 100))


def difficulty(future, team, first_gw, weeks=5):
    """[(gameweek, [(opponent id, home?, difficulty)])] for a team's next few gameweeks:
    an empty list is a blank, two entries a double."""
    rows = []
    for gw in range(first_gw, first_gw + weeks):
        games = [(f["a"], True, f["hd"]) if f["h"] == team else (f["h"], False, f["ad"])
                 for f in future if f["event"] == gw and team in (f["h"], f["a"])]
        rows.append((gw, games))
    return rows


def leagues_to_show(entry, config):
    """The leagues for the Leagues page: those listed in the config (by id), else the team's
    private leagues (FPL's own public ones - Overall, country, club - are huge)."""
    if not entry:
        return []
    wanted = config.get("leagues") or []
    by_id = {lg["id"]: lg for lg in entry["leagues"]}
    if wanted:
        return [by_id[i] for i in wanted if i in by_id][:MAX_LEAGUES]
    return [lg for lg in entry["leagues"] if lg["type"] == "x"][:MAX_LEAGUES]


# ── Fetching and caching ──────────────────────────────────────────────────────

def http_json(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json",
                                               "Accept-Encoding": "gzip"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = r.read()
        if r.headers.get("Content-Encoding") == "gzip":
            body = gzip.decompress(body)
    return json.loads(body)


def explain(err):
    if isinstance(err, urllib.error.HTTPError):
        return {404: "not found", 429: "too many requests", 503: "FPL is updating"}.get(err.code, f"HTTP {err.code}")
    if isinstance(err, urllib.error.URLError):
        return "offline"
    return str(err)[:60] or type(err).__name__


class Store:
    """Keeps the team's FPL data fresh, cached on disk.

    get(name) returns a part's cut-down data or None: "static" (gameweeks, teams, players),
    "entry" (the team), "history", "picks", "live" (points and fixtures of the current
    gameweek), "future" (fixtures to come) and "league:<id>". Fetching happens in a
    background thread (start()); each part is fetched when due() says so.
    """

    def __init__(self, config, cache_dir=CACHE_DIR, fetch=http_json, clock=time.time):
        self.config, self.cache_dir, self.fetch, self.clock = config, cache_dir, fetch, clock
        self.team_id = int(config["team_id"])
        self.lock = threading.Lock()
        self.entries, self.errors, self.retry_at = {}, {}, {}
        try:
            for name in os.listdir(cache_dir):
                if name.endswith(".json"):
                    with open(os.path.join(cache_dir, name)) as f:
                        entry = json.load(f)
                    if entry.get("team") == self.team_id:
                        self.entries[name[:-5].replace("_", ":")] = entry
        except (OSError, ValueError):
            pass

    def get(self, name):
        with self.lock:
            return (self.entries.get(name) or {}).get("data")

    def fetched(self, name):
        with self.lock:
            return (self.entries.get(name) or {}).get("fetched")

    def error(self):
        """The most recent fetch problem still unresolved, or None."""
        with self.lock:
            return next(iter(sorted(self.errors.values(), reverse=True)), (0, None))[1]

    def stamp(self):
        """Changes whenever any data arrives - for knowing when to redraw."""
        with self.lock:
            return tuple(sorted((k, v["fetched"]) for k, v in self.entries.items()))

    def due(self, now):
        """{part: seconds between fetches} - what to keep fresh, and how fresh, right now."""
        static = self.get("static")
        if not static:
            return {"static": 0}
        dt = datetime.fromtimestamp(now, UTC)
        gw, _ = gameweeks(static, dt)
        live = self.get("live")
        live_now = live if live and live["event"] == gw else None
        ph = phase(live_now["fixtures"], dt) if live_now else None
        busy = ph in ("live", "bonus")
        out = {"static": 30 * MINUTE if busy else 2 * HOUR, "entry": 15 * MINUTE if busy else HOUR,
               "future": 6 * HOUR}
        if gw:
            out["history"] = 6 * HOUR
            picks = self.get("picks")
            out["picks"] = 0 if (picks or {}).get("event") != gw else 6 * HOUR
            if picks and picks["event"] != gw:
                pass                        # keep last week's points on show until the new picks come
            elif not live_now:
                out["live"] = 0
            elif ph == "live":
                out["live"] = MINUTE
            elif ph == "bonus":
                out["live"] = 5 * MINUTE
            elif ph == "waiting":
                k = next_kickoff(live_now["fixtures"], dt)
                out["live"] = max(MINUTE, min(HOUR, (k - dt).total_seconds())) if k else HOUR
            else:
                out["live"] = 6 * HOUR if event(static, gw) and event(static, gw)["checked"] else HOUR
        for lg in leagues_to_show(self.get("entry"), self.config):
            out[f"league:{lg['id']}"] = 5 * MINUTE if busy else HOUR
        return out

    def stale(self, name, every, now):
        """Due: older than `every`, or fetched before a deadline that has since passed."""
        t = self.fetched(name)
        if t is None or now - t >= every:
            return True
        static = self.get("static")
        if static and name not in ("live", "picks"):
            return any(t < api_time(e["deadline"]).timestamp() <= now for e in static["events"])
        return False

    def refresh(self):
        """One pass: fetch whatever is due. Returns how many parts were fetched (or tried)."""
        n, done = 0, set()
        while True:                         # again after the first fetch tells us the gameweek
            now = self.clock()
            todo = [k for k, every in self.due(now).items()
                    if k not in done and now >= self.retry_at.get(k, 0) and self.stale(k, every, now)]
            if not todo:
                return n
            for name in todo:
                done.add(name)
                n += 1
                self._fetch_part(name, now)

    def _fetch_part(self, name, now):
        try:
            data = self._get(name, datetime.fromtimestamp(now, UTC))
        except Exception as e:              # network, HTTP, bad JSON: keep the old data
            with self.lock:
                self.errors[name] = (now, explain(e))
            self.retry_at[name] = now + RETRY_AFTER
            log(f"{name}: {explain(e)} ({e})")
            return
        entry = {"team": self.team_id, "fetched": now, "data": data}
        with self.lock:
            self.entries[name] = entry
            self.errors.pop(name, None)
        self._save(name, entry)

    def _get(self, name, now):
        tid = self.team_id
        if name == "static":
            return slim_static(self.fetch(f"{API}/bootstrap-static/"))
        if name == "entry":
            return slim_entry(self.fetch(f"{API}/entry/{tid}/"))
        if name == "history":
            return slim_history(self.fetch(f"{API}/entry/{tid}/history/"))
        if name == "future":
            return slim_future(self.fetch(f"{API}/fixtures/?future=1"))
        gw, _ = gameweeks(self.get("static"), now)
        if name == "picks":
            return slim_picks(self.fetch(f"{API}/entry/{tid}/event/{gw}/picks/"), gw)
        if name == "live":
            fixtures = self.fetch(f"{API}/fixtures/?event={gw}")
            return slim_live(self.fetch(f"{API}/event/{gw}/live/"), fixtures, gw)
        if name.startswith("league:"):
            return self._league(int(name[7:]))
        raise ValueError(f"unknown part {name}")

    def _league(self, league_id):
        """The table's first page (50 teams), plus the page with the team on, if further down."""
        url = f"{API}/leagues-classic/{league_id}/standings/"
        pages = [self.fetch(url)]
        if not any(r["entry"] == self.team_id for r in pages[0]["standings"]["results"]):
            lg = next((lg for lg in (self.get("entry") or {}).get("leagues", []) if lg["id"] == league_id), None)
            if lg and lg.get("rank"):
                page = (lg["rank"] - 1) // 50 + 1
                if page > 1:
                    pages.append(self.fetch(f"{url}?page_standings={page}"))
        return slim_league(pages, self.team_id)

    def _save(self, name, entry):
        path = os.path.join(self.cache_dir, name.replace(":", "_") + ".json")
        try:
            os.makedirs(self.cache_dir, exist_ok=True)
            with open(path + ".tmp", "w") as f:
                json.dump(entry, f)
            os.replace(path + ".tmp", path)
        except OSError as e:
            log("can't cache:", e)

    def run(self, every=15):
        while True:
            self.refresh()
            time.sleep(every)

    def start(self):
        threading.Thread(target=self.run, daemon=True).start()
        return self


def check():
    """Command-line check of the team against the live API (run on the Pi or a Mac)."""
    cfg = load_config()
    store = Store(cfg, cache_dir="/nonexistent")
    store._save = lambda name, entry: None
    store.refresh()
    if store.error():
        print("FAILED -", store.error())
    static, entry, picks = store.get("static"), store.get("entry"), store.get("picks")
    if not (static and entry):
        return 1
    now = datetime.now(UTC)
    gw, nxt = gameweeks(static, now)
    print(f"{entry['name']} ({entry['manager']}): {entry['total']} points, overall rank {entry['rank']:,}")
    if picks:
        team = squad(static, picks, store.get("live"), now)
        t = totals(team, picks)
        print(f"Gameweek {gw}: {t['points']} points live, {t['to_play']} still to play"
              f" ({phase(store.get('live')['fixtures'], now)})")
        print("  " + ", ".join(f"{p['name']} {p['shown']}" for p in team if p["mult"]))
    for lg in leagues_to_show(entry, cfg):
        table = store.get(f"league:{lg['id']}")
        me = next((r for r in table["rows"] if r["me"]), None) if table else None
        print(f"{lg['name']}: {me['rank'] if me else '?'} of {lg['size']}")
    if nxt:
        print(f"Next deadline: {nxt['name']}, {api_time(nxt['deadline']).astimezone(LOCAL):%a %d %b %H:%M}")
    return 0 if not store.error() else 1


if __name__ == "__main__":
    if sys.argv[1:] == ["--check"]:
        sys.exit(check())
    print(__doc__)
