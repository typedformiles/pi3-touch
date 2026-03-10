#!/usr/bin/env python3
"""
Moode Remote Display
A fullscreen pygame app for Pi 3B + HyperPixel 4.0 (480x800 portrait)
Connects to Moode/MPD on moode.local:6600
"""

import pygame
import mpd
import time
import io
import threading
import socket
from PIL import Image
import urllib.request

# ── Config ────────────────────────────────────────────────────────────────────
MPD_HOST = "moode.local"
MPD_PORT = 6600
MOODE_URL = "http://moode.local"
SCREEN_W, SCREEN_H = 480, 800
FPS = 30
RECONNECT_INTERVAL = 5      # seconds between reconnect attempts
POLL_INTERVAL = 1.0         # seconds between MPD status polls
DIM_AFTER = 60              # seconds of inactivity before dimming
DIM_BRIGHTNESS = 30         # 0-255

# ── Colours ───────────────────────────────────────────────────────────────────
BG          = (15,  15,  20)
BG_CARD     = (28,  28,  38)
TEXT_PRI    = (240, 240, 240)
TEXT_SEC    = (160, 160, 175)
TEXT_DIM    = (90,  90, 105)
ACCENT      = (130,  80, 220)   # purple
ACCENT_DIM  = (60,   40, 110)
BAR_BG      = (45,   45,  60)
BAR_FG      = (130,  80, 220)
BTN_PRESS   = (50,   30,  90)

# ── Layout constants (portrait 480x800) ───────────────────────────────────────
PAD         = 24
ART_SIZE    = 180           # album art max dimension (~1/4 of screen area)
ART_Y       = 60
INFO_Y      = ART_Y + ART_SIZE + 28
PROGRESS_Y  = INFO_Y + 160
BTN_Y       = PROGRESS_Y + 60
BTN_RADIUS  = 36
VOL_Y       = BTN_Y + 100
CLOCK_Y     = VOL_Y + 70


def load_font(size, bold=False):
    for name in ["DejaVuSans-Bold" if bold else "DejaVuSans",
                 "FreeSansBold" if bold else "FreeSans",
                 None]:
        try:
            return pygame.font.SysFont(name, size, bold=bold) if name else \
                   pygame.font.Font(None, size)
        except Exception:
            pass
    return pygame.font.Font(None, size)


def truncate(text, font, max_width):
    if font.size(text)[0] <= max_width:
        return text
    while text and font.size(text + "…")[0] > max_width:
        text = text[:-1]
    return text + "…"


def draw_rounded_rect(surface, colour, rect, radius=12):
    pygame.draw.rect(surface, colour, rect, border_radius=radius)


def seconds_to_mmss(s):
    try:
        s = int(float(s))
        return f"{s // 60}:{s % 60:02d}"
    except Exception:
        return "0:00"


class MoodeDisplay:
    def __init__(self):
        pygame.init()
        pygame.mouse.set_visible(False)

        # Framebuffer / fullscreen
        flags = pygame.FULLSCREEN | pygame.NOFRAME
        self.screen = pygame.display.set_mode((SCREEN_W, SCREEN_H), flags)
        pygame.display.set_caption("Moode Display")

        self.clock = pygame.time.Clock()

        # Fonts
        self.font_title   = load_font(26, bold=True)
        self.font_artist  = load_font(22)
        self.font_album   = load_font(18)
        self.font_time    = load_font(17)
        self.font_btn     = load_font(28, bold=True)
        self.font_vol     = load_font(18)
        self.font_clock   = load_font(42, bold=True)
        self.font_sub     = load_font(20)

        # State
        self.mpd_client   = None
        self.connected    = False
        self.status       = {}
        self.song         = {}
        self.art_surface  = None
        self.art_uri      = None
        self.last_poll    = 0
        self.last_touch   = time.time()
        self.dimmed        = False
        self.brightness    = 255

        # Touch button rects (defined in draw, stored for hit testing)
        self.btn_prev = None
        self.btn_play = None
        self.btn_next = None
        self.btn_vol_down = None
        self.btn_vol_up   = None

        # Background MPD thread
        self.lock = threading.Lock()
        self._start_mpd_thread()

    # ── MPD connection ────────────────────────────────────────────────────────

    def _start_mpd_thread(self):
        t = threading.Thread(target=self._mpd_loop, daemon=True)
        t.start()

    def _mpd_loop(self):
        while True:
            try:
                client = mpd.MPDClient()
                client.timeout = 10
                client.connect(MPD_HOST, MPD_PORT)
                with self.lock:
                    self.mpd_client = client
                    self.connected  = True
                # Poll loop
                while True:
                    with self.lock:
                        status = client.status()
                        song   = client.currentsong()
                        self.status = status
                        self.song   = song
                    time.sleep(POLL_INTERVAL)
            except Exception:
                with self.lock:
                    self.connected  = False
                    self.mpd_client = None
                    self.status     = {}
                    self.song       = {}
                time.sleep(RECONNECT_INTERVAL)

    def _mpd_cmd(self, cmd, *args):
        """Send a command to MPD safely."""
        try:
            with self.lock:
                if self.mpd_client:
                    getattr(self.mpd_client, cmd)(*args)
        except Exception:
            pass

    # ── Album art ─────────────────────────────────────────────────────────────

    def _fetch_art(self, uri):
        if not uri or uri == self.art_uri:
            return
        self.art_uri = uri
        try:
            # Try Moode's albumart endpoint
            url = f"{MOODE_URL}/coverart.php"
            req = urllib.request.Request(url, headers={"User-Agent": "MoodeDisplay/1.0"})
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = resp.read()
            img = Image.open(io.BytesIO(data)).convert("RGB")
            img.thumbnail((ART_SIZE, ART_SIZE), Image.LANCZOS)
            mode = img.mode
            size = img.size
            raw  = img.tobytes()
            surf = pygame.image.fromstring(raw, size, mode)
            self.art_surface = surf
        except Exception:
            self.art_surface = None

    # ── Drawing ───────────────────────────────────────────────────────────────

    def _draw_background(self):
        self.screen.fill(BG)

    def _draw_status_bar(self):
        # Connected indicator + clock
        now = time.strftime("%H:%M")
        clock_surf = self.font_clock.render(now, True, TEXT_PRI)
        self.screen.blit(clock_surf, (PAD, 14))

        dot_col = ACCENT if self.connected else (180, 60, 60)
        pygame.draw.circle(self.screen, dot_col, (SCREEN_W - PAD - 8, 28), 8)

    def _draw_art(self):
        art_x = (SCREEN_W - ART_SIZE) // 2
        rect  = pygame.Rect(art_x - 6, ART_Y - 6, ART_SIZE + 12, ART_SIZE + 12)
        draw_rounded_rect(self.screen, BG_CARD, rect, radius=16)

        if self.art_surface:
            sw, sh = self.art_surface.get_size()
            blit_x = art_x + (ART_SIZE - sw) // 2
            blit_y = ART_Y  + (ART_SIZE - sh) // 2
            self.screen.blit(self.art_surface, (blit_x, blit_y))
        else:
            # Placeholder note icon
            note = self.font_btn.render("♪", True, ACCENT_DIM)
            nr   = note.get_rect(center=(SCREEN_W // 2, ART_Y + ART_SIZE // 2))
            self.screen.blit(note, nr)

    def _draw_track_info(self):
        song   = self.song
        title  = song.get("title")  or song.get("file", "Unknown").split("/")[-1]
        artist = song.get("artist") or ""
        album  = song.get("album")  or ""

        max_w = SCREEN_W - PAD * 2

        t_surf = self.font_title.render(truncate(title, self.font_title, max_w), True, TEXT_PRI)
        a_surf = self.font_artist.render(truncate(artist, self.font_artist, max_w), True, TEXT_SEC)
        al_surf = self.font_album.render(truncate(album, self.font_album, max_w), True, TEXT_DIM)

        self.screen.blit(t_surf,  (PAD, INFO_Y))
        self.screen.blit(a_surf,  (PAD, INFO_Y + 36))
        self.screen.blit(al_surf, (PAD, INFO_Y + 66))

        # State badge
        state = self.status.get("state", "")
        badge_col = ACCENT if state == "play" else ACCENT_DIM
        badge_txt = {"play": "▶ PLAYING", "pause": "⏸ PAUSED", "stop": "⏹ STOPPED"}.get(state, "")
        if badge_txt:
            b_surf = self.font_time.render(badge_txt, True, badge_col)
            self.screen.blit(b_surf, (PAD, INFO_Y + 100))

    def _draw_progress(self):
        elapsed  = float(self.status.get("elapsed",  0))
        duration = float(self.status.get("duration", 1) or 1)
        ratio    = min(elapsed / duration, 1.0)

        bar_x = PAD
        bar_w = SCREEN_W - PAD * 2
        bar_h = 6
        bar_y = PROGRESS_Y

        # Track
        pygame.draw.rect(self.screen, BAR_BG,
                         (bar_x, bar_y, bar_w, bar_h), border_radius=3)
        # Fill
        fill_w = int(bar_w * ratio)
        if fill_w > 0:
            pygame.draw.rect(self.screen, BAR_FG,
                             (bar_x, bar_y, fill_w, bar_h), border_radius=3)
        # Knob
        knob_x = bar_x + fill_w
        pygame.draw.circle(self.screen, ACCENT, (knob_x, bar_y + bar_h // 2), 8)

        # Times
        el_surf  = self.font_time.render(seconds_to_mmss(elapsed),  True, TEXT_SEC)
        dur_surf = self.font_time.render(seconds_to_mmss(duration), True, TEXT_SEC)
        self.screen.blit(el_surf,  (bar_x, bar_y + 14))
        dur_r = dur_surf.get_rect(right=bar_x + bar_w, top=bar_y + 14)
        self.screen.blit(dur_surf, dur_r)

    def _draw_controls(self):
        cx = SCREEN_W // 2
        spacing = 90

        buttons = [
            ("prev", cx - spacing, "⏮"),
            ("play", cx,           "⏸" if self.status.get("state") == "play" else "▶"),
            ("next", cx + spacing, "⏭"),
        ]

        rects = {}
        for name, bx, label in buttons:
            r = BTN_RADIUS if name == "play" else BTN_RADIUS - 6
            col = BG_CARD
            rect = pygame.Rect(bx - r, BTN_Y - r, r * 2, r * 2)
            draw_rounded_rect(self.screen, col, rect, radius=r)
            pygame.draw.rect(self.screen, ACCENT_DIM, rect,
                             width=2, border_radius=r)
            lbl = self.font_btn.render(label, True, TEXT_PRI)
            lr  = lbl.get_rect(center=(bx, BTN_Y))
            self.screen.blit(lbl, lr)
            rects[name] = rect

        self.btn_prev = rects["prev"]
        self.btn_play = rects["play"]
        self.btn_next = rects["next"]

    def _draw_volume(self):
        vol = int(self.status.get("volume", 0) or 0)

        vol_lbl = self.font_vol.render(f"Vol  {vol}%", True, TEXT_SEC)
        self.screen.blit(vol_lbl, (SCREEN_W // 2 - vol_lbl.get_width() // 2, VOL_Y - 24))

        bar_x = PAD + 50
        bar_w = SCREEN_W - PAD * 2 - 100
        bar_h = 5
        bar_y = VOL_Y

        pygame.draw.rect(self.screen, BAR_BG,
                         (bar_x, bar_y, bar_w, bar_h), border_radius=3)
        fill_w = int(bar_w * vol / 100)
        if fill_w > 0:
            pygame.draw.rect(self.screen, ACCENT_DIM,
                             (bar_x, bar_y, fill_w, bar_h), border_radius=3)

        r = 22
        # Vol down
        vd = pygame.Rect(PAD, bar_y - r, r * 2, r * 2)
        draw_rounded_rect(self.screen, BG_CARD, vd, radius=r)
        vd_l = self.font_vol.render("−", True, TEXT_SEC)
        self.screen.blit(vd_l, vd_l.get_rect(center=vd.center))
        self.btn_vol_down = vd

        # Vol up
        vu = pygame.Rect(SCREEN_W - PAD - r * 2, bar_y - r, r * 2, r * 2)
        draw_rounded_rect(self.screen, BG_CARD, vu, radius=r)
        vu_l = self.font_vol.render("+", True, TEXT_SEC)
        self.screen.blit(vu_l, vu_l.get_rect(center=vu.center))
        self.btn_vol_up = vu

    def _draw_no_connection(self):
        msg1 = self.font_sub.render("Connecting to moode.local…", True, TEXT_DIM)
        msg2 = self.font_time.render(MPD_HOST, True, ACCENT_DIM)
        self.screen.blit(msg1, msg1.get_rect(center=(SCREEN_W // 2, SCREEN_H // 2)))
        self.screen.blit(msg2, msg2.get_rect(center=(SCREEN_W // 2, SCREEN_H // 2 + 36)))

    # ── Dimming ───────────────────────────────────────────────────────────────

    def _apply_dim(self):
        idle = time.time() - self.last_touch
        if idle > DIM_AFTER and not self.dimmed:
            self.dimmed = True
        elif idle <= DIM_AFTER and self.dimmed:
            self.dimmed = False

        if self.dimmed:
            dim = pygame.Surface((SCREEN_W, SCREEN_H), pygame.SRCALPHA)
            alpha = 255 - DIM_BRIGHTNESS
            dim.fill((0, 0, 0, alpha))
            self.screen.blit(dim, (0, 0))

    # ── Touch handling ────────────────────────────────────────────────────────

    def _handle_touch(self, pos):
        self.last_touch = time.time()

        if self.dimmed:
            self.dimmed = False
            return  # First touch just wakes

        if self.btn_play and self.btn_play.collidepoint(pos):
            state = self.status.get("state", "")
            if state == "play":
                self._mpd_cmd("pause", 1)
            else:
                self._mpd_cmd("play")

        elif self.btn_prev and self.btn_prev.collidepoint(pos):
            self._mpd_cmd("previous")

        elif self.btn_next and self.btn_next.collidepoint(pos):
            self._mpd_cmd("next")

        elif self.btn_vol_down and self.btn_vol_down.collidepoint(pos):
            vol = int(self.status.get("volume", 50) or 50)
            self._mpd_cmd("setvol", max(0, vol - 5))

        elif self.btn_vol_up and self.btn_vol_up.collidepoint(pos):
            vol = int(self.status.get("volume", 50) or 50)
            self._mpd_cmd("setvol", min(100, vol + 5))

    # ── Art update ────────────────────────────────────────────────────────────

    def _maybe_update_art(self):
        uri = self.song.get("file")
        if uri != self.art_uri:
            threading.Thread(target=self._fetch_art, args=(uri,), daemon=True).start()

    # ── Main loop ─────────────────────────────────────────────────────────────

    def run(self):
        running = True
        while running:
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    running = False
                elif event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_ESCAPE:
                        running = False
                elif event.type in (pygame.MOUSEBUTTONDOWN, pygame.FINGERDOWN):
                    if event.type == pygame.FINGERDOWN:
                        pos = (int(event.x * SCREEN_W), int(event.y * SCREEN_H))
                    else:
                        pos = event.pos
                    self._handle_touch(pos)

            with self.lock:
                connected = self.connected
                status    = dict(self.status)
                song      = dict(self.song)

            self.status = status
            self.song   = song

            self._draw_background()

            if connected:
                self._maybe_update_art()
                self._draw_status_bar()
                self._draw_art()
                self._draw_track_info()
                self._draw_progress()
                self._draw_controls()
                self._draw_volume()
            else:
                self._draw_no_connection()

            self._apply_dim()
            pygame.display.flip()
            self.clock.tick(FPS)

        pygame.quit()


if __name__ == "__main__":
    app = MoodeDisplay()
    app.run()
