"""Shared HyperPixel helpers for Pi3 Touch apps: touch input and framebuffer drawing.

Standard library only, so nothing needs installing on the Pi.

Drawing goes straight into /dev/fb0 rather than through DRM/KMS: whatever app owns the
HDMI output (mpv) holds the DRM device, and the HyperPixel (DPI) keeps scanning out the
top-left corner of the framebuffer, so we can still paint the 480x800 panel there.
"""
import fcntl
import gzip
import os
import select
import struct
import time

PW, PH = 480, 800                      # HyperPixel 4.0 rectangular, portrait
FB = "/dev/fb0"
FBSYS = "/sys/class/graphics/fb0/"

EV_KEY, EV_ABS = 0x01, 0x03
BTN_TOUCH = 0x14A
ABS_X, ABS_Y, ABS_MT_X, ABS_MT_Y = 0x00, 0x01, 0x35, 0x36
EVENT = struct.Struct("llHHi")         # struct input_event (16 bytes on 32-bit Pi OS)


def log(*a):
    print(*a, flush=True)


def fbcon_off():
    """Stop the text console drawing over our panels (needs root)."""
    for v in sorted(os.listdir("/sys/class/vtconsole")):
        path = f"/sys/class/vtconsole/{v}/"
        try:
            with open(path + "name") as f:
                if "frame buffer" in f.read():
                    with open(path + "bind", "w") as b:
                        b.write("0")
        except OSError:
            pass


# ── Touch ─────────────────────────────────────────────────────────────────────

def find_touch():
    """Return (/dev/input/eventN, name) for the first touchscreen-looking device."""
    with open("/proc/bus/input/devices") as f:
        blocks = f.read().split("\n\n")
    for b in blocks:
        lines = b.splitlines()
        name = next((l.split("=", 1)[1].strip('"') for l in lines if l.startswith("N: ")), "")
        handlers = next((l for l in lines if l.startswith("H: ")), "")
        ev = next((h for h in handlers.split() if h.startswith("event")), None)
        if ev and any(k in name.lower() for k in ("touch", "goodix", "ft5", "edt")):
            return "/dev/input/" + ev, name
    return None, None


def absrange(fd, code):
    """(min, max) of an absolute axis via EVIOCGABS."""
    req = 0x80184540 + code            # _IOR('E', 0x40 + code, struct input_absinfo)
    _, lo, hi, _, _, _ = struct.unpack("6i", fcntl.ioctl(fd, req, bytes(24)))
    return lo, hi


class Touch:
    """Taps on the HyperPixel as portrait-normalised (x, y) in 0..1 plus how long they were held.

    The Goodix controller advertises landscape ranges (x 0-799, y 0-479), but its raw x runs
    left-to-right and its raw y top-to-bottom on the portrait panel - so normalising each axis
    by its own range gives portrait coordinates directly.
    """

    def __init__(self, wait=True):
        while True:
            dev, name = find_touch()
            if dev or not wait:
                break
            log("waiting for touchscreen...")
            time.sleep(5)
        if not dev:
            raise OSError("no touchscreen found")
        self.fd = os.open(dev, os.O_RDONLY)
        try:
            self.xr, self.yr = absrange(self.fd, ABS_MT_X), absrange(self.fd, ABS_MT_Y)
            if self.xr[1] <= 0 or self.yr[1] <= 0:
                raise OSError
        except OSError:
            self.xr, self.yr = absrange(self.fd, ABS_X), absrange(self.fd, ABS_Y)
        self.name, self.dev = name, dev
        self._x = self._y = 0
        self._down = None
        log(f"touch: {name} ({dev}) x{self.xr} y{self.yr}")

    def norm(self, x, y):
        (xlo, xhi), (ylo, yhi) = self.xr, self.yr
        return (x - xlo) / max(1, xhi - xlo), (y - ylo) / max(1, yhi - ylo)

    def taps(self, timeout):
        """Wait up to `timeout` s; return a list of (nx, ny, held_seconds) for fingers lifted."""
        out = []
        r, _, _ = select.select([self.fd], [], [], timeout)
        if not r:
            return out
        data = os.read(self.fd, EVENT.size * 64)
        for i in range(0, len(data) - EVENT.size + 1, EVENT.size):
            _, _, typ, code, val = EVENT.unpack_from(data, i)
            if typ == EV_ABS and code in (ABS_X, ABS_MT_X):
                self._x = val
            elif typ == EV_ABS and code in (ABS_Y, ABS_MT_Y):
                self._y = val
            elif typ == EV_KEY and code == BTN_TOUCH:
                if val == 1:
                    self._down = time.time()
                elif val == 0:
                    held = time.time() - self._down if self._down else 0.0
                    self._down = None
                    out.append((*self.norm(self._x, self._y), held))
        return out


# ── Framebuffer ───────────────────────────────────────────────────────────────

def fb_info():
    def rd(n):
        with open(FBSYS + n) as f:
            return f.read().strip()
    vw, vh = (int(v) for v in rd("virtual_size").split(","))
    return int(rd("bits_per_pixel")), int(rd("stride")), vw, vh


def convert(rgb, bpp):
    """RGB24 bytes -> framebuffer pixels (XRGB8888 little-endian or RGB565)."""
    n = len(rgb) // 3
    if bpp == 32:
        out = bytearray(n * 4)
        out[0::4], out[1::4], out[2::4] = rgb[2::3], rgb[1::3], rgb[0::3]
        out[3::4] = b"\xff" * n
        return bytes(out)
    if bpp == 16:
        out = bytearray(n * 2)
        for i in range(n):
            r, g, b = rgb[3 * i], rgb[3 * i + 1], rgb[3 * i + 2]
            v = ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)
            out[2 * i], out[2 * i + 1] = v & 0xFF, v >> 8
        return bytes(out)
    raise ValueError(f"unsupported framebuffer depth {bpp}")


def load_rgb(path):
    """Read a gzipped raw RGB24 image produced by the art scripts."""
    with gzip.open(path) as g:
        return g.read()


class Screen:
    """Paints pre-rendered images and solid boxes onto the HyperPixel's corner of fb0."""

    def __init__(self):
        self.bpp, self.stride, self.vw, self.vh = fb_info()
        self.bytespp = self.bpp // 8
        self._cache = {}
        log(f"framebuffer: {self.bpp} bpp, stride {self.stride}, {self.vw}x{self.vh}")

    def image(self, path):
        """Converted pixels for an image file, cached."""
        if path not in self._cache:
            self._cache[path] = convert(load_rgb(path), self.bpp)
        return self._cache[path]

    def blit(self, path, x=0, y=0, w=PW, h=PH):
        """Draw a w x h image at (x, y)."""
        px = self.image(path)
        row = w * self.bytespp
        with open(FB, "r+b", buffering=0) as fb:
            for r in range(min(h, self.vh - y)):
                fb.seek((y + r) * self.stride + x * self.bytespp)
                fb.write(px[r * row: (r + 1) * row])

    def fill(self, x, y, w, h, rgb):
        """Draw a solid rectangle."""
        if w <= 0 or h <= 0:
            return
        line = convert(bytes(rgb) * w, self.bpp)
        with open(FB, "r+b", buffering=0) as fb:
            for r in range(min(h, self.vh - y)):
                fb.seek((y + r) * self.stride + x * self.bytespp)
                fb.write(line)
