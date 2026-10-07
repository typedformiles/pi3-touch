"""A hands-free tour of the Pi3 Touch apps, for filming. Runs ON the Pi (mac/demo-tour.sh sends it).

Taps and swipes are written straight into the touchscreen's input device, so the menu and
the apps see them exactly like a finger (and the 180-degree mounting is honoured). The tour
only navigates - in Moode Remote it opens menus and tabs but never plays or changes music.

    python3 demo_tour.py [delay]        # the whole tour, starting after `delay` seconds
    python3 demo_tour.py tap X Y        # one tap at upright screen coordinates (480x800)
    python3 demo_tour.py home           # back to the menu
"""
import os
import struct
import sys
import time

sys.path.insert(0, "/opt/pi3-touch/common")
import pitouch  # noqa: E402

W, H = pitouch.PW, pitouch.PH
EV_SYN, EV_KEY, EV_ABS = 0x00, 0x01, 0x03
ABS_X, ABS_Y = 0x00, 0x01
ABS_MT_SLOT, ABS_MT_X, ABS_MT_Y, ABS_MT_TRACKING_ID = 0x2F, 0x35, 0x36, 0x39
BTN_TOUCH = 0x14A
HOME_REQUEST = "/run/pi3-touch/request-home"


class Finger:
    """A pretend finger on the real touchscreen."""

    def __init__(self):
        dev, name = pitouch.find_touch()
        if not dev:
            raise SystemExit("no touchscreen found")
        self.fd = os.open(dev, os.O_RDWR)
        self.xr = pitouch.absrange(self.fd, ABS_MT_X)
        self.yr = pitouch.absrange(self.fd, ABS_MT_Y)
        self.tid = 4000
        print(f"finger on {name} ({dev}) x{self.xr} y{self.yr}, rotation {pitouch.rotation()}", flush=True)

    def raw(self, x, y):
        """Upright screen pixel -> the panel's raw coordinates (the inverse of pitouch.Touch.norm)."""
        nx, ny = x / W, y / H
        if pitouch.rotation() == 180:
            nx, ny = 1 - nx, 1 - ny
        (xlo, xhi), (ylo, yhi) = self.xr, self.yr
        return round(xlo + nx * (xhi - xlo)), round(ylo + ny * (yhi - ylo))

    def send(self, *events):
        os.write(self.fd, b"".join(pitouch.EVENT.pack(0, 0, t, c, v) for t, c, v in events + ((EV_SYN, 0, 0),)))

    def at(self, x, y):
        rx, ry = self.raw(x, y)
        return (EV_ABS, ABS_MT_X, rx), (EV_ABS, ABS_MT_Y, ry), (EV_ABS, ABS_X, rx), (EV_ABS, ABS_Y, ry)

    def down(self, x, y):
        self.tid += 1
        self.send((EV_ABS, ABS_MT_SLOT, 0), (EV_ABS, ABS_MT_TRACKING_ID, self.tid), *self.at(x, y)[:2],
                  (EV_KEY, BTN_TOUCH, 1), *self.at(x, y)[2:])

    def move(self, x, y):
        self.send((EV_ABS, ABS_MT_SLOT, 0), *self.at(x, y))

    def up(self):
        self.send((EV_ABS, ABS_MT_SLOT, 0), (EV_ABS, ABS_MT_TRACKING_ID, -1), (EV_KEY, BTN_TOUCH, 0))

    def tap(self, x, y):
        self.down(x, y)
        time.sleep(0.08)
        self.up()

    def swipe(self, x0, y0, x1, y1, secs=0.3, steps=12):
        self.down(x0, y0)
        for i in range(1, steps + 1):
            time.sleep(secs / steps)
            self.move(x0 + (x1 - x0) * i / steps, y0 + (y1 - y0) * i / steps)
        self.up()


def home():
    open(HOME_REQUEST, "w").close()


# Where things are on the upright 480x800 screen (from each app's layout constants)
MENU_TITLE = (240, 100)                                       # tap: cancel the countdown, stay
TILE = {"booth": (127, 261), "moode": (352, 261), "weather": (127, 501), "clock": (352, 501)}
SWIPE_LEFT = (420, 450, 80, 450)                              # -> next page/tab
WEATHER = {"maidenhead": (88, 83), "hunstanton": (240, 83), "herne": (392, 83),
           "tab4": [(60, 768), (180, 768), (300, 768), (420, 768)],     # Now Wind Week Tides
           "tide_next": (436, 314), "home": (382, 27)}
CLOCK = {"london": (120, 409), "tokyo": (120, 654), "dubai": (360, 409), "sydney": (360, 654), "back": (60, 32),
         "tabs": [(60, 768), (180, 768), (300, 768), (420, 768)],       # Now Weather News Money
         "home": (408, 31)}
MOODE = {"tabs": [(80, 768), (240, 768), (400, 768)],                   # Now Up Next 4U
         "bliss": (182, 662), "close": (240, 100), "row2": (240, 219), "page_down": (348, 78),
         "who": (332, 27), "close_picker": (240, 650), "home": (382, 27)}


def tour(f):
    """About 85 seconds: one look at each screen, nothing lingered on."""
    t0 = time.time()

    def step(label, action, hold):
        print(f"{time.time() - t0:5.1f}s  {label}", flush=True)
        action()
        time.sleep(hold)

    tap, swipe = f.tap, f.swipe
    step("menu", home, 2)
    step("menu: stay", lambda: tap(*MENU_TITLE), 2)

    # Weather: one of each page - Now, Wind, Week, then tides at the coast
    step("Weather: Now", lambda: tap(*TILE["weather"]), 5)
    step("Weather: Wind", lambda: swipe(*SWIPE_LEFT), 3.5)
    step("Weather: Week", lambda: swipe(*SWIPE_LEFT), 3)
    step("Weather: Hunstanton", lambda: tap(*WEATHER["hunstanton"]), 0.8)
    step("Weather: Hunstanton tides", lambda: tap(*WEATHER["tab4"][3]), 4.5)
    step("menu", lambda: tap(*WEATHER["home"]), 2)
    step("menu: stay", lambda: tap(*MENU_TITLE), 1)

    # World Clock: the clocks, London's pages, Sydney's exchange rate
    step("World Clock", lambda: tap(*TILE["clock"]), 5.5)
    step("London: Now", lambda: tap(*CLOCK["london"]), 4)
    step("London: Weather", lambda: tap(*CLOCK["tabs"][1]), 3.5)
    step("London: News", lambda: tap(*CLOCK["tabs"][2]), 3.5)
    step("back to the clocks", lambda: tap(*CLOCK["back"]), 1.5)
    step("Sydney", lambda: tap(*CLOCK["sydney"]), 1)
    step("Sydney: Money", lambda: tap(*CLOCK["tabs"][3]), 4.5)
    step("menu", lambda: tap(*CLOCK["home"]), 2)
    step("menu: stay", lambda: tap(*MENU_TITLE), 1)

    # Moode Remote: Now, Bliss, Up Next and a track's menu, 4U, who's listening
    step("Moode Remote: Now", lambda: tap(*TILE["moode"]), 5)
    step("Moode: Bliss menu", lambda: tap(*MOODE["bliss"]), 3)
    step("Moode: close", lambda: tap(*MOODE["close"]), 1)
    step("Moode: Up Next", lambda: tap(*MOODE["tabs"][1]), 3.5)
    step("Moode: a track's menu", lambda: tap(*MOODE["row2"]), 2.5)
    step("Moode: close", lambda: tap(*MOODE["close"]), 1)
    step("Moode: 4U", lambda: tap(*MOODE["tabs"][2]), 4)
    step("Moode: who's listening", lambda: tap(*MOODE["who"]), 2.5)
    step("Moode: close", lambda: tap(*MOODE["close_picker"]), 1)
    step("Moode: Now (the end)", lambda: tap(*MOODE["tabs"][0]), 3)
    print(f"tour done in {time.time() - t0:.0f}s", flush=True)


if __name__ == "__main__":
    args = sys.argv[1:]
    if args[:1] == ["tap"]:
        Finger().tap(int(args[1]), int(args[2]))
    elif args[:1] == ["home"]:
        home()
    else:
        delay = int(args[0]) if args else 10
        finger = Finger()
        for i in range(delay, 0, -1):
            print(f"starting in {i}…", flush=True)
            time.sleep(1)
        tour(finger)
