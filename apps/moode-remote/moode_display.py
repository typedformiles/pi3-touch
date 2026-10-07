#!/usr/bin/env python3
"""
Moode Remote Display
A fullscreen pygame app for Pi 3B + HyperPixel 4.0 (480x800 portrait), driving Moode through
its web app's API on moode.local:5005 (moode_api.py) - profiles, likes, Bliss and all.

Tabs along the bottom: Now (art, controls, Like / Bliss / Shuffle / Repeat), Up Next (what's
coming up and who queued it) and 4U (one-tap moods, mixes, decades and genres). The photo top
right is who's listening - tap it to switch; likes and mixes are theirs.
"""

import base64
import io
import os
import queue
import random
import sys
import threading
import time

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "..", "common"))
import moode_api  # noqa: E402

# Use KMS/DRM driver on headless Pi (set before pygame.display.init)
if "DISPLAY" not in os.environ and "WAYLAND_DISPLAY" not in os.environ:
    os.environ.setdefault("SDL_VIDEODRIVER", "kmsdrm")
    # HyperPixel may appear as card0 or card1 — adjust if needed
    os.environ.setdefault("SDL_KMSDRM_DEVICE_INDEX", "0")
os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
import pygame  # noqa: E402
import pgscreen  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402

# ── Config ────────────────────────────────────────────────────────────────────
SCREEN_W, SCREEN_H = 480, 800
FPS = 20                    # touch polling rate; the screen is only redrawn when something changes
POLL_INTERVAL = 1.0         # seconds between status polls
RETRY_INTERVAL = 5          # seconds between attempts while moode.local:5005 can't be reached
LISTS_EVERY = 30 * 60       # profiles / moods / decades / genres refresh
DIM_AFTER = 300             # seconds of inactivity before dimming (5 min)
DIM_BRIGHTNESS = 120        # 0-255 (higher = brighter when dimmed)
TOAST_FOR = 2.5             # seconds a message stays up
HOME_REQUEST = "/run/pi3-touch/request-home"   # Pi3 Touch launcher picks this up
STATE_DIR = os.environ.get("STATE_DIRECTORY", "/var/lib/pi3-touch-moode-remote")
FONT_DIR = os.environ.get("PI3_FONT_DIR", "/usr/share/fonts/truetype/dejavu")
TABS = ["Now", "Next", "4U"]

# ── Colours ───────────────────────────────────────────────────────────────────
BG          = (15,  15,  20)
BG_CARD     = (28,  28,  38)
BG_CARD_HI  = (40,  38,  56)
TEXT_PRI    = (240, 240, 240)
TEXT_SEC    = (160, 160, 175)
TEXT_DIM    = (90,  90, 105)
ACCENT      = (130,  80, 220)   # purple
ACCENT_DIM  = (60,   40, 110)
BAR_BG      = (45,   45,  60)
LIKE        = (240,  90, 130)
DANGER      = (220,  80,  80)
MOODS       = {"Late night": (52, 48, 120), "Chill": (34, 98, 110), "Energetic": (150, 64, 52)}

# ── Layout constants (portrait 480x800) ───────────────────────────────────────
PAD         = 16
HEAD_H      = 56
TABS_Y      = 736
ART_SIZE    = 300
ART_Y       = 64
INFO_Y      = ART_Y + ART_SIZE + 12
PROGRESS_Y  = INFO_Y + 100
BTN_Y       = PROGRESS_Y + 72
BTN_RADIUS  = 36
ACTIONS_Y   = BTN_Y + 52
ROW_H       = 76                # Up Next: one queue row
LIST_TOP    = HEAD_H + 52


def load_font(size, bold=False):
    path = os.path.join(FONT_DIR, "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf")
    try:
        return pygame.font.Font(path, size)
    except (OSError, FileNotFoundError):
        return pygame.font.SysFont("dejavusans,freesans,arial", size, bold=bold)


def truncate(text, font, max_width):
    if font.size(text)[0] <= max_width:
        return text
    while text and font.size(text + "…")[0] > max_width:
        text = text[:-1]
    return text + "…"


def seconds_to_mmss(s):
    try:
        s = int(float(s))
        return f"{s // 60}:{s % 60:02d}"
    except Exception:
        return "0:00"


def circle_image(data, size):
    """Image bytes -> a size x size round pygame surface (listener photos)."""
    img = Image.open(io.BytesIO(data)).convert("RGB")
    side = min(img.size)
    img = img.crop(((img.width - side) // 2, (img.height - side) // 2,
                    (img.width + side) // 2, (img.height + side) // 2)).resize((size * 2, size * 2), Image.LANCZOS)
    mask = Image.new("L", img.size, 0)
    ImageDraw.Draw(mask).ellipse((0, 0, img.width - 1, img.height - 1), fill=255)
    img.putalpha(mask)
    img = img.resize((size, size), Image.LANCZOS)
    return pygame.image.fromstring(img.tobytes(), img.size, "RGBA")


def hex_colour(h, default=ACCENT):
    try:
        return tuple(int(h.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))
    except (AttributeError, ValueError):
        return default


def per_page():
    """Up Next rows per page, below the current song's row."""
    return (TABS_Y - 8 - LIST_TOP - ROW_H) // ROW_H


class MoodeDisplay:
    def __init__(self, api=None, screen=None, start_threads=True):
        if screen is None:
            self.display = pgscreen.Display(SCREEN_W, SCREEN_H, "Moode Display")
            screen = self.display.surface
        else:
            self.display = None
            pygame.font.init()
        self.screen = screen
        self.clock = pygame.time.Clock()
        self.api = api or moode_api.Api()

        # Fonts
        self.font_title   = load_font(26, bold=True)
        self.font_artist  = load_font(21)
        self.font_album   = load_font(17)
        self.font_time    = load_font(16)
        self.font_clock   = load_font(28, bold=True)
        self.font_sub     = load_font(20)
        self.font_head    = load_font(22, bold=True)
        self.font_row     = load_font(19, bold=True)
        self.font_rowsub  = load_font(15)
        self.font_tile    = load_font(18, bold=True)
        self.font_small   = load_font(14)
        self.font_icon    = load_font(30)
        self.font_note    = load_font(96)
        self.font_tab     = load_font(20)
        self.font_tab_on  = load_font(20, bold=True)

        # What the API says (written by the background threads, under self.lock)
        self.lock = threading.Lock()
        self.connected = False
        self.status = {}
        self.queue = []
        self.queue_version = None
        self.profiles = []
        self.liked = set()
        self.lists = {"moods": [], "decades": [], "genres": []}
        self.lists_at = 0
        self.art_surface = None
        self.art_file = None
        self.avatars = {}           # (profile id, size) -> round surface

        # Screen state
        self.tab = TABS[0]
        self.next_offset = 0        # Up Next: first row shown after the current song
        self.sheet = None           # open action sheet: {"title", "sub", "actions": [(icon, label, fn, danger)]}
        self.picker = False         # the "who's listening?" overlay
        self.confirm_clear = 0.0    # Up Next: when "Clear" was tapped (tap again to confirm)
        self.toast = ("", 0.0)      # (message, shown until)
        self.last_touch = time.time()
        self.dimmed = False
        self.sleeping = False
        self.down = None            # where the current touch started
        self.buttons = {}
        self._drawn = None          # drawn_state() of the frame on screen
        self._prev_song = None
        self._prev_state = None

        self.api.profile = self.load_profile()
        self.jobs = queue.Queue()   # actions, run one at a time off the UI thread
        if start_threads:
            for target in (self._poll_loop, self._job_loop):
                threading.Thread(target=target, daemon=True).start()

    # ── Listener ──────────────────────────────────────────────────────────────

    def profile_file(self):
        return os.path.join(STATE_DIR, "profile")

    def load_profile(self):
        try:
            with open(self.profile_file()) as f:
                return int(f.read().strip())
        except (OSError, ValueError):
            return None                 # first run: the first profile, once we know them

    def set_profile(self, pid):
        self.api.profile = pid
        try:
            os.makedirs(STATE_DIR, exist_ok=True)
            with open(self.profile_file(), "w") as f:
                f.write(str(pid))
        except OSError as e:
            print(f"[profile] can't remember listener: {e}", flush=True)
        self.do(self._load_likes)

    def me(self):
        return self.profile_by_id(self.api.profile)

    def profile_by_id(self, pid):
        return next((p for p in self.profiles if p["id"] == pid), None)

    # ── Background: polling, art, actions ─────────────────────────────────────

    def _poll_loop(self):
        while True:
            try:
                self.poll_once()
                time.sleep(POLL_INTERVAL)
            except Exception as e:
                with self.lock:
                    was, self.connected = self.connected, False
                if was:
                    print(f"[api] lost {self.api.base}: {e}", flush=True)
                time.sleep(RETRY_INTERVAL)

    def poll_once(self):
        if time.time() - self.lists_at > LISTS_EVERY:
            self._load_lists()
        st = self.api.status()
        with self.lock:
            self.status, self.connected = st, True
        if st.get("version") != self.queue_version:
            q = self.api.queue()
            with self.lock:
                self.queue, self.queue_version = q, st.get("version")
        song = st.get("song") or {}
        if song.get("file") != self.art_file:
            self._fetch_art(song.get("file"))

    def _load_lists(self):
        profiles = self.api.profiles()
        lists = {"moods": self.api.moods(), "decades": self.api.decades(), "genres": self.api.genres()}
        with self.lock:
            self.profiles, self.lists = profiles, lists
            self.avatars.clear()
        if self.profile_by_id(self.api.profile) is None and profiles:
            self.api.profile = profiles[0]["id"]
        self._load_likes()
        self.lists_at = time.time()

    def _load_likes(self):
        likes = set(self.api.likes()) if self.api.profile is not None else set()
        with self.lock:
            self.liked = likes

    def _fetch_art(self, file):
        self.art_file = file
        surf = None
        if file:
            try:
                img = Image.open(io.BytesIO(self.api.art(file))).convert("RGB")
                img.thumbnail((ART_SIZE, ART_SIZE), Image.LANCZOS)
                surf = pygame.image.fromstring(img.tobytes(), img.size, img.mode)
            except Exception as e:
                print(f"[art] no art for {file}: {e}", flush=True)
        with self.lock:
            self.art_surface = surf

    def do(self, fn, done=None):
        """Run an API action in the background; done(result) gives the message to show (or None)."""
        self.jobs.put((fn, done))

    def _job_loop(self):
        while True:
            self.run_job(*self.jobs.get())

    def run_job(self, fn, done):
        try:
            result = fn()
            msg = done(result) if done else None
        except Exception as e:
            print(f"[api] action failed: {e}", flush=True)
            msg = "Couldn't reach Moode"
        if msg:
            self.say(msg)

    def say(self, msg):
        self.toast = (msg, time.time() + TOAST_FOR)

    # ── Drawing helpers ───────────────────────────────────────────────────────

    def text(self, s, font, colour, pos, anchor="topleft"):
        surf = font.render(s, True, colour)
        rect = surf.get_rect(**{anchor: pos})
        self.screen.blit(surf, rect)
        return rect

    def rrect(self, colour, rect, radius=12, width=0):
        pygame.draw.rect(self.screen, colour, rect, width=width, border_radius=radius)

    def avatar(self, profile, size):
        """A listener's round photo (or their initial on their colour)."""
        if not profile:
            return None
        key = (profile["id"], size)
        if key not in self.avatars:
            surf = None
            if profile.get("avatar"):
                try:
                    surf = circle_image(base64.b64decode(profile["avatar"].split(",", 1)[1]), size)
                except Exception:
                    surf = None
            if surf is None:
                surf = pygame.Surface((size, size), pygame.SRCALPHA)
                pygame.draw.circle(surf, hex_colour(profile.get("color")), (size // 2, size // 2), size // 2)
                letter = load_font(int(size * .5), bold=True).render(profile["name"][:1].upper(), True, TEXT_PRI)
                surf.blit(letter, letter.get_rect(center=(size // 2, size // 2)))
            self.avatars[key] = surf
        return self.avatars[key]

    def _draw_icon_prev(self, cx, cy, size, colour):
        """Draw previous track icon: bar + left triangle."""
        s = size // 2
        pygame.draw.rect(self.screen, colour, (cx - s, cy - s, 3, s * 2))
        pygame.draw.polygon(self.screen, colour, [(cx - s + 4, cy), (cx + s, cy - s), (cx + s, cy + s)])

    def _draw_icon_next(self, cx, cy, size, colour):
        """Draw next track icon: right triangle + bar."""
        s = size // 2
        pygame.draw.polygon(self.screen, colour, [(cx - s, cy - s), (cx - s, cy + s), (cx + s - 4, cy)])
        pygame.draw.rect(self.screen, colour, (cx + s - 3, cy - s, 3, s * 2))

    def _draw_icon_play(self, cx, cy, size, colour):
        """Draw play icon: right-pointing triangle."""
        s = size // 2
        pygame.draw.polygon(self.screen, colour, [(cx - s + 2, cy - s), (cx - s + 2, cy + s), (cx + s, cy)])

    def _draw_icon_pause(self, cx, cy, size, colour):
        """Draw pause icon: two vertical bars."""
        s = size // 2
        bar_w = gap = max(size // 5, 3)
        pygame.draw.rect(self.screen, colour, (cx - gap - bar_w, cy - s, bar_w, s * 2))
        pygame.draw.rect(self.screen, colour, (cx + gap, cy - s, bar_w, s * 2))

    # ── Chrome: status bar, tabs, messages ────────────────────────────────────

    def _draw_status_bar(self):
        self.text(time.strftime("%H:%M"), self.font_clock, TEXT_PRI, (PAD, 14))
        dot_col = ACCENT if self.connected else (180, 60, 60)
        pygame.draw.circle(self.screen, dot_col, (SCREEN_W - PAD - 8, 28), 8)

        # Sleep button (moon crescent) — left of connection dot
        mx, my, mr = SCREEN_W - PAD - 40, 26, 10
        pygame.draw.circle(self.screen, TEXT_SEC, (mx, my), mr)
        pygame.draw.circle(self.screen, BG, (mx + 5, my - 4), mr - 2)
        self.buttons["sleep"] = pygame.Rect(mx - 18, my - 18, 36, 36)

        # Home button (house) - back to the Pi3 Touch menu; only when the launcher is installed
        x = SCREEN_W - PAD - 82
        if os.path.isdir(os.path.dirname(HOME_REQUEST)):
            hx, hy = x, 27
            pygame.draw.polygon(self.screen, TEXT_SEC, [(hx - 13, hy - 1), (hx, hy - 12), (hx + 13, hy - 1)])
            pygame.draw.rect(self.screen, TEXT_SEC, (hx - 9, hy - 1, 18, 12))
            pygame.draw.rect(self.screen, BG, (hx - 2, hy + 4, 5, 7))          # door
            self.buttons["home"] = pygame.Rect(hx - 18, hy - 18, 36, 36)
            x -= 50

        # Who's listening - tap to switch
        me = self.me()
        av = self.avatar(me, 34)
        if av:
            self.screen.blit(av, av.get_rect(center=(x, 27)))
            nr = self.text(me["name"], self.font_time, TEXT_SEC, (x - 24, 27), "midright")
            self.buttons["who"] = pygame.Rect(nr.x - 8, 2, x + 22 - nr.x + 8, 50)

    def _draw_tabs(self):
        pygame.draw.rect(self.screen, BG_CARD, (0, TABS_Y, SCREEN_W, SCREEN_H - TABS_Y))
        w = SCREEN_W / len(TABS)
        for i, t in enumerate(TABS):
            r = pygame.Rect(round(i * w), TABS_Y, round(w), SCREEN_H - TABS_Y)
            on = t == self.tab
            if on:
                pygame.draw.rect(self.screen, ACCENT, (r.x + 20, TABS_Y, r.w - 40, 4), border_radius=2)
            label = "Up Next" if t == "Next" else t
            self.text(label, self.font_tab_on if on else self.font_tab, ACCENT if on else TEXT_SEC, r.center, "center")
            self.buttons[f"tab:{t}"] = r

    def _draw_no_connection(self):
        cy = (HEAD_H + TABS_Y) // 2
        self.text(f"Connecting to {self.api.base.split('//')[-1]}…", self.font_sub, TEXT_DIM, (SCREEN_W // 2, cy), "center")
        self.text("the Moode web app", self.font_time, ACCENT_DIM, (SCREEN_W // 2, cy + 34), "center")

    def _draw_toast(self):
        msg, until = self.toast
        if msg and time.time() < until:
            surf = self.font_time.render(msg, True, TEXT_PRI)
            r = surf.get_rect(center=(SCREEN_W // 2, TABS_Y - 30)).inflate(32, 18)
            self.rrect((58, 44, 96), r, radius=r.h // 2)
            self.screen.blit(surf, surf.get_rect(center=r.center))

    # ── Now ───────────────────────────────────────────────────────────────────

    def _draw_now(self):
        st, song = self.status, self.status.get("song") or {}
        if not song:
            cy = ART_Y + ART_SIZE // 2
            self.text("Nothing playing", self.font_sub, TEXT_DIM, (SCREEN_W // 2, cy), "center")
            self.text("Pick something in 4U", self.font_time, ACCENT_DIM, (SCREEN_W // 2, cy + 32), "center")
            return
        # Art
        art_x = (SCREEN_W - ART_SIZE) // 2
        self.rrect(BG_CARD, (art_x - 6, ART_Y - 6, ART_SIZE + 12, ART_SIZE + 12), radius=16)
        if self.art_surface:
            sw, sh = self.art_surface.get_size()
            self.screen.blit(self.art_surface, (art_x + (ART_SIZE - sw) // 2, ART_Y + (ART_SIZE - sh) // 2))
        else:
            self.text("♪", self.font_note, ACCENT_DIM, (SCREEN_W // 2, ART_Y + ART_SIZE // 2), "center")

        # Track info
        max_w = SCREEN_W - PAD * 2
        title = song.get("title") or (song.get("file") or "Unknown").split("/")[-1]
        self.text(truncate(title, self.font_title, max_w), self.font_title, TEXT_PRI, (PAD, INFO_Y))
        self.text(truncate(song.get("artist") or song.get("albumartist") or "", self.font_artist, max_w),
                  self.font_artist, TEXT_SEC, (PAD, INFO_Y + 35))
        album = " · ".join(filter(None, [song.get("album"), str(song.get("date") or "")[:4]]))
        self.text(truncate(album, self.font_album, max_w), self.font_album, TEXT_DIM, (PAD, INFO_Y + 63))

        # Progress
        elapsed, duration = float(st.get("elapsed") or 0), float(st.get("duration") or 0)
        ratio = min(elapsed / duration, 1.0) if duration else 0
        bar_w = SCREEN_W - PAD * 2
        self.rrect(BAR_BG, (PAD, PROGRESS_Y, bar_w, 6), radius=3)
        if ratio > 0:
            self.rrect(ACCENT, (PAD, PROGRESS_Y, int(bar_w * ratio), 6), radius=3)
        pygame.draw.circle(self.screen, ACCENT, (PAD + int(bar_w * ratio), PROGRESS_Y + 3), 7)
        self.text(seconds_to_mmss(elapsed), self.font_time, TEXT_SEC, (PAD, PROGRESS_Y + 14))
        self.text(seconds_to_mmss(duration), self.font_time, TEXT_SEC, (SCREEN_W - PAD, PROGRESS_Y + 14), "topright")

        # Prev / play / next
        cx = SCREEN_W // 2
        for name, bx, r in (("prev", cx - 100, BTN_RADIUS - 6), ("play", cx, BTN_RADIUS), ("next", cx + 100, BTN_RADIUS - 6)):
            rect = pygame.Rect(bx - r, BTN_Y - r, r * 2, r * 2)
            self.rrect(BG_CARD, rect, radius=r)
            self.rrect(ACCENT_DIM, rect, radius=r, width=2)
            if name == "prev":
                self._draw_icon_prev(bx, BTN_Y, 20, TEXT_PRI)
            elif name == "next":
                self._draw_icon_next(bx, BTN_Y, 20, TEXT_PRI)
            elif st.get("state") == "play":
                self._draw_icon_pause(bx, BTN_Y, 20, TEXT_PRI)
            else:
                self._draw_icon_play(bx, BTN_Y, 20, TEXT_PRI)
            self.buttons[name] = rect

        # Like / Bliss / Shuffle / Repeat
        liked = song.get("file") in self.liked
        station = bool(st.get("station"))
        repeat = "Repeat 1" if st.get("repeat") and st.get("single") else "Repeat"
        actions = [("like", "♥" if liked else "♡", "Liked" if liked else "Like", liked, LIKE),
                   ("bliss", "✦", "Station" if station else "Bliss", station, ACCENT),
                   ("shuffle", "⇄", "Shuffle", bool(st.get("random")), ACCENT),
                   ("repeat", "↻", repeat, bool(st.get("repeat")), ACCENT)]
        gap = 10
        w = (SCREEN_W - 2 * PAD - gap * 3) // 4
        for i, (name, icon, label, on, col) in enumerate(actions):
            r = pygame.Rect(PAD + i * (w + gap), ACTIONS_Y, w, TABS_Y - 12 - ACTIONS_Y)
            self.rrect(BG_CARD_HI if on else BG_CARD, r, radius=14)
            if on:
                self.rrect(col, r, radius=14, width=2)
            self.text(icon, self.font_icon, col if on else TEXT_SEC, (r.centerx, r.y + 26), "center")
            self.text(label, self.font_small, TEXT_PRI if on else TEXT_SEC, (r.centerx, r.bottom - 16), "center")
            self.buttons[name] = r
        owner = self.profile_by_id(st.get("station_owner")) if station else None
        av = self.avatar(owner, 22)
        if av:                                               # whose station it is
            br = self.buttons["bliss"]
            self.screen.blit(av, (br.right - 26, br.y + 4))

    def bliss_sheet(self):
        st = self.status
        song = st.get("song") or {}
        on = bool(st.get("station"))

        def bliss(action, ok_msg):
            return lambda: self.do(lambda: self.api.bliss(action),
                                   lambda r: (ok_msg.format(n=r.get("added") or 20)) if r.get("ok") else "Bliss couldn't do that")
        acts = []
        if song:
            acts += [("✦", "Start a station from this", bliss("station", "Station on ✦"), False),
                     ("+", "Add 20 similar to the queue", bliss("append", "Added {n} similar ✦"), False),
                     ("▶", "Play a mix now", bliss("playnow", "Fresh mix playing ✦"), False),
                     ("◉", "More albums like this", bliss("album", "Queued similar albums ✦"), False)]
        acts.append(("■" if on else "∞", "Auto-queue is on - tap to stop" if on else "Auto-queue: keep it going",
                     lambda: self.do(lambda: self.api.station(not on),
                                     lambda r: "Auto-queue on ✦" if r.get("on") else "Auto-queue off"), False))
        owner = st.get("station_owner")
        if on and owner and owner != self.api.profile:
            name = (self.profile_by_id(owner) or {}).get("name", "someone")
            acts.append(("⇄", f"Take over the station (now {name}'s)",
                         lambda: self.do(self.api.takeover, lambda r: "You're steering the station ✦" if r.get("ok") else "Couldn't take over"),
                         False))
        self.sheet = {"title": "Bliss ✦", "sub": song.get("title", "Nothing playing"), "actions": acts}

    # ── Up Next ───────────────────────────────────────────────────────────────

    def upcoming(self):
        """(current track or None, the tracks after it) from the queue."""
        cur_id = str(self.status.get("songid") or "")
        i = next((n for n, t in enumerate(self.queue) if str(t.get("id")) == cur_id), None)
        if i is None:
            return None, self.queue
        return self.queue[i], self.queue[i + 1:]

    def _draw_next(self):
        cur, rest = self.upcoming()
        n = per_page()
        self.next_offset = max(0, min(self.next_offset, max(0, len(rest) - 1) // n * n))
        hr = self.text("Up next", self.font_head, TEXT_PRI, (PAD, HEAD_H + 10))
        self.text(f"{len(rest)} track{'' if len(rest) == 1 else 's'}", self.font_time, TEXT_DIM, (hr.right + 10, HEAD_H + 15))

        # Clear (tap twice), page up / down
        confirming = time.time() - self.confirm_clear < 4
        r = pygame.Rect(SCREEN_W - PAD - 88, HEAD_H + 4, 88, 36)
        if rest:
            self.rrect(DANGER if confirming else BG_CARD, r, radius=18)
            self.text("Sure?" if confirming else "Clear", self.font_time, TEXT_PRI, r.center, "center")
            self.buttons["clear"] = r
        for name, x, ok, up in (("page-up", r.x - 96, self.next_offset > 0, True),
                                ("page-down", r.x - 48, self.next_offset + n < len(rest), False)):
            pr = pygame.Rect(x, HEAD_H + 4, 40, 36)
            if ok:
                self.rrect(BG_CARD, pr, radius=18)
                cx, cy, s = pr.centerx, pr.centery, 7
                pts = [(cx - s, cy + 3), (cx + s, cy + 3), (cx, cy - 5)] if up else [(cx - s, cy - 3), (cx + s, cy - 3), (cx, cy + 5)]
                pygame.draw.polygon(self.screen, TEXT_PRI, pts)
                self.buttons[name] = pr

        rows = [("now", cur)] if cur and self.next_offset == 0 else []
        rows += [("next", t) for t in rest[self.next_offset:self.next_offset + n + (1 - len(rows))]]
        if not rows:
            self.text("Nothing queued", self.font_sub, TEXT_DIM, (SCREEN_W // 2, LIST_TOP + 120), "center")
            self.text("Start something in 4U", self.font_time, ACCENT_DIM, (SCREEN_W // 2, LIST_TOP + 154), "center")
        y = LIST_TOP
        for kind, t in rows:
            r = pygame.Rect(PAD - 6, y, SCREEN_W - 2 * PAD + 12, ROW_H - 6)
            self.rrect(BG_CARD_HI if kind == "now" else BG_CARD, r, radius=12)
            if kind == "now":
                pygame.draw.rect(self.screen, ACCENT, (r.x, r.y + 10, 4, r.h - 20), border_radius=2)
            by = t.get("by") or {}
            if by.get("auto"):                                 # a Bliss pick
                self.text("✦", self.font_icon, ACCENT, (r.x + 30, r.centery), "center")
            else:                                              # whoever queued it
                av = self.avatar(self.profile_by_id(by.get("pid")), 34)
                if av:
                    self.screen.blit(av, av.get_rect(center=(r.x + 30, r.centery)))
            x = r.x + 58
            dur = seconds_to_mmss(t.get("duration") or 0)
            dw = self.font_time.size(dur)[0]
            self.text(truncate(t.get("title") or "", self.font_row, r.right - x - dw - 22), self.font_row, TEXT_PRI, (x, r.y + 12))
            sub = " · ".join(filter(None, [t.get("artist"), t.get("album")]))
            if kind == "now":
                sub = "Now playing · " + sub
            self.text(truncate(sub, self.font_rowsub, r.right - x - 12), self.font_rowsub,
                      ACCENT if kind == "now" else TEXT_SEC, (x, r.y + 42))
            self.text(dur, self.font_time, TEXT_DIM, (r.right - 12, r.y + 14), "topright")
            self.buttons[f"track:{t.get('id')}"] = r
            y += ROW_H

    def track_sheet(self, t):
        file, tid = t.get("file"), t.get("id")
        playing = str(tid) == str(self.status.get("songid"))
        liked = file in self.liked
        acts = [] if playing else [("▶", "Play now", lambda: self.do(lambda: self.api.control("playid", id=tid)), False)]
        if not playing:
            to = int((self.status.get("song") or {}).get("pos") or 0) + 1
            acts.append(("⇥", "Play next", lambda: self.do(lambda: self.api.queue_op("move", id=tid, to=to),
                                                          lambda r: "Playing next"), False))
        acts += [("♥" if liked else "♡", "Unlike" if liked else "Like", lambda: self.toggle_like(file), False),
                 ("✦", "More like this", lambda: self.do(lambda: self.api.bliss("append", file=file),
                                                        lambda r: f"Added {r.get('added') or 20} similar ✦" if r.get("ok") else "Couldn't build that mix"),
                  False)]
        if not playing:
            acts.append(("✕", "Remove from the queue", lambda: self.do(lambda: self.api.queue_op("remove", id=tid),
                                                                      lambda r: "Removed"), True))
        self.sheet = {"title": t.get("title") or "", "sub": t.get("artist") or "", "actions": acts}

    # ── 4U ────────────────────────────────────────────────────────────────────

    def _draw_4u(self):
        me = self.me()
        y = HEAD_H + 8
        self.text(f"For {me['name']}" if me else "For you", self.font_head, TEXT_PRI, (PAD, y))
        y += 38

        def section(title, tiles, cols, h):
            nonlocal y
            if not tiles:
                return
            self.text(title, self.font_small, TEXT_SEC, (PAD, y))
            y += 22
            gap = 10
            w = (SCREEN_W - 2 * PAD - gap * (cols - 1)) / cols
            for i, (key, label, sub, colour) in enumerate(tiles):
                row, col = divmod(i, cols)
                r = pygame.Rect(round(PAD + col * (w + gap)), y + row * (h + gap), round(w), h)
                self.rrect(colour or BG_CARD, r, radius=12)
                if sub:
                    self.text(truncate(label, self.font_tile, r.w - 20), self.font_tile, TEXT_PRI, (r.x + 12, r.y + 10))
                    self.text(truncate(sub, self.font_small, r.w - 20), self.font_small, TEXT_SEC, (r.x + 12, r.bottom - 24))
                else:
                    self.text(truncate(label, self.font_tile, r.w - 14), self.font_tile, TEXT_PRI, r.center, "center")
                self.buttons[key] = r
            rows = -(-len(tiles) // cols)
            y += rows * h + (rows - 1) * gap + 16

        section("What's the mood?", [(f"mood:{m}", m, "", MOODS.get(m, ACCENT_DIM)) for m in self.lists["moods"]], 3, 62)
        picks = []
        if self.liked:
            picks = [("q:likes", "Shuffle my Likes", f"{len(self.liked)} songs", ACCENT_DIM),
                     ("q:likemix", "Mix from my Likes", "a Bliss mix", ACCENT_DIM)]
        picks += [("q:randomalbum", "Random album", "start to finish", None),
                  ("q:randomsong", "Random song", "one to discover", None)]
        section("Quick picks", picks, 2, 64)
        section("By era", [(f"decade:{d['decade']}", d["decade"], "", None) for d in self.lists["decades"]], 3, 44)
        top = sorted(self.lists["genres"], key=lambda g: -g.get("count", 0))[:6]
        section("By genre", [(f"genre:{g['genre']}", g["genre"], "", None) for g in top], 3, 44)

    def quickplay(self, kind, value, label):
        self.say(f"Shuffling {label}…")
        self.tab = "Now"
        self.do(lambda: self.api.quickplay(kind, value),
                lambda r: f"Playing {label} · {r.get('count')} tracks" if r.get("ok") else "Nothing found")

    def like_mix(self):
        if not self.liked:
            return
        seed = random.choice(sorted(self.liked))
        self.tab = "Now"
        self.say("Building a mix from your Likes…")
        self.do(lambda: self.api.bliss("playnow", file=seed),
                lambda r: "Fresh mix from your Likes ✦" if r.get("ok") else "Couldn't build that mix")

    def toggle_like(self, file):
        if not file or self.api.profile is None:
            return
        on = file not in self.liked
        with self.lock:                                      # show it straight away
            (self.liked.add if on else self.liked.discard)(file)
        self.say("♥ Added to Likes" if on else "Removed from Likes")
        self.do(lambda: self.api.like(file, on))

    # ── Overlays: action sheet, listener picker ───────────────────────────────

    def _shade(self, alpha):
        self.screen.fill((255 - alpha,) * 3, special_flags=pygame.BLEND_MULT)

    def sheet_rect(self):
        h = 84 + len(self.sheet["actions"]) * 62 + 12
        return pygame.Rect(8, SCREEN_H - h - 8, SCREEN_W - 16, h)

    PICKER_RECT = pygame.Rect(16, 210, SCREEN_W - 32, 300)

    def _draw_sheet(self):
        self._shade(170)
        acts = self.sheet["actions"]
        r = self.sheet_rect()
        self.rrect(BG_CARD, r, radius=20)
        self.text(truncate(self.sheet["title"], self.font_head, r.w - 36), self.font_head, TEXT_PRI, (r.x + 18, r.y + 18))
        self.text(truncate(self.sheet["sub"], self.font_time, r.w - 36), self.font_time, TEXT_SEC, (r.x + 18, r.y + 50))
        self.buttons = {}
        y = r.y + 84
        for i, (icon, label, _, danger) in enumerate(acts):
            row = pygame.Rect(r.x + 10, y, r.w - 20, 56)
            self.rrect(BG_CARD_HI, row, radius=12)
            self.text(icon, self.font_tile, DANGER if danger else ACCENT, (row.x + 30, row.centery), "center")
            self.text(truncate(label, self.font_sub, row.w - 70), self.font_sub, DANGER if danger else TEXT_PRI,
                      (row.x + 58, row.centery), "midleft")
            self.buttons[f"act:{i}"] = row
            y += 62

    def _draw_picker(self):
        self._shade(190)
        r = self.PICKER_RECT
        self.rrect(BG_CARD, r, radius=20)
        self.text("Who's listening?", self.font_head, TEXT_PRI, (r.centerx, r.y + 36), "center")
        self.buttons = {}
        w = r.w / max(1, len(self.profiles))
        for i, p in enumerate(self.profiles):
            cx = round(r.x + w * i + w / 2)
            on = p["id"] == self.api.profile
            if on:
                pygame.draw.circle(self.screen, ACCENT, (cx, r.y + 136), 48, 4)
            av = self.avatar(p, 84)
            self.screen.blit(av, av.get_rect(center=(cx, r.y + 136)))
            self.text(p["name"], self.font_row if on else self.font_sub, TEXT_PRI if on else TEXT_SEC, (cx, r.y + 196), "midtop")
            self.buttons[f"pick:{p['id']}"] = pygame.Rect(round(r.x + w * i), r.y + 80, round(w), 150)
        self.text("Likes and mixes follow whoever's picked", self.font_small, TEXT_DIM, (r.centerx, r.bottom - 30), "center")

    # ── Dimming ───────────────────────────────────────────────────────────────

    def _update_dim(self):
        self.dimmed = time.time() - self.last_touch > DIM_AFTER

    def _apply_dim(self):
        if self.sleeping or self.dimmed:                     # darken in place (no alpha layer)
            level = 10 if self.sleeping else DIM_BRIGHTNESS
            self.screen.fill((level, level, level), special_flags=pygame.BLEND_MULT)

    # ── Touch handling ────────────────────────────────────────────────────────

    def touch_down(self, pos):
        self.down = pos

    def touch_up(self, pos):
        """A touch ended: a sideways swipe changes tab, anything else is a tap where it started."""
        start, self.down = self.down or pos, None
        dx, dy = pos[0] - start[0], pos[1] - start[1]
        if abs(dx) > 80 and abs(dx) > abs(dy) and not (self.sleeping or self.dimmed or self.sheet or self.picker):
            self.last_touch = time.time()
            self.tab = TABS[(TABS.index(self.tab) + (-1 if dx > 0 else 1)) % len(TABS)]
        else:
            self._handle_touch(start)

    def _handle_touch(self, pos):
        self.last_touch = time.time()
        if self.sleeping:
            self.sleeping = False
            return  # First touch just wakes from sleep
        if self.dimmed:
            self.dimmed = False
            return  # First touch just wakes from dim

        hit = next((n for n, r in self.buttons.items() if r.collidepoint(pos)), None)
        if self.sheet:
            if hit and hit.startswith("act:"):
                fn = self.sheet["actions"][int(hit[4:])][2]
                self.sheet = None
                fn()
            elif not self.sheet_rect().collidepoint(pos):
                self.sheet = None                            # tap outside closes it
            return
        if self.picker:
            if hit and hit.startswith("pick:"):
                self.set_profile(int(hit[5:]))
                self.say(f"Listening as {(self.me() or {}).get('name', '')}")
                self.picker = False
            elif not self.PICKER_RECT.collidepoint(pos):
                self.picker = False
            return

        st = self.status
        if hit == "sleep":
            self.sleeping = True
        elif hit == "home":
            try:
                open(HOME_REQUEST, "w").close()
            except OSError as e:
                print(f"[home] can't request menu: {e}", flush=True)
        elif hit == "who":
            self.picker = True
        elif hit and hit.startswith("tab:"):
            self.tab = hit[4:]
        elif hit in ("play", "prev", "next"):
            cmd = {"play": "toggle"}.get(hit, hit)
            self.do(lambda: self.api.control(cmd))
        elif hit == "like":
            self.toggle_like((st.get("song") or {}).get("file"))
        elif hit == "bliss":
            self.bliss_sheet()
        elif hit == "shuffle":
            on = not st.get("random")
            self.do(lambda: self.api.control("random", on=on), lambda r: "Shuffle on" if on else "Shuffle off")
        elif hit == "repeat":
            mode = "all" if not st.get("repeat") else ("one" if not st.get("single") else "off")
            self.do(lambda: self.api.control("repeatmode", mode=mode),
                    lambda r: {"all": "Repeat all", "one": "Repeat this song", "off": "Repeat off"}[mode])
        elif hit == "clear":
            if time.time() - self.confirm_clear < 4:
                self.confirm_clear = 0
                self.do(lambda: self.api.queue_op("clearupnext"), lambda r: "Cleared up next")
            else:
                self.confirm_clear = time.time()
        elif hit in ("page-up", "page-down"):
            self.next_offset += per_page() if hit == "page-down" else -per_page()
        elif hit and hit.startswith("track:"):
            t = next((t for t in self.queue if str(t.get("id")) == hit[6:]), None)
            if t:
                self.track_sheet(t)
        elif hit and hit.startswith("mood:"):
            self.quickplay("mood", hit[5:], hit[5:])
        elif hit and hit.startswith("decade:"):
            self.quickplay("decade", hit[7:], hit[7:])
        elif hit and hit.startswith("genre:"):
            self.quickplay("genre", [hit[6:]], hit[6:])
        elif hit == "q:likes":
            self.quickplay("likes", None, "your Likes")
        elif hit == "q:likemix":
            self.like_mix()
        elif hit == "q:randomalbum":
            self.quickplay("randomalbum", None, "a random album")
        elif hit == "q:randomsong":
            self.quickplay("randomsong", None, "a random song")

    # ── Frames ────────────────────────────────────────────────────────────────

    def drawn_state(self):
        """Everything on screen depends on; the frame is redrawn only when this changes."""
        st, song = self.status, self.status.get("song") or {}
        msg, until = self.toast
        return (self.tab, self.connected, song.get("file"), st.get("state"), int(float(st.get("elapsed") or 0)),
                st.get("random"), st.get("repeat"), st.get("single"), st.get("station"), st.get("station_owner"),
                self.queue_version, song.get("file") in self.liked, len(self.liked), id(self.art_surface),
                self.api.profile, len(self.profiles), tuple(map(len, self.lists.values())), self.next_offset,
                id(self.sheet), self.picker, time.time() - self.confirm_clear < 4,
                msg if time.time() < until else "", self.dimmed, self.sleeping, time.strftime("%H:%M"))

    def draw(self):
        self.buttons = {}
        self.screen.fill(BG)
        self._draw_status_bar()          # always - it holds the Home button
        if not self.connected:
            self._draw_no_connection()
        else:
            {"Now": self._draw_now, "Next": self._draw_next, "4U": self._draw_4u}[self.tab]()
        self._draw_tabs()
        self._draw_toast()
        if self.sheet:
            self._draw_sheet()
        elif self.picker:
            self._draw_picker()
        self._apply_dim()

    def frame(self):
        """Redraw only if something on screen would change. Returns True if it drew."""
        # Wake screen on track change or play/pause state change
        # (but not from manual sleep — user deliberately chose that)
        song, state = (self.status.get("song") or {}).get("file"), self.status.get("state")
        if (song, state) != (self._prev_song, self._prev_state):
            if self._prev_song is not None and not self.sleeping:
                self.last_touch = time.time()
            self._prev_song, self._prev_state = song, state
        self._update_dim()
        with self.lock:
            drawn = self.drawn_state()
            if drawn == self._drawn:
                return False
            self.draw()
        self._drawn = drawn
        return True

    # ── Main loop ─────────────────────────────────────────────────────────────

    def run(self):
        while True:
            for event in pygame.event.get():
                if event.type == pygame.QUIT or (event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE):
                    pygame.quit()
                    return
                # SDL also turns touches into mouse events; take the finger ones only
                if event.type == pygame.FINGERDOWN:
                    self.touch_down(self.display.point(event))
                elif event.type == pygame.FINGERUP:
                    self.touch_up(self.display.point(event))
                elif event.type == pygame.MOUSEBUTTONDOWN and not getattr(event, "touch", False):
                    self.touch_down(self.display.point(event))
                elif event.type == pygame.MOUSEBUTTONUP and not getattr(event, "touch", False):
                    self.touch_up(self.display.point(event))
            if self.frame():
                self.display.present()
            self.clock.tick(FPS)


if __name__ == "__main__":
    app = MoodeDisplay()
    app.run()
