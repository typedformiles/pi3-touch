"""Client for the Moode web app's API (moode.local:5005) - standard library only.

That service sits on the Moode Pi in front of MPD and adds listener profiles, likes,
Bliss mixes and stations, one-tap quickplay and who-queued-what. Requests carry the
listener's profile id (X-Profile) so likes, mixes and station steering are theirs.

    status()                     now playing + player state, polled once a second
    queue()                      the whole queue, each track with "by" (who added it)
    control(cmd, **kw)           toggle | next | prev | seek(elapsed) | random(on) |
                                 repeatmode(mode: all|one|off) | playid(id)
    queue_op(op, **kw)           move(id, to) | remove(id) | clearupnext | add(uri, ...)
    bliss(action, **kw)          station | append | playnow | album (optionally file=...)
    station(on), takeover()      the auto-queue, and steering someone else's
    quickplay(type, value)       mood | decade | genre (list) | likes | randomalbum | randomsong
    like(uri, on), likes()       the listener's favourites
    profiles(), moods(), decades(), genres()
    art(file)                    album art bytes (JPEG)
"""
import json
import os
import urllib.parse
import urllib.request

BASE = os.environ.get("MOODE_API", "http://moode.local:5005")
USER_AGENT = "pi3-touch-moode-remote/2.0"


class Api:
    def __init__(self, base=BASE, timeout=6):
        self.base, self.timeout = base.rstrip("/"), timeout
        self.profile = None             # listener id, sent as X-Profile

    def request(self, path, body=None, timeout=None):
        headers = {"User-Agent": USER_AGENT}
        if self.profile is not None:
            headers["X-Profile"] = str(self.profile)
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(self.base + path, data=data, headers=headers,
                                     method="POST" if body is not None else "GET")
        with urllib.request.urlopen(req, timeout=timeout or self.timeout) as r:
            return r.read()

    def get(self, path, timeout=None):
        return json.loads(self.request(path, timeout=timeout))

    def post(self, path, body=None):
        return json.loads(self.request(path, body or {}, timeout=20))

    # ── Reading ──
    def status(self):
        return self.get("/api/status", timeout=3)

    def queue(self):
        return self.get("/api/queue", timeout=15)

    def profiles(self):
        return self.get("/api/profiles")

    def likes(self):
        return self.get("/api/likes")

    def moods(self):
        return self.get("/api/moods")

    def decades(self):
        return self.get("/api/decades")

    def genres(self):
        return self.get("/api/genres")

    def art(self, file):
        return self.request("/api/art?" + urllib.parse.urlencode({"file": file}), timeout=15)

    # ── Doing ──
    def control(self, cmd, **kw):
        return self.post("/api/control", dict(kw, cmd=cmd))

    def queue_op(self, op, **kw):
        return self.post("/api/queue/op", dict(kw, op=op))

    def bliss(self, action, **kw):
        return self.post("/api/bliss", dict({"n": 20}, **kw, action=action))

    def station(self, on):
        return self.post("/api/station", {"on": on})

    def takeover(self):
        return self.post("/api/station/takeover", {})

    def quickplay(self, kind, value=None):
        return self.post("/api/quickplay", {"type": kind, "value": value})

    def like(self, uri, on):
        return self.post("/api/like", {"uri": uri, "on": on})
