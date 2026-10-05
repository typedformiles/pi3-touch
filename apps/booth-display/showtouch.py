#!/usr/bin/env python3
"""HyperPixel touch panel for the booth display.

Top strip (hold 1.5 s) = back to the Pi3 Touch menu. Below it, three bands:
previous slide / pause-resume / next slide. Commands go to mpv (showloop) over its
IPC socket; the panel is drawn straight into the framebuffer (see common/pitouch.py).
"""
import json
import os
import socket
import sys
import time

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "common"))
import pitouch  # noqa: E402

RUN = "/run/pi3-touch"
SOCK = f"{RUN}/booth-mpv.sock"
STATE = f"{RUN}/booth-state"
HOME_REQUEST = f"{RUN}/request-home"
PANEL = os.path.join(HERE, "art", "panel-{}.rgb.gz")

HOME_STRIP = 80 / pitouch.PH         # top 80 px
HOME_HOLD = 1.5                       # s - so a visitor's tap can't exit the slideshow
REDRAW_EVERY = 10                     # s - repaint in case anything scribbles on it
DEBOUNCE = 0.35


def zone(ny):
    """Which control a tap at portrait height ny (0 = top, 1 = bottom) hit."""
    if ny < HOME_STRIP:
        return "home"
    p = (ny - HOME_STRIP) / (1 - HOME_STRIP)
    return "prev" if p < 1 / 3 else "next" if p > 2 / 3 else "toggle"


def send(cmd):
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(2)
        s.connect(SOCK)
        s.sendall((json.dumps({"command": ["script-message", "rotate", cmd]}) + "\n").encode())
        s.recv(4096)                  # wait for mpv's reply so it doesn't log a broken pipe
        s.close()
        return True
    except OSError as e:
        pitouch.log("mpv not reachable:", e)
        return False


def request_home():
    try:
        open(HOME_REQUEST, "w").close()
    except OSError as e:
        pitouch.log("can't request menu:", e)


def read_state():
    try:
        with open(STATE) as f:
            return f.read().strip() or "playing"
    except OSError:
        return "playing"


def main():
    touch = pitouch.Touch()
    screen = pitouch.Screen()
    state = read_state()
    screen.blit(PANEL.format(state))
    last_draw = time.time()
    last_action = 0.0

    while True:
        for nx, ny, held in touch.taps(1.0):
            now = time.time()
            if now - last_action < DEBOUNCE:
                continue
            last_action = now
            action = zone(ny)
            pitouch.log(f"tap y={ny:.2f} held={held:.1f}s -> {action}")
            if action == "home":
                if held >= HOME_HOLD:
                    request_home()
            else:
                send(action)
        new = read_state()
        if new != state or time.time() - last_draw > REDRAW_EVERY:
            state = new
            screen.blit(PANEL.format(state))
            last_draw = time.time()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
